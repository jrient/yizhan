"""只读聚合。页面要的所有数字都从这里出,不写任何东西。

各实例的 state.db 以只读模式打开(mode=ro),即使 web 有 bug 也写不坏 listener 的库。
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import time
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
    """timer 下次什么时候触发。

    OnUnitInactiveSec 这类相对定时器只填 NextElapseUSecMonotonic,实时字段是 0,
    所以要用开机时刻把单调时间换算回墙上时间(2026-09-21 页面显示 null 才发现)。
    """
    d = _show(f'yizhan-dispatcher@{inst}.timer',
              ['NextElapseUSecRealtime', 'NextElapseUSecMonotonic'])
    us = d.get('NextElapseUSecRealtime')
    if us and us.isdigit() and us != '0':
        return datetime.fromtimestamp(int(us) / 1e6).strftime('%H:%M:%S')
    mono = d.get('NextElapseUSecMonotonic')
    if mono and mono.isdigit() and mono != '0':
        try:
            with open('/proc/uptime') as f:
                up = float(f.read().split()[0])
            boot = time.time() - up
            return datetime.fromtimestamp(boot + int(mono) / 1e6).strftime('%H:%M:%S')
        except (OSError, ValueError):
            return None
    return None


# ---- 给人看的文案 ----
# 页面上不露内部代号:状态、告警都在后端翻成中文,前端只管显示,两边不会各翻各的。
STATE_LABELS = {
    'open': '待处理', 'answering': '处理中', 'waiting': '挂起等待',
    'answered': '已答复', 'closed': '已关闭', 'stuck': '卡死',
}

_ALERT_LABELS = {
    'probe_failed': 'claude 连不上',
    'listener_stale': '监听心跳中断',
    'session_rotate': '会话已轮换',
    'new_chat': '机器人进了新群',
    'stuck': '卡死',
    'stale_open': '长时间未处理',
    'defer_exhausted': '挂起次数用完',
    'ws_missed': '长连接漏消息',
}
RECENT_ALERT_MS = 24 * 3600_000       # 卡片墙只挂 24 小时内的告警,旧的不再红着误导人


def state_label(state: str | None) -> str:
    return STATE_LABELS.get(state or '', state or '?')


def alert_label(kind: str) -> str:
    """'stuck:12' → '卡 #12 卡死';'new_chat:oc_x' → '机器人进了新群'。"""
    base, _, arg = (kind or '').partition(':')
    label = _ALERT_LABELS.get(base, base or '未知告警')
    if arg and base in ('stuck', 'stale_open', 'defer_exhausted'):
        return f'卡 #{arg} {label}'
    return label


def _alert_row(r) -> dict:
    keys = r.keys()
    return {'kind': r['kind'], 'label': alert_label(r['kind']), 'at': r['sent_at'],
            'text': r['text'] if 'text' in keys else None}


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
                   today_cost_usd=0.0, pending=0, context_tokens=0, last_alert=None,
                   last_msg_at=None, last_reply_at=None)
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
    a = conn.execute('SELECT * FROM alerts WHERE sent_at>=? ORDER BY sent_at DESC LIMIT 1',
                     (now - RECENT_ALERT_MS,)).fetchone()
    row['last_alert'] = _alert_row(a) if a else None
    row['last_msg_at'] = q("SELECT MAX(create_time) FROM events WHERE sender_type!='app'")
    row['last_reply_at'] = q('SELECT MAX(sent_at) FROM replies')
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
    alerts = [_alert_row(r) for r in conn.execute('SELECT * FROM alerts ORDER BY sent_at DESC LIMIT 10')]
    states = dict(conn.execute('SELECT state,COUNT(*) FROM issues GROUP BY state').fetchall())
    state_list = [{'state': k, 'label': state_label(k), 'n': v} for k, v in states.items()]
    summary = instance_summary(inst)
    conn.close()
    # dispatcher 正在跑时 timer 没有下次时间(OnUnitInactiveSec 要等它先停),不是故障
    dispatcher_active = unit_state(f'yizhan-dispatcher@{inst}.service') in ('active', 'activating')
    return {'inst': inst, 'hourly': hourly, 'alerts': alerts, 'card_states': states,
            'card_state_list': state_list,
            'summary': summary, 'timer_next': timer_next(inst),
            'dispatcher_active': dispatcher_active,
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
        first, last_rep, n_rep = conn.execute('SELECT MIN(sent_at), MAX(sent_at), COUNT(*) FROM replies '
                                              'WHERE issue_id=?', (r['issue_id'],)).fetchone()
        m0 = conn.execute('SELECT e.text, e.chat_type FROM issue_messages im JOIN events e '
                          'ON e.message_id=im.message_id WHERE im.issue_id=? ORDER BY e.create_time LIMIT 1',
                          (r['issue_id'],)).fetchone()
        items.append({'id': r['issue_id'], 'state': r['state'], 'state_label': state_label(r['state']),
                      'preview': _preview(m0['text'] if m0 else ''),
                      'source': '私聊' if (m0 and m0['chat_type'] == 'p2p') else '群',
                      'n_replies': n_rep, 'last_reply_at': last_rep,
                      'sender': r['sender_id'],
                      'sender_name': _sender_name(conn, r['sender_id']), 'n_msgs': n,
                      'created_at': r['created_at'], 'chat_id': r['chat_id'],
                      'thread_id': r['thread_id'], 'redispatch': r['redispatch'], 'note': r['note'],
                      'first_reply_s': round((first - r['created_at']) / 1000) if first else None})
    conn.close()
    return {'total': total, 'page': page, 'size': size, 'items': items}


def _preview(text: str, n: int = 60) -> str:
    """卡列表里的一行摘要:去掉 @机器人 前缀和换行。"""
    import re
    t = re.sub(r'^\s*(@\S+\s*)+', '', text or '').replace('\n', ' ').strip()
    return t if len(t) <= n else t[:n] + '…'


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
    c = dict(it)
    c['state_label'] = state_label(c.get('state'))
    return {'card': c, 'messages': msgs, 'replies': reps, 'rounds': rnds}


def session(inst: str) -> dict:
    cfg, conn = _conn_or_raise(inst)
    s = dbm.session_get(conn)
    rnds = [dict(r) for r in dbm.rounds(conn)]
    hist = [dict(r) for r in dbm.session_history(conn)]
    # 当前会话累计:直接取 CLI 报的最新累计值,和 claude 自己的口径一致
    total = 0.0
    if s:
        r = conn.execute('SELECT MAX(cost_cum_usd) FROM rounds WHERE session_id=?',
                         (s['session_id'],)).fetchone()
        total = float(r[0] or 0.0)
    if not total:
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
    if not p.exists() and '.' not in safe:
        # fgctl 下载时存成 <key>.png,卡详情里的链接用的是裸 key(2026-09-23 审查发现图片全 404)
        png = p.with_name(safe + '.png')
        if png.exists():
            return png
    return p


# ---- 统计 ----
# 统计按「类」算,不按原始 kind:色板只有 8 个槽,超过 8 类必须合并,不能生成第 9 种颜色。
# 两种超时合成一类,给「权限拒绝」腾出槽位。
ERROR_CATEGORY = {
    'probe_failed': 'probe_failed', 'resume_failed': 'resume_failed',
    'idle_killed': 'timeout', 'hard_killed': 'timeout', 'rc_nonzero': 'rc_nonzero',
    'stuck': 'stuck', 'permission_denied': 'permission_denied',
    'evidence_rejected': 'evidence_rejected', 'feishu_error': 'feishu_error',
}
ERROR_LABELS = {
    'probe_failed': '探活失败', 'resume_failed': '会话续接失败', 'timeout': '超时被杀',
    'rc_nonzero': '异常退出', 'stuck': '卡死', 'permission_denied': '权限拒绝',
    'evidence_rejected': '证据门拦下', 'feishu_error': '飞书接口/长连接',
}
# 固定顺序:颜色跟着种类走,不跟排名走(筛选后幸存的系列不能换色)。与 app.js 的 ERROR_SLOT 一一对应。
ERROR_ORDER = list(ERROR_LABELS)
REPLY_TYPE_LABELS = {'frame': '画面', 'config': '配置', 'time': '时间', 'error': '报错',
                     'script': '剧本', 'other': '答疑'}


def _pct(vals: list[float], p: float) -> float | None:
    if not vals:
        return None
    v = sorted(vals)
    k = (len(v) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 1)


def stats(inst: str, days: int = 7) -> dict:
    """统计页的全部数据。按本地时区的自然日分桶,没数据的日子补 0(图上要看得出「那天没活」)。"""
    from collections import Counter, defaultdict
    days = max(1, min(int(days), 90))
    cfg, conn = _conn_or_raise(inst)
    today0 = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start = today0 - timedelta(days=days - 1)
    t0 = int(start.timestamp() * 1000)
    day_keys = [(start + timedelta(days=i)).strftime('%m-%d') for i in range(days)]
    dk = lambda ms: datetime.fromtimestamp(ms / 1000).strftime('%m-%d')

    msgs, cards_n, reps, cost, errs = Counter(), Counter(), Counter(), defaultdict(float), Counter()
    err_kind_day: dict[str, Counter] = defaultdict(Counter)
    frs_by_day: dict[str, list] = defaultdict(list)

    for (t,) in conn.execute("SELECT create_time FROM events WHERE create_time>=? AND sender_type!='app'", (t0,)):
        msgs[dk(t)] += 1
    for (t,) in conn.execute('SELECT sent_at FROM replies WHERE sent_at>=?', (t0,)):
        reps[dk(t)] += 1
    for t, c in conn.execute('SELECT started_at, cost_usd FROM rounds WHERE started_at>=?', (t0,)):
        cost[dk(t)] += c or 0.0
    has_errors = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='errors'").fetchone()
    for kind, t in (conn.execute('SELECT kind, at FROM errors WHERE at>=?', (t0,)) if has_errors else []):
        errs[dk(t)] += 1
        err_kind_day[ERROR_CATEGORY.get(kind, 'rc_nonzero')][dk(t)] += 1
    all_frs = []
    for created, first in conn.execute('SELECT i.created_at, (SELECT MIN(sent_at) FROM replies r '
                                       'WHERE r.issue_id=i.issue_id) FROM issues i WHERE i.created_at>=?', (t0,)):
        cards_n[dk(created)] += 1
        if first:
            s = (first - created) / 1000
            frs_by_day[dk(created)].append(s)
            all_frs.append(s)

    daily = [{'day': d, 'msgs': msgs[d], 'cards': cards_n[d], 'replies': reps[d],
              'cost_usd': round(cost[d], 2), 'errors': errs[d],
              'first_reply_p50_s': _pct(frs_by_day[d], 0.5),
              'first_reply_p90_s': _pct(frs_by_day[d], 0.9)} for d in day_keys]

    present = [k for k in ERROR_ORDER if err_kind_day.get(k)] + \
              sorted(k for k in err_kind_day if k not in ERROR_ORDER)
    errors_by_kind = [{'kind': k, 'label': ERROR_LABELS.get(k, k),
                       'daily': [err_kind_day[k][d] for d in day_keys],
                       'total': sum(err_kind_day[k].values())} for k in present]

    rtypes = Counter()
    for (ty,) in conn.execute('SELECT type FROM replies WHERE sent_at>=?', (t0,)):
        for one in (ty or 'other').split():
            rtypes[one] += 1
    reply_types = [{'type': k, 'label': REPLY_TYPE_LABELS.get(k, k), 'n': n}
                   for k, n in rtypes.most_common()]

    snd = Counter(r[0] for r in conn.execute('SELECT sender_id FROM issues WHERE created_at>=?', (t0,)))
    top = snd.most_common(5)
    senders = [{'name': _sender_name(conn, oid), 'n': n} for oid, n in top]
    rest = sum(snd.values()) - sum(n for _, n in top)
    if rest:
        senders.append({'name': '其他', 'n': rest})

    context = [{'at': r['started_at'], 'issue_id': r['issue_id'], 'context_tokens': r['context_tokens'],
                'cost_usd': r['cost_usd'], 'duration_s': round((r['ended_at'] - r['started_at']) / 1000)}
               for r in conn.execute('SELECT * FROM rounds WHERE started_at>=? ORDER BY started_at', (t0,))]

    first_round = conn.execute('SELECT MIN(started_at) FROM rounds').fetchone()[0]
    first_error = conn.execute('SELECT MIN(at) FROM errors').fetchone()[0] if has_errors else None
    n_cards = sum(cards_n.values())
    answered = conn.execute("SELECT COUNT(*) FROM issues WHERE created_at>=? AND state IN ('answered','closed')",
                            (t0,)).fetchone()[0]
    conn.close()
    return {
        'range': {'days': days, 'start': day_keys[0], 'end': day_keys[-1]},
        'kpis': {'msgs': sum(msgs.values()), 'cards': n_cards, 'replies': sum(reps.values()),
                 'cost_usd': round(sum(cost.values()), 2), 'errors': sum(errs.values()),
                 'evidence_rejected': sum(err_kind_day['evidence_rejected'].values()),
                 'first_reply_p50_s': _pct(all_frs, 0.5), 'first_reply_p90_s': _pct(all_frs, 0.9),
                 'answered_rate': round(answered / n_cards, 3) if n_cards else None},
        'daily': daily, 'errors_by_kind': errors_by_kind, 'reply_types': reply_types,
        'senders': senders, 'context': context,
        # 数据从什么时候开始有的:页面要说清楚,免得把「没记录」读成「是 0」
        'coverage': {'cost_since': first_round, 'errors_since': first_error},
    }
