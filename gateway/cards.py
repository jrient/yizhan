"""问题卡:准入、归并、静默窗、状态机。

状态机:open → answering → answered → closed;answering → waiting(defer) → open;
      open → ignored(准入不过,事件级);answering → stuck(重派超限)。
"""
from __future__ import annotations

import sqlite3

from . import db as dbm
from .config import Config
from .events import EventRow

ACTIVE = ('open', 'answering', 'waiting')


def admit(ev: EventRow, cfg: Config, participated: set[str]) -> bool:
    """群:在 group_allow 且(@我 或 在我参与过的话题);私聊:发送人在 p2p_allow。"""
    if ev.sender_type == 'app' or not ev.sender_id:
        return False
    if ev.chat_type == 'p2p':
        return ev.sender_id in cfg.p2p_allow
    if ev.chat_id not in cfg.group_allow:
        return False
    return bool(ev.mentions_me) or (ev.thread_id in participated if ev.thread_id else False)


def merge_key(ev: EventRow) -> str:
    if ev.thread_id:
        return f't:{ev.chat_id}:{ev.thread_id}'
    return f's:{ev.chat_id}:{ev.sender_id}'


def _row_ev(r: sqlite3.Row) -> EventRow:
    import json
    return EventRow(message_id=r['message_id'], chat_id=r['chat_id'], chat_type=r['chat_type'],
                    create_time=r['create_time'], sender_id=r['sender_id'], sender_type=r['sender_type'],
                    thread_id=r['thread_id'], root_id=r['root_id'], parent_id=r['parent_id'], msg_type=r['msg_type'],
                    text=r['text'] or '', image_keys=json.loads(r['image_keys'] or '[]'),
                    mentions=json.loads(r['mentions'] or '[]'), mentions_me=bool(r['mentions_me']))


def assign_cards(conn, cfg: Config, now_ms: int) -> list[int]:
    """把 card_state='new' 的事件归卡。返回本轮新建的卡 id。"""
    participated = dbm.participated_threads(conn)
    created: list[int] = []
    for r in dbm.new_events(conn):
        ev = _row_ev(r)
        if not admit(ev, cfg, participated):
            dbm.set_event_state(conn, ev.message_id, 'ignored')
            continue
        key = merge_key(ev)
        cur = dbm.find_mergeable_issue(conn, key)
        gap_ok = cur is not None and (ev.thread_id or
                                      abs(ev.create_time - int(cur['last_msg_at'] or 0)) <= cfg.merge_gap_minutes * 60_000)
        if cur is not None and gap_ok:
            iid = int(cur['issue_id'])
            dbm.add_issue_message(conn, iid, ev.message_id, ev.create_time)
            if cur['state'] == 'waiting':      # 等待中的卡来了新消息 → 重新排队
                dbm.update_issue(conn, iid, state='open', defer_until=None)
        else:
            iid = dbm.create_issue(conn, ev.chat_id, ev.thread_id, ev.sender_id, key, ev.create_time)
            dbm.add_issue_message(conn, iid, ev.message_id, ev.create_time)
            created.append(iid)
    return created


def is_quiet(issue: sqlite3.Row, cfg: Config, now_ms: int) -> bool:
    return now_ms - int(issue['last_msg_at'] or 0) >= cfg.quiet_seconds * 1000


def pick_card(conn, cfg: Config, now_ms: int):
    """最早的 open 卡,且 defer 已到、静默窗已满。"""
    for it in dbm.issues_by_state(conn, ['open']):
        if it['defer_until'] and int(it['defer_until']) > now_ms:
            continue
        if is_quiet(it, cfg, now_ms):
            return it
    return None


def lock_card(conn, cfg: Config, issue_id: int, owner: str, now_ms: int):
    dbm.update_issue(conn, issue_id, state='answering', lock_owner=owner, locked_at=now_ms,
                     lock_until=now_ms + cfg.lock_minutes * 60_000)


def finish_card(conn, cfg: Config, issue_id: int) -> str:
    """处理进程结束后收尾。answered/waiting/closed → 解锁;仍 answering → 重派或 stuck。"""
    it = dbm.get_issue(conn, issue_id)
    if it['state'] in ('answered', 'waiting', 'closed'):
        dbm.update_issue(conn, issue_id, lock_owner=None, lock_until=None)
        return it['state']
    n = int(it['redispatch'] or 0) + 1
    if n >= cfg.max_redispatch:
        dbm.update_issue(conn, issue_id, state='stuck', redispatch=n, lock_owner=None, lock_until=None)
        return 'stuck'
    dbm.update_issue(conn, issue_id, state='open', redispatch=n, lock_owner=None, lock_until=None)
    return 'open'


def recover_stale(conn, cfg: Config) -> list[tuple[int, str]]:
    """dispatcher 是单实例,启动时还处于 answering 的卡都是上一次被杀留下的。"""
    out = []
    for it in dbm.issues_by_state(conn, ['answering']):
        out.append((int(it['issue_id']), finish_card(conn, cfg, int(it['issue_id']))))
    return out


def wake_deferred(conn, now_ms: int) -> list[int]:
    woke = []
    for it in dbm.issues_by_state(conn, ['waiting']):
        if it['defer_until'] is not None and int(it['defer_until']) <= now_ms:
            dbm.update_issue(conn, int(it['issue_id']), state='open', defer_until=None)
            woke.append(int(it['issue_id']))
    return woke


def stale_open(conn, now_ms: int, minutes: int = 30) -> list[sqlite3.Row]:
    return [it for it in dbm.issues_by_state(conn, ['open']) if now_ms - int(it['created_at']) > minutes * 60_000]
