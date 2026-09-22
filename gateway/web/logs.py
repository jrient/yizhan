"""日志流 → SSE 事件。三路:listener、dispatcher(journald)、claude(stream-json 文件)。"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Iterator

from .. import config as configmod

LEVELS = {0: 'emerg', 1: 'alert', 2: 'crit', 3: 'error', 4: 'warn', 5: 'notice', 6: 'info', 7: 'debug'}
STREAMS = ('listener', 'dispatcher', 'claude')


def unit_for(inst: str, stream: str) -> str:
    return (f'yizhan-listener@{inst}.service' if stream == 'listener'
            else f'yizhan-dispatcher@{inst}.service')


def parse_journal_line(line: str) -> dict | None:
    try:
        o = json.loads(line)
    except ValueError:
        return None
    msg = o.get('MESSAGE')
    if isinstance(msg, list):                 # journald 对二进制 MESSAGE 给的是字节数组
        msg = bytes(msg).decode('utf-8', 'replace')
    if msg is None:
        return None
    us = o.get('__REALTIME_TIMESTAMP')
    pri = o.get('PRIORITY')
    return {'at': int(int(us) / 1000) if us and str(us).isdigit() else int(time.time() * 1000),
            'level': LEVELS.get(int(pri), 'info') if pri and str(pri).isdigit() else 'info',
            'text': msg}


def parse_stream_line(line: str) -> dict | None:
    """claude 的 stream-json:只留看得懂的几类,工具调用压成一行。"""
    try:
        o = json.loads(line)
    except ValueError:
        return None
    now = int(time.time() * 1000)
    t = o.get('type')
    if t == 'assistant':
        parts = []
        for b in (o.get('message') or {}).get('content') or []:
            if not isinstance(b, dict):
                continue
            if b.get('type') == 'text' and b.get('text', '').strip():
                parts.append(b['text'].strip())
            elif b.get('type') == 'tool_use':
                arg = json.dumps(b.get('input') or {}, ensure_ascii=False)
                parts.append(f"· {b.get('name')} {arg[:160]}")
        if not parts:
            return None
        return {'at': now, 'level': 'info', 'text': '\n'.join(parts)}
    if t == 'result':
        u = o.get('usage') or {}
        return {'at': now, 'level': 'error' if o.get('is_error') else 'notice',
                'text': f"[result] {o.get('subtype')} 用时 {int((o.get('duration_ms') or 0) / 1000)}s "
                        f"成本 ${o.get('total_cost_usd') or 0:.4f} 输出 {u.get('output_tokens') or 0} tokens"}
    if t == 'user':
        return {'at': now, 'level': 'debug', 'text': '[工具返回]'}
    return None


def _keep(ev: dict | None, since_ms: int, q: str | None) -> bool:
    if ev is None:
        return False
    if since_ms and ev['at'] < since_ms:
        return False
    return not q or q.lower() in ev['text'].lower()


def tail_journal(unit: str, since_ms: int = 0, q: str | None = None,
                 lines: int = 200, stop: threading.Event | None = None) -> Iterator[dict]:
    argv = ['journalctl', '--user', '-u', unit, '-o', 'json', '-n', str(lines), '-f', '--no-pager']
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
    if stop is not None:
        # 读 stdout 会一直阻塞到下一条日志,生成器自己醒不过来。
        # 让一个看门线程在 stop 置位时 terminate,读就断了,循环才收得掉。
        threading.Thread(target=_terminate_on_stop, args=(p, stop),
                         name='journal-stop', daemon=True).start()
    try:
        for line in p.stdout:
            if stop is not None and stop.is_set():
                break
            ev = parse_journal_line(line)
            if _keep(ev, since_ms, q):
                yield ev
    finally:
        p.terminate()


def _terminate_on_stop(p, stop: threading.Event):
    stop.wait()
    try:
        p.terminate()
    except Exception:
        pass


def tail_file(path: Path, since_ms: int = 0, q: str | None = None, parse=parse_stream_line,
              lines: int = 200, poll: float = 0.5, _once: bool = False,
              stop: threading.Event | None = None) -> Iterator[dict]:
    """先补最后 N 行,再跟着文件追加往下读。文件不存在就等它出现。"""
    path = Path(path)
    if path.exists():
        tail = path.read_text(encoding='utf-8', errors='replace').splitlines()[-lines:]
        for line in tail:
            ev = parse(line)
            if _keep(ev, since_ms, q):
                yield ev
        pos = path.stat().st_size
    else:
        pos = 0
    while not _once:
        if stop is not None:
            if stop.wait(poll):             # 置位就收尾,不用外面 close()
                return
        else:
            time.sleep(poll)
        if not path.exists():
            continue
        size = path.stat().st_size
        if size < pos:                      # 被截断或轮转
            pos = 0
        if size == pos:
            continue
        with open(path, encoding='utf-8', errors='replace') as f:
            f.seek(pos)
            chunk = f.read()
            pos = f.tell()
        for line in chunk.splitlines():
            ev = parse(line)
            if _keep(ev, 0, q):
                yield ev


def journal_available() -> bool:
    try:
        p = subprocess.run(['journalctl', '--user', '-n', '1', '--no-pager'],
                           capture_output=True, timeout=5)
        return p.returncode == 0
    except Exception:
        return False


def stream_events(inst: str, stream: str, since_ms: int = 0, q: str | None = None,
                  stop: threading.Event | None = None) -> Iterator[dict]:
    if stream not in STREAMS:
        raise ValueError(f'不认识的日志流: {stream}')
    cfg = configmod.load(inst)
    if stream == 'claude':
        yield from tail_file(cfg.paths.logs / 'claude-stream.jsonl', since_ms, q, stop=stop)
        return
    if journal_available():
        yield from tail_journal(unit_for(inst, stream), since_ms, q, stop=stop)
        return
    # journald 读不到时的退路:dispatcher 自己也往文件写了一份
    if stream == 'dispatcher':
        yield {'at': int(time.time() * 1000), 'level': 'warn',
               'text': '[读不到 journal,退回读 logs/dispatcher.log]'}
        yield from tail_file(cfg.paths.logs / 'dispatcher.log', since_ms, q, parse=_parse_plain,
                             stop=stop)
    else:
        yield {'at': int(time.time() * 1000), 'level': 'error',
               'text': '[读不到 journal,listener 没有文件日志。用 journalctl --user -u '
                       f'yizhan-listener@{inst} -f 看]'}


def _parse_plain(line: str) -> dict | None:
    line = line.rstrip('\n')
    if not line.strip():
        return None
    lvl = 'error' if ' ERROR ' in line else 'warn' if ' WARNING ' in line else 'info'
    return {'at': int(time.time() * 1000), 'level': lvl, 'text': line}
