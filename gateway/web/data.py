"""只读聚合。页面要的所有数字都从这里出,不写任何东西。

各实例的 state.db 以只读模式打开(mode=ro),即使 web 有 bug 也写不坏 listener 的库。
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from .. import config as configmod, db as dbm
from ..paths import Paths

RESERVED = {'web', 'example', 'web.example'}


class DbBroken(Exception):
    pass


# ---- 实例与库 ----
def list_instances() -> list[str]:
    d = Paths('x').config_dir
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob('*.toml') if p.stem not in RESERVED)


def open_ro(db_file: Path) -> sqlite3.Connection | None:
    """只读打开。库不存在返回 None,打不开抛 DbBroken。"""
    if not Path(db_file).exists():
        return None
    try:
        conn = sqlite3.connect(f'file:{db_file}?mode=ro', uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute('SELECT 1 FROM sqlite_master LIMIT 1').fetchall()
        return conn
    except sqlite3.Error as e:
        raise DbBroken(str(e)) from e


# ---- systemd ----
def run_systemctl(args: list[str], timeout: int = 10) -> tuple[int, str]:
    try:
        p = subprocess.run(['systemctl', '--user', *args], capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or '').strip()
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return -1, str(e)


def unit_state(unit: str) -> str:
    rc, out = run_systemctl(['is-active', unit])
    return out or ('unknown' if rc == -1 else 'inactive')


def _show(unit: str, props: list[str]) -> dict[str, str]:
    rc, out = run_systemctl(['show', unit, *[f'-p{p}' for p in props]])
    d = {}
    for line in out.splitlines():
        if '=' in line:
            k, v = line.split('=', 1)
            d[k] = v
    return d


def dispatcher_last(inst: str) -> dict:
    """dispatcher 上一轮的退出码。非 0 通常就是探活失败(claude 连不上)。

    不读 alerts 表判探活:告警 1 小时内去重,而且恢复之后不会自己消掉,会一直红。
    """
    d = _show(f'yizhan-dispatcher@{inst}.service', ['ExecMainStatus', 'ExecMainExitTimestamp', 'Result'])
    rc = d.get('ExecMainStatus')
    return {'rc': int(rc) if rc and rc.isdigit() else None,
            'at': d.get('ExecMainExitTimestamp') or None,
            'result': d.get('Result') or None}


def timer_next(inst: str) -> str | None:
    d = _show(f'yizhan-dispatcher@{inst}.timer', ['NextElapseUSecRealtime'])
    us = d.get('NextElapseUSecRealtime')
    if not us or not us.isdigit() or us == '0':
        return None
    return datetime.fromtimestamp(int(us) / 1e6).strftime('%H:%M:%S')


# ---- 健康 ----
def health(f: dict) -> tuple[str, str]:
    """输入一组事实,输出 (颜色, 原因)。纯函数,好测。"""
    if f.get('db_state') == 'missing':
        return 'grey', '还没启用过(没有 state.db)'
    if f.get('db_state') == 'broken':
        return 'red', 'state.db 打不开'
    if f.get('listener_active') != 'active':
        return 'red', f"listener 没在跑({f.get('listener_active')})"
    ago = f.get('heartbeat_ago_s')
    if ago is not None and ago > 600:
        return 'red', f'心跳停了 {int(ago / 60)} 分钟'
    if f.get('stuck'):
        return 'red', f"有 {f['stuck']} 张卡卡死"
    rc = f.get('dispatcher_rc')
    if rc not in (None, 0):
        return 'red', f'dispatcher 上轮退出码 {rc}(多半是 claude 探活失败)'
    if f.get('paused'):
        return 'yellow', '已暂停'
    oldest = f.get('oldest_open_min')
    if oldest and oldest > 30:
        return 'yellow', f'有卡等了 {int(oldest)} 分钟还没处理'
    return 'green', '正常'


# ---- 时间 ----
def _today_start_ms() -> int:
    return int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)


def _facts(conn, cfg, now: int) -> dict:
    stuck = conn.execute("SELECT COUNT(*) FROM issues WHERE state='stuck'").fetchone()[0]
    oldest = conn.execute("SELECT MIN(created_at) FROM issues WHERE state='open'").fetchone()[0]
    hb = dbm.heartbeat_get(conn)
    return {'stuck': stuck,
            'oldest_open_min': (now - int(oldest)) / 60000 if oldest else None,
            'heartbeat_ago_s': (now - hb) / 1000 if hb else None}


def instance_summary(inst: str) -> dict:
    """卡片墙上一张卡的全部数据。任何一项出错都不能影响别的实例。"""
    row: dict = {'inst': inst}
    try:
        cfg = configmod.load(inst)
    except configmod.ConfigError as e:
        return {**row, 'health': 'grey', 'health_reason': f'配置有问题: {e}', 'db_state': 'missing'}
    row['project_dir'] = cfg.project_dir
    row['paused'] = cfg.paths.paused_file.exists()
    try:
        conn = open_ro(cfg.paths.db_file)
        db_state = 'ok' if conn else 'missing'
    except DbBroken:
        conn, db_state = None, 'broken'
    row['db_state'] = db_state

    listener = unit_state(f'yizhan-listener@{inst}')
    last = dispatcher_last(inst)
    row['units'] = {'listener': listener, 'timer': unit_state(f'yizhan-dispatcher@{inst}.timer'),
                    'dispatcher_last': last}

    now = dbm.now_ms()
    if conn is None:
        row.update(heartbeat_ago_s=None, today_msgs=0, today_cards=0, today_replies=0,
                   today_cost_usd=0.0, pending=0, context_tokens=0, last_alert=None)
        row['health'], row['health_reason'] = health({**row, 'listener_active': listener,
                                                      'dispatcher_rc': last['rc']})
        return row

    t0 = _today_start_ms()
    q = lambda sql, *a: conn.execute(sql, a).fetchone()[0]
    row['today_msgs'] = q('SELECT COUNT(*) FROM events WHERE create_time>=?', t0)
    row['today_cards'] = q('SELECT COUNT(*) FROM issues WHERE created_at>=?', t0)
    row['today_replies'] = q('SELECT COUNT(*) FROM replies WHERE sent_at>=?', t0)
    row['today_cost_usd'] = round(q('SELECT COALESCE(SUM(cost_usd),0) FROM rounds WHERE started_at>=?', t0), 4)
    row['pending'] = q("SELECT COUNT(*) FROM issues WHERE state IN ('open','answering','waiting')")
    last_round = conn.execute('SELECT context_tokens FROM rounds ORDER BY started_at DESC LIMIT 1').fetchone()
    row['context_tokens'] = int(last_round[0] or 0) if last_round else 0
    a = conn.execute('SELECT kind,sent_at FROM alerts ORDER BY sent_at DESC LIMIT 1').fetchone()
    row['last_alert'] = {'kind': a['kind'], 'at': a['sent_at']} if a else None
    facts = _facts(conn, cfg, now)
    row.update(facts)
    row['health'], row['health_reason'] = health({**row, **facts, 'listener_active': listener,
                                                  'dispatcher_rc': last['rc']})
    conn.close()
    return row


def instances() -> list[dict]:
    out = []
    for inst in list_instances():
        try:
            out.append(instance_summary(inst))
        except Exception as e:                      # 一个实例炸了不能拖垮整页
            out.append({'inst': inst, 'health': 'red', 'health_reason': f'读取失败: {e}', 'db_state': 'broken'})
    return out


# ---- 详情 ----
def _conn_or_raise(inst: str):
    cfg = configmod.load(inst)
    conn = open_ro(cfg.paths.db_file)
    if conn is None:
        raise DbBroken(f'{inst} 还没有 state.db')
    return cfg, conn


def overview(inst: str) -> dict:
    cfg, conn = _conn_or_raise(inst)
    t0 = _today_start_ms()
    hourly = []
    for h in range(24):
        a = t0 + h * 3600_000
        b = a + 3600_000
        if a > dbm.now_ms():
            break
        hourly.append({
            'hour': f'{h:02d}',
            'msgs': conn.execute('SELECT COUNT(*) FROM events WHERE create_time>=? AND create_time<?',
                                 (a, b)).fetchone()[0],
            'cards': conn.execute('SELECT COUNT(*) FROM issues WHERE created_at>=? AND created_at<?',
                                  (a, b)).fetchone()[0]})
    alerts = [dict(r) for r in conn.execute('SELECT kind,sent_at FROM alerts ORDER BY sent_at DESC LIMIT 10')]
    states = dict(conn.execute('SELECT state,COUNT(*) FROM issues GROUP BY state').fetchall())
    summary = instance_summary(inst)
    conn.close()
    return {'inst': inst, 'hourly': hourly, 'alerts': alerts, 'card_states': states,
            'summary': summary, 'timer_next': timer_next(inst),
            'quiet_seconds': cfg.quiet_seconds, 'skill': cfg.skill}


def _sender_name(conn, open_id: str | None) -> str:
    if not open_id:
        return '?'
    return dbm.member_name(conn, open_id) or open_id


def cards(inst: str, state: str | None = None, page: int = 1, size: int = 20) -> dict:
    cfg, conn = _conn_or_raise(inst)
    where, args = '', []
    if state:
        where, args = 'WHERE state=?', [state]
    total = conn.execute(f'SELECT COUNT(*) FROM issues {where}', args).fetchone()[0]
    rows = conn.execute(f'SELECT * FROM issues {where} ORDER BY created_at DESC LIMIT ? OFFSET ?',
                        (*args, size, (page - 1) * size)).fetchall()
    items = []
    for r in rows:
        n = conn.execute('SELECT COUNT(*) FROM issue_messages WHERE issue_id=?', (r['issue_id'],)).fetchone()[0]
        first = conn.execute('SELECT MIN(sent_at) FROM replies WHERE issue_id=?', (r['issue_id'],)).fetchone()[0]
        items.append({'id': r['issue_id'], 'state': r['state'], 'sender': r['sender_id'],
                      'sender_name': _sender_name(conn, r['sender_id']), 'n_msgs': n,
                      'created_at': r['created_at'], 'chat_id': r['chat_id'],
                      'thread_id': r['thread_id'], 'redispatch': r['redispatch'], 'note': r['note'],
                      'first_reply_s': round((first - r['created_at']) / 1000) if first else None})
    conn.close()
    return {'total': total, 'page': page, 'size': size, 'items': items}


def card(inst: str, issue_id: int) -> dict:
    cfg, conn = _conn_or_raise(inst)
    it = conn.execute('SELECT * FROM issues WHERE issue_id=?', (issue_id,)).fetchone()
    if it is None:
        conn.close()
        raise KeyError(f'没有卡 #{issue_id}')
    msgs = []
    for m in conn.execute('SELECT e.* FROM issue_messages im JOIN events e ON e.message_id=im.message_id '
                          'WHERE im.issue_id=? ORDER BY e.create_time', (issue_id,)):
        msgs.append({'mid': m['message_id'], 'at': m['create_time'], 'text': m['text'],
                     'sender_name': _sender_name(conn, m['sender_id']),
                     'images': [f"/api/i/{inst}/attach/{k}" for k in json.loads(m['image_keys'] or '[]')]})
    reps = [dict(r) for r in conn.execute('SELECT * FROM replies WHERE issue_id=? ORDER BY sent_at', (issue_id,))]
    rnds = [dict(r) for r in conn.execute('SELECT * FROM rounds WHERE issue_id=? ORDER BY started_at', (issue_id,))]
    conn.close()
    return {'card': dict(it), 'messages': msgs, 'replies': reps, 'rounds': rnds}


def session(inst: str) -> dict:
    cfg, conn = _conn_or_raise(inst)
    s = dbm.session_get(conn)
    rnds = [dict(r) for r in dbm.rounds(conn)]
    hist = [dict(r) for r in dbm.session_history(conn)]
    total = conn.execute('SELECT COALESCE(SUM(cost_usd),0) FROM rounds').fetchone()[0]
    conn.close()
    cur = dict(s) if s else None
    if cur:
        cur.pop('usage', None)
    return {'session': cur, 'rounds': rnds, 'rotations': hist,
            'total_cost_usd': round(total, 4),
            'context_tokens': rnds[-1]['context_tokens'] if rnds else 0}


def attachment_path(inst: str, key: str) -> Path:
    """附件 key → 本地文件路径。key 来自 URL,必须当成不可信输入清洗。"""
    cfg = configmod.load(inst)
    safe = ''.join(c for c in key if c.isalnum() or c in '-_.')
    while '..' in safe:
        safe = safe.replace('..', '.')
    safe = safe.lstrip('.')
    if not safe:
        raise ValueError(f'附件名非法: {key!r}')
    p = (cfg.paths.attachments / safe).resolve()
    if not str(p).startswith(str(cfg.paths.attachments.resolve())):
        raise ValueError(f'附件名越界: {key!r}')
    return p
