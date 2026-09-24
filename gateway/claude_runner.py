"""唤起 claude -p。

三条借鉴(MidnightV1):总是先 --resume,失败再起新会话;stream-json 按空闲时长判超时,杀整个进程组;
spawn 前剥 CLAUDECODE 等环境变量(否则从 Claude 会话内启动会报 nested session)。
"""
from __future__ import annotations

import collections
import json
import logging
import os
import queue
import threading
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger('yizhan.runner')

STRIP_ENV = ('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SSE_PORT', 'CLAUDE_CODE_SESSION_ID',
             'ANTHROPIC_API_KEY', 'CLAUDE_CONFIG_DIR')
RESUME_FAIL_MARKS = ('No conversation found', 'no conversation found', 'Could not find session',
                     'session not found', 'Invalid session ID')


@dataclass
class RunResult:
    rc: int
    result: dict | None = None
    usage: dict | None = None
    stderr: str = ''
    idle_killed: bool = False
    hard_killed: bool = False
    resume_failed: bool = False
    context_tokens: int = 0
    lines: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.rc == 0 and not self.idle_killed and not self.hard_killed


def build_env(cfg, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
    env['TZ'] = 'Asia/Shanghai'
    env['YIZHAN_INST'] = cfg.inst
    env.setdefault('HOME', os.path.expanduser('~'))
    env['PATH'] = os.path.dirname(cfg.claude_bin) + os.pathsep + env.get('PATH', '/usr/bin:/bin')
    env.update(getattr(cfg, 'claude_env', None) or {})
    if extra:
        env.update(extra)
    return env


def build_argv(cfg, prompt: str, session_id: str | None, resume: bool) -> list[str]:
    argv = [cfg.claude_bin, '-p', prompt, '--output-format', 'stream-json', '--verbose',
            '--add-dir', cfg.project_dir]
    for d in getattr(cfg, 'extra_dirs', None) or []:
        argv += ['--add-dir', os.path.expanduser(d)]
    if session_id:
        argv += (['--resume', session_id] if resume else ['--session-id', session_id])
    argv += list(cfg.permission_args)
    return argv


def run_claude(cfg, workspace: Path, prompt: str, session_id: str | None, *, resume: bool = True,
               log_file: Path | None = None) -> RunResult:
    argv = build_argv(cfg, prompt, session_id, resume)
    idle_limit = cfg.idle_timeout_min * 60
    hard_limit = cfg.hard_timeout_min * 60
    log.info('spawn: %s', ' '.join(argv[:6]) + ' …')
    proc = subprocess.Popen(argv, cwd=str(workspace), env=build_env(cfg), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, bufsize=1, start_new_session=True)
    res = RunResult(rc=-1)
    start = last = time.time()
    lf = open(log_file, 'a', encoding='utf-8') if log_file else None
    # 两个读线程,阻塞式逐行读,主循环从队列取。原来是非阻塞 readline 加 poll(),有两个坑
    # (2026-09-23 审查发现):
    #  · 非阻塞 readline 可能只拿到半行,json 解析失败就被丢了,result 行丢了本轮就没有成本;
    #  · readline 刚返回空、进程恰好退出就 break,缓冲里最后几行没读;
    #  · stderr 只在结束后读一次,claude 写满 64KB 管道就卡死,15 分钟后被当成无响应杀掉。
    q: queue.Queue = queue.Queue()
    readers = [threading.Thread(target=_pump, args=(proc.stdout, q, 'out'), daemon=True),
               threading.Thread(target=_pump, args=(proc.stderr, q, 'err'), daemon=True)]
    for t in readers:
        t.start()
    err_tail: collections.deque = collections.deque(maxlen=400)
    open_streams = {'out', 'err'}
    exited_at = None

    def handle_out(line: str):
        if lf:
            lf.write(line if line.endswith('\n') else line + '\n'); lf.flush()
        try:
            obj = json.loads(line)
        except Exception:
            return
        res.lines.append(obj)
        if obj.get('type') == 'result':
            res.result = obj
            res.usage = obj.get('usage')

    try:
        while open_streams:
            try:
                tag, line = q.get(timeout=0.5)
            except queue.Empty:
                now = time.time()
                if proc.poll() is not None:
                    # 进程已退出但流还没关:多半是它拉起的子进程继承了管道。给 3 秒收尾就走,
                    # 不能一直等到空闲超时
                    exited_at = exited_at or now
                    if now - exited_at > 3:
                        break
                    continue
                if now - last > idle_limit:
                    res.idle_killed = True; break
                if now - start > hard_limit:
                    res.hard_killed = True; break
                continue
            if line is None:
                open_streams.discard(tag)
                continue
            if tag == 'err':
                err_tail.append(line)          # stderr 不算「有输出」,空闲只看 stdout
                continue
            last = time.time()
            handle_out(line)
    finally:
        if res.idle_killed or res.hard_killed:
            _kill_group(proc)
        try:
            res.rc = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            res.rc = proc.wait()
        for t in readers:
            t.join(timeout=5)
        while True:                            # 被杀或提前退出后,队列里剩下的也收完
            try:
                tag, line = q.get_nowait()
            except queue.Empty:
                break
            if line is None:
                continue
            (err_tail.append if tag == 'err' else handle_out)(line)
        res.stderr = ''.join(err_tail)[-4000:]
        if lf:
            lf.close()
    res.context_tokens = context_tokens(res.lines)
    blob = res.stderr + json.dumps(res.result or {}, ensure_ascii=False)
    if resume and session_id and any(m in blob for m in RESUME_FAIL_MARKS):
        res.resume_failed = True
    log.info('exit rc=%s idle=%s hard=%s resume_failed=%s context=%d tokens',
             res.rc, res.idle_killed, res.hard_killed, res.resume_failed, res.context_tokens)
    return res


def context_tokens(lines: list[dict]) -> int:
    """会话占了多少上下文窗口。

    取**单条 assistant 消息**的 input + cache_read + cache_creation 的最大值,不是整轮累加:
    result 里的顶层 usage 是一轮内所有请求的累计,同一份上下文被反复计入 cache_read
    (2026-09-21 实测:一条「1」的私聊,顶层累计 51 万,实际窗口只有 4 万出头),
    拿它判轮换阈值会误判。
    """
    best = 0
    for o in lines:
        u = (o.get('message') or {}).get('usage') if isinstance(o.get('message'), dict) else None
        if not u:
            continue
        best = max(best, sum(int(u.get(k) or 0) for k in
                             ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')))
    return best


def context_window(result: dict | None) -> int | None:
    """模型的上下文窗口有多大(result.modelUsage 里每个模型各报一个)。

    取最大的:主模型窗口最大,子代理可能用小模型。页面拿它当上下文水位的分母,
    不写死 1M——模型或窗口一换,分母跟着变。
    """
    wins = [int(m.get('contextWindow') or 0) for m in ((result or {}).get('modelUsage') or {}).values()
            if isinstance(m, dict)]
    return max(wins) if wins and max(wins) > 0 else None


def _pump(stream, q: queue.Queue, tag: str):
    """一个读线程:逐行读到 EOF,每行放进队列,最后放一个 None 表示这条流结束。"""
    try:
        for line in iter(stream.readline, ''):
            q.put((tag, line))
    except (ValueError, OSError):
        pass                                   # 流已被关闭
    finally:
        q.put((tag, None))


def _kill_group(proc):
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        for _ in range(50):
            if proc.poll() is not None:
                return
            time.sleep(0.1)
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except ProcessLookupError:
        pass


def probe(cfg) -> tuple[bool, str]:
    """探活:认证还在不在。失败时不要动任何卡。"""
    try:
        p = subprocess.run([cfg.claude_bin, '-p', '回 ok'], capture_output=True, text=True,
                           timeout=cfg.probe_timeout_sec, env=build_env(cfg), cwd=os.path.expanduser('~'))
    except subprocess.TimeoutExpired:
        return False, f'探活超时 {cfg.probe_timeout_sec}s'
    except FileNotFoundError:
        return False, f'找不到 claude: {cfg.claude_bin}'
    if p.returncode != 0:
        return False, f'rc={p.returncode} {(p.stderr or p.stdout)[-300:]}'
    return True, (p.stdout or '').strip()[:100]
