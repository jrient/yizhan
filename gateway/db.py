"""每实例一个 SQLite(WAL)。fgctl 与 listener 是唯二写入口。"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  message_id   TEXT PRIMARY KEY,
  chat_id      TEXT NOT NULL,
  chat_type    TEXT,               -- group / p2p
  thread_id    TEXT,
  root_id      TEXT,
  parent_id    TEXT,
  sender_id    TEXT,
  sender_type  TEXT,               -- user / app
  create_time  INTEGER NOT NULL,   -- ms
  msg_type     TEXT,
  text         TEXT,
  image_keys   TEXT,               -- json list
  mentions     TEXT,               -- json list of {key,id,name}
  mentions_me  INTEGER DEFAULT 0,
  card_state   TEXT DEFAULT 'new', -- new / carded / ignored
  issue_id     INTEGER,
  raw          TEXT,
  received_at  INTEGER
);
CREATE INDEX IF NOT EXISTS ix_events_state ON events(card_state, create_time);
CREATE INDEX IF NOT EXISTS ix_events_chat ON events(chat_id, create_time);

CREATE TABLE IF NOT EXISTS chats (
  chat_id     TEXT PRIMARY KEY,
  chat_type   TEXT,
  name        TEXT,
  allowed     INTEGER DEFAULT 0,
  last_active INTEGER
);
CREATE TABLE IF NOT EXISTS threads (
  thread_id    TEXT PRIMARY KEY,
  chat_id      TEXT,
  root_id      TEXT,
  participated INTEGER DEFAULT 0,  -- 我在里面发过言
  last_active  INTEGER
);
CREATE TABLE IF NOT EXISTS issues (
  issue_id     INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id      TEXT NOT NULL,
  thread_id    TEXT,
  sender_id    TEXT,
  merge_key    TEXT,
  state        TEXT DEFAULT 'open',   -- open/answering/waiting/answered/closed/stuck
  created_at   INTEGER,
  updated_at   INTEGER,
  last_msg_at  INTEGER,
  lock_owner   TEXT,
  lock_until   INTEGER,
  locked_at    INTEGER,
  last_read_at INTEGER,
  redispatch   INTEGER DEFAULT 0,
  defer_count  INTEGER DEFAULT 0,
  defer_until  INTEGER,
  woke_from_defer INTEGER DEFAULT 0,
  note         TEXT
);
CREATE INDEX IF NOT EXISTS ix_issues_state ON issues(state, created_at);
CREATE TABLE IF NOT EXISTS issue_messages (
  issue_id   INTEGER, message_id TEXT, seq INTEGER,
  PRIMARY KEY (issue_id, message_id)
);
CREATE TABLE IF NOT EXISTS replies (
  reply_id      INTEGER PRIMARY KEY AUTOINCREMENT,
  issue_id      INTEGER,
  to_message_id TEXT,
  my_message_id TEXT,
  via           TEXT,
  type          TEXT,
  evidence      TEXT,
  summary       TEXT,
  sent_at       INTEGER
);
CREATE TABLE IF NOT EXISTS session (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  session_id  TEXT,
  created_at  INTEGER,
  reason      TEXT,
  prev_id     TEXT,
  usage       TEXT,
  fresh       INTEGER DEFAULT 1     -- 1 = 还没跑过第一轮(不能 --resume)
);
CREATE TABLE IF NOT EXISTS rounds (
  round_id       INTEGER PRIMARY KEY AUTOINCREMENT,
  issue_id       INTEGER,
  session_id     TEXT,
  started_at     INTEGER,
  ended_at       INTEGER,
  rc             INTEGER,
  context_tokens INTEGER,
  output_tokens  INTEGER,
  cost_usd       REAL,         -- 本轮增量(API 折算价)
  cost_cum_usd   REAL,         -- CLI 报的会话累计值,算增量用
  outcome        TEXT          -- answered/waiting/open/stuck/rotated
);
CREATE INDEX IF NOT EXISTS ix_rounds_issue ON rounds(issue_id);
CREATE TABLE IF NOT EXISTS session_history (
  session_id TEXT PRIMARY KEY, created_at INTEGER, reason TEXT, prev_id TEXT, ended_at INTEGER
);
CREATE TABLE IF NOT EXISTS cursor (
  key TEXT PRIMARY KEY, last_time INTEGER
);
CREATE TABLE IF NOT EXISTS heartbeat (
  id INTEGER PRIMARY KEY CHECK (id = 1), at INTEGER
);
CREATE TABLE IF NOT EXISTS alerts (
  kind TEXT, sent_at INTEGER
);
CREATE TABLE IF NOT EXISTS members (
  open_id TEXT PRIMARY KEY, name TEXT
);
CREATE TABLE IF NOT EXISTS reactions (
  message_id TEXT PRIMARY KEY, reaction_id TEXT
);
"""


def now_ms() -> int:
    return int(time.time() * 1000)


def _migrate(conn):
    """给已经在跑的库补新列。"""
    have = {r[1] for r in conn.execute('PRAGMA table_info(issues)')}
    for col, ddl in (('defer_count', 'INTEGER DEFAULT 0'), ('woke_from_defer', 'INTEGER DEFAULT 0')):
        if col not in have:
            conn.execute(f'ALTER TABLE issues ADD COLUMN {col} {ddl}')
    rcols = {r[1] for r in conn.execute('PRAGMA table_info(rounds)')}
    if rcols and 'cost_cum_usd' not in rcols:
        conn.execute('ALTER TABLE rounds ADD COLUMN cost_cum_usd REAL')


def connect(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=30000')
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


# ---- events ----
def insert_event(conn, ev) -> bool:
    """ev: gateway.events.EventRow。重复 message_id 返回 False。"""
    try:
        conn.execute(
            'INSERT INTO events(message_id,chat_id,chat_type,thread_id,root_id,parent_id,sender_id,sender_type,'
            'create_time,msg_type,text,image_keys,mentions,mentions_me,raw,received_at) '
            'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (ev.message_id, ev.chat_id, ev.chat_type, ev.thread_id, ev.root_id, ev.parent_id, ev.sender_id,
             ev.sender_type, ev.create_time, ev.msg_type, ev.text, json.dumps(ev.image_keys),
             json.dumps(ev.mentions, ensure_ascii=False), int(ev.mentions_me),
             json.dumps(ev.raw, ensure_ascii=False) if ev.raw is not None else None, now_ms()))
    except sqlite3.IntegrityError:
        return False
    for m in ev.mentions:
        if m.get('id') and m.get('name'):
            conn.execute('INSERT OR REPLACE INTO members(open_id,name) VALUES(?,?)', (m['id'], m['name']))
    return True


def new_events(conn) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM events WHERE card_state='new' ORDER BY create_time").fetchall()


def set_event_state(conn, message_id: str, state: str, issue_id: int | None = None):
    conn.execute('UPDATE events SET card_state=?, issue_id=? WHERE message_id=?', (state, issue_id, message_id))


def get_event(conn, message_id: str):
    return conn.execute('SELECT * FROM events WHERE message_id=?', (message_id,)).fetchone()


def chat_events(conn, chat_id: str, since_ms: int | None = None, limit: int = 30, thread_id: str | None = None):
    q = 'SELECT * FROM events WHERE chat_id=?'
    args: list[Any] = [chat_id]
    if thread_id:
        q += ' AND thread_id=?'; args.append(thread_id)
    if since_ms:
        q += ' AND create_time>=?'; args.append(since_ms)
    q += ' ORDER BY create_time DESC LIMIT ?'; args.append(limit)
    return conn.execute(q, args).fetchall()[::-1]


# ---- chats / threads ----
def upsert_chat(conn, chat_id: str, chat_type: str | None, name: str | None = None, allowed: bool | None = None,
                active_ms: int | None = None):
    row = conn.execute('SELECT * FROM chats WHERE chat_id=?', (chat_id,)).fetchone()
    if row is None:
        conn.execute('INSERT INTO chats(chat_id,chat_type,name,allowed,last_active) VALUES(?,?,?,?,?)',
                     (chat_id, chat_type, name, int(bool(allowed)), active_ms or now_ms()))
        return True
    conn.execute('UPDATE chats SET chat_type=COALESCE(?,chat_type), name=COALESCE(?,name), '
                 'allowed=COALESCE(?,allowed), last_active=COALESCE(?,last_active) WHERE chat_id=?',
                 (chat_type, name, None if allowed is None else int(allowed), active_ms, chat_id))
    return False


def touch_thread(conn, thread_id: str, chat_id: str, root_id: str | None, active_ms: int,
                 participated: bool | None = None):
    conn.execute('INSERT INTO threads(thread_id,chat_id,root_id,participated,last_active) VALUES(?,?,?,?,?) '
                 'ON CONFLICT(thread_id) DO UPDATE SET last_active=MAX(last_active,excluded.last_active), '
                 'root_id=COALESCE(threads.root_id,excluded.root_id), '
                 'participated=MAX(threads.participated,excluded.participated)',
                 (thread_id, chat_id, root_id, int(bool(participated)), active_ms))


def participated_threads(conn) -> set[str]:
    return {r[0] for r in conn.execute('SELECT thread_id FROM threads WHERE participated=1')}


# ---- cursor / heartbeat ----
def get_cursor(conn, key: str) -> int:
    r = conn.execute('SELECT last_time FROM cursor WHERE key=?', (key,)).fetchone()
    return int(r[0]) if r else 0


def set_cursor(conn, key: str, t: int):
    conn.execute('INSERT INTO cursor(key,last_time) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET '
                 'last_time=MAX(last_time,excluded.last_time)', (key, t))


def heartbeat_touch(conn, t: int | None = None):
    conn.execute('INSERT INTO heartbeat(id,at) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET at=excluded.at',
                 (t or now_ms(),))


def heartbeat_get(conn) -> int:
    r = conn.execute('SELECT at FROM heartbeat WHERE id=1').fetchone()
    return int(r[0]) if r else 0


# ---- session ----
def session_get(conn):
    return conn.execute('SELECT * FROM session WHERE id=1').fetchone()


def session_rotate(conn, reason: str) -> str:
    prev = session_get(conn)
    sid = str(uuid.uuid4())
    t = now_ms()
    conn.execute('INSERT INTO session(id,session_id,created_at,reason,prev_id,fresh) VALUES(1,?,?,?,?,1) '
                 'ON CONFLICT(id) DO UPDATE SET session_id=excluded.session_id, created_at=excluded.created_at, '
                 'reason=excluded.reason, prev_id=excluded.prev_id, fresh=1, usage=NULL',
                 (sid, t, reason, prev['session_id'] if prev else None))
    if prev:
        conn.execute('UPDATE session_history SET ended_at=? WHERE session_id=? AND ended_at IS NULL',
                     (t, prev['session_id']))
    conn.execute('INSERT OR IGNORE INTO session_history(session_id,created_at,reason,prev_id,ended_at) '
                 'VALUES(?,?,?,?,NULL)', (sid, t, reason, prev['session_id'] if prev else None))
    return sid


def session_mark_used(conn, usage: dict | None = None):
    conn.execute('UPDATE session SET fresh=0, usage=COALESCE(?,usage) WHERE id=1',
                 (json.dumps(usage, ensure_ascii=False) if usage else None,))


def session_cost_cum(conn, session_id: str) -> float | None:
    """这个会话上一轮记下的累计值。没有记录返回 None。"""
    r = conn.execute('SELECT cost_cum_usd FROM rounds WHERE session_id=? AND cost_cum_usd IS NOT NULL '
                     'ORDER BY started_at DESC, round_id DESC LIMIT 1', (session_id,)).fetchone()
    return float(r[0]) if r else None


def insert_round(conn, *, issue_id, session_id, started_at, ended_at, rc, context_tokens,
                 output_tokens, cost_usd, outcome, cost_cum_usd=None):
    """一轮 claude 的账。页面的成本与会话曲线读这张表。

    claude-stream.jsonl 里有 token 与 total_cost_usd,但没有卡号,对不上卡;
    session 表只留当前与上一个 id,没有完整轮换史。所以两样都在这里落库(2026-09-21)。
    """
    conn.execute('INSERT INTO rounds(issue_id,session_id,started_at,ended_at,rc,context_tokens,'
                 'output_tokens,cost_usd,cost_cum_usd,outcome) VALUES(?,?,?,?,?,?,?,?,?,?)',
                 (issue_id, session_id, started_at, ended_at, rc, context_tokens, output_tokens,
                  cost_usd, cost_cum_usd, outcome))


def rounds(conn, limit: int = 200) -> list[sqlite3.Row]:
    return conn.execute('SELECT * FROM rounds ORDER BY started_at DESC LIMIT ?', (limit,)).fetchall()[::-1]


def session_history(conn) -> list[sqlite3.Row]:
    # 同一毫秒内连续轮换时 created_at 会相同,再按 rowid 兜底保证新的在前
    return conn.execute('SELECT * FROM session_history ORDER BY created_at DESC, rowid DESC').fetchall()


# ---- issues ----
def create_issue(conn, chat_id: str, thread_id: str | None, sender_id: str, merge_key: str, t: int) -> int:
    cur = conn.execute('INSERT INTO issues(chat_id,thread_id,sender_id,merge_key,state,created_at,updated_at,'
                       'last_msg_at) VALUES(?,?,?,?,"open",?,?,?)',
                       (chat_id, thread_id, sender_id, merge_key, t, t, t))
    return int(cur.lastrowid)


def add_issue_message(conn, issue_id: int, message_id: str, t: int):
    seq = conn.execute('SELECT COALESCE(MAX(seq),0)+1 FROM issue_messages WHERE issue_id=?', (issue_id,)).fetchone()[0]
    conn.execute('INSERT OR IGNORE INTO issue_messages(issue_id,message_id,seq) VALUES(?,?,?)', (issue_id, message_id, seq))
    conn.execute('UPDATE issues SET last_msg_at=MAX(COALESCE(last_msg_at,0),?), updated_at=? WHERE issue_id=?',
                 (t, now_ms(), issue_id))
    set_event_state(conn, message_id, 'carded', issue_id)


def issue_messages(conn, issue_id: int) -> list[sqlite3.Row]:
    return conn.execute('SELECT e.* FROM issue_messages im JOIN events e ON e.message_id=im.message_id '
                        'WHERE im.issue_id=? ORDER BY e.create_time', (issue_id,)).fetchall()


def get_issue(conn, issue_id: int):
    return conn.execute('SELECT * FROM issues WHERE issue_id=?', (issue_id,)).fetchone()


def issue_for_message(conn, message_id: str):
    r = conn.execute('SELECT issue_id FROM issue_messages WHERE message_id=?', (message_id,)).fetchone()
    return get_issue(conn, r[0]) if r else None


def update_issue(conn, issue_id: int, **fields):
    fields['updated_at'] = now_ms()
    sets = ', '.join(f'{k}=?' for k in fields)
    conn.execute(f'UPDATE issues SET {sets} WHERE issue_id=?', (*fields.values(), issue_id))


def issues_by_state(conn, states: Iterable[str]) -> list[sqlite3.Row]:
    states = list(states)
    q = f'SELECT * FROM issues WHERE state IN ({",".join("?"*len(states))}) ORDER BY created_at'
    return conn.execute(q, states).fetchall()


def find_mergeable_issue(conn, merge_key: str):
    """同归并键、仍活跃(open/answering/waiting)的最新一张卡。"""
    return conn.execute("SELECT * FROM issues WHERE merge_key=? AND state IN ('open','answering','waiting') "
                        'ORDER BY created_at DESC LIMIT 1', (merge_key,)).fetchone()


def sender_open_issues(conn, sender_id: str, since_ms: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM issues WHERE sender_id=? AND created_at>=? AND state IN ('open','answering','waiting') "
                        'ORDER BY created_at', (sender_id, since_ms)).fetchall()


# ---- replies ----
def insert_reply(conn, issue_id: int | None, to_message_id: str | None, my_message_id: str | None, via: str,
                 kind: str, evidence: str, summary: str):
    conn.execute('INSERT INTO replies(issue_id,to_message_id,my_message_id,via,type,evidence,summary,sent_at) '
                 'VALUES(?,?,?,?,?,?,?,?)', (issue_id, to_message_id, my_message_id, via, kind, evidence, summary[:400], now_ms()))


def replies_for_issue(conn, issue_id: int) -> list[sqlite3.Row]:
    return conn.execute('SELECT * FROM replies WHERE issue_id=? ORDER BY sent_at', (issue_id,)).fetchall()


def my_message_ids(conn) -> set[str]:
    return {r[0] for r in conn.execute('SELECT my_message_id FROM replies WHERE my_message_id IS NOT NULL')}


# ---- alerts / members / reactions ----
def alert_recent(conn, kind: str, within_ms: int) -> bool:
    r = conn.execute('SELECT 1 FROM alerts WHERE kind=? AND sent_at>=? LIMIT 1', (kind, now_ms() - within_ms)).fetchone()
    return r is not None


def alert_record(conn, kind: str):
    conn.execute('INSERT INTO alerts(kind,sent_at) VALUES(?,?)', (kind, now_ms()))


def members(conn) -> dict[str, str]:
    """{name: open_id}"""
    return {r['name']: r['open_id'] for r in conn.execute('SELECT open_id,name FROM members')}


def member_name(conn, open_id: str) -> str | None:
    r = conn.execute('SELECT name FROM members WHERE open_id=?', (open_id,)).fetchone()
    return r[0] if r else None


def reaction_set(conn, message_id: str, reaction_id: str):
    conn.execute('INSERT OR REPLACE INTO reactions(message_id,reaction_id) VALUES(?,?)', (message_id, reaction_id))


def reaction_pop(conn, message_id: str) -> str | None:
    r = conn.execute('SELECT reaction_id FROM reactions WHERE message_id=?', (message_id,)).fetchone()
    if r:
        conn.execute('DELETE FROM reactions WHERE message_id=?', (message_id,))
        return r[0]
    return None
