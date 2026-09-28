"""每小时给实例的常驻会话发一次 /compact,免得上下文越滚越大。

和 dispatcher 抢同一把 flock:同一 session 不能并行 resume。正在处理卡就等,最多等 WAIT_S 秒,
还拿不到就跳过这一小时。暂停中(人工接管)、还没有会话、会话还没跑过第一轮,都不动。
上下文没到 compact_at_tokens 也不动:小会话压完反而变大(09-24 实测 5k→10k),还丢细节。

claude -p /compact 不管压没压成,result 都报 success;真实结果在 system/status 事件的
compact_result / compact_error 里,压成了还有一条 compact_boundary 带压缩后的 token 数。
"""
from __future__ import annotations

import logging
import sys
import time

from . import claude_runner, config as configmod, db as dbm
from .dispatcher import acquire_lock
from .paths import unescape_inst

log = logging.getLogger('yizhan.compact')

WAIT_S = 30 * 60


def run(cfg, *, wait_s: float = WAIT_S, poll_s: float = 10) -> str:
    if cfg.paths.paused_file.exists():
        return 'paused'
    conn = dbm.connect(cfg.paths.db_file)
    t0 = time.time()
    while (fh := acquire_lock(cfg.paths.lock_file)) is None:
        if time.time() - t0 > wait_s:
            return 'busy'
        time.sleep(poll_s)
    try:
        s = dbm.session_get(conn)
        if s is None or s['fresh']:
            return 'no_session'
        if cfg.compact_at_tokens and (s['ctx_tokens'] or 0) < cfg.compact_at_tokens:
            return 'small'
        res = claude_runner.run_claude(cfg, cfg.paths.workspace, '/compact', s['session_id'], resume=True,
                                       log_file=cfg.paths.logs / 'claude-stream.jsonl')
        if not res.ok or res.resume_failed:
            dbm.record_error(conn, 'compact_failed', f'rc={res.rc} ' + res.stderr[-200:])
            return 'failed'
        status, err, post = compact_outcome(res.lines)
        if status == 'success':
            if post is not None:
                dbm.session_set_ctx(conn, s['session_id'], post)
            return 'ok'
        if err and 'Not enough messages' in err:
            return 'noop'
        dbm.record_error(conn, 'compact_failed', err or '流里没有 compact_result')
        return 'failed'
    finally:
        fh.close()


def compact_outcome(lines: list[dict]) -> tuple[str | None, str | None, int | None]:
    """从 stream 里取 (compact_result, compact_error, 压缩后 token 数)。"""
    status = err = post = None
    for o in lines:
        if o.get('type') != 'system':
            continue
        if o.get('subtype') == 'status' and 'compact_result' in o:
            status, err = o.get('compact_result'), o.get('compact_error')
        elif o.get('subtype') == 'compact_boundary':
            post = (o.get('compact_metadata') or {}).get('post_tokens')
            status = status or 'success'
    return status, err, post


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    cfg = configmod.load(unescape_inst(argv[0]))
    r = run(cfg)
    log.info('compact %s → %s', cfg.inst, r)
    return 1 if r == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
