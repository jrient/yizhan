import json
import os
from pathlib import Path

import pytest

from gateway import cards, db as dbm, dispatcher
from gateway.events import EventRow

FAKE = str(Path(__file__).parent / 'fakes' / 'fake_claude.py')
ME = [{'key': '@_u1', 'id': 'ou_bot', 'name': 'bot'}]


class FakeClient:
    def __init__(self): self.texts = []
    def send_text(self, oid, text): self.texts.append(text); return 'om_alert'


@pytest.fixture
def dcfg(cfg, tmp_path, monkeypatch):
    cfg.claude_bin = FAKE
    cfg.idle_timeout_min = 0.5
    monkeypatch.setenv('FAKE_MODE', 'ok')
    monkeypatch.setenv('FAKE_RECORD', str(tmp_path / 'rec.jsonl'))
    return cfg


def seed(conn, cfg, mid='om_0'):
    t = dbm.now_ms() - 120_000
    dbm.insert_event(conn, EventRow(message_id=mid, chat_id='oc_group1', chat_type='group', create_time=t,
                                    sender_id='ou_a', sender_type='user', msg_type='text', text='问题',
                                    mentions=ME, mentions_me=True))


def test_full_round_marks_answered_when_hook_answers(dcfg, tmp_path):
    conn = dbm.connect(dcfg.paths.db_file); seed(conn, dcfg); conn.close()
    hook = tmp_path / 'hook.py'
    hook.write_text(
        "import sqlite3,os\n"
        f"c=sqlite3.connect({str(dcfg.paths.db_file)!r})\n"
        "c.execute(\"UPDATE issues SET state='answered'\"); c.commit()\n")
    os.environ['FAKE_HOOK'] = str(hook)
    try:
        assert dispatcher.run_once(dcfg, client=FakeClient()) == 1
    finally:
        os.environ.pop('FAKE_HOOK')
    conn = dbm.connect(dcfg.paths.db_file)
    it = dbm.get_issue(conn, 1)
    assert it['state'] == 'answered' and it['lock_owner'] is None
    rec = json.loads((tmp_path / 'rec.jsonl').read_text().splitlines()[0])
    assert '处理问题卡 #1' in ' '.join(rec['argv'])
    assert dbm.session_get(conn)['fresh'] == 0


def test_no_answer_redispatches_then_stuck(dcfg):
    conn = dbm.connect(dcfg.paths.db_file); seed(conn, dcfg); conn.close()
    c = FakeClient()
    dispatcher.run_once(dcfg, client=c)          # claude 什么都没干 → 重派到上限
    conn = dbm.connect(dcfg.paths.db_file)
    it = dbm.get_issue(conn, 1)
    assert it['state'] == 'stuck' and it['redispatch'] == 3
    assert any('stuck' in t for t in c.texts)


def test_resume_failure_rotates(dcfg, monkeypatch):
    conn = dbm.connect(dcfg.paths.db_file)
    seed(conn, dcfg)
    first = dbm.session_rotate(conn, 'init')
    dbm.session_mark_used(conn)                  # 不再 fresh → 会走 --resume
    conn.close()
    monkeypatch.setenv('FAKE_MODE', 'resume_fail')
    c = FakeClient()
    dispatcher.run_once(dcfg, client=c, max_cards=1)
    conn = dbm.connect(dcfg.paths.db_file)
    s = dbm.session_get(conn)
    assert s['session_id'] != first and s['prev_id'] == first and s['fresh'] == 1
    assert any('轮换' in t for t in c.texts)


def test_lock_blocks_second_instance(dcfg):
    fh = dispatcher.acquire_lock(dcfg.paths.lock_file)
    assert fh is not None
    assert dispatcher.acquire_lock(dcfg.paths.lock_file) is None
    fh.close()
    assert dispatcher.acquire_lock(dcfg.paths.lock_file) is not None


def test_watchdog_alerts_on_stale_listener(dcfg, monkeypatch):
    seen = []
    monkeypatch.setattr(dispatcher, 'restart_listener', lambda inst: seen.append(inst))
    conn = dbm.connect(dcfg.paths.db_file)
    dbm.heartbeat_touch(conn, dbm.now_ms() - 20 * 60_000)
    c = FakeClient()
    dispatcher.watchdog(conn, dcfg, c)
    assert any('心跳' in t for t in c.texts) and seen == ['demo']


def test_quiet_window_skips_recent_message(dcfg):
    conn = dbm.connect(dcfg.paths.db_file)
    dbm.insert_event(conn, EventRow(message_id='om_x', chat_id='oc_group1', chat_type='group',
                                    create_time=dbm.now_ms(), sender_id='ou_a', sender_type='user',
                                    msg_type='text', text='刚发的', mentions=ME, mentions_me=True))
    conn.close()
    assert dispatcher.run_once(dcfg, client=FakeClient()) == 0


def test_threshold_rotate(dcfg):
    dcfg.rotate_at_tokens = 5           # 假 claude 的 assistant 消息报 input 40 → 超阈值
    conn = dbm.connect(dcfg.paths.db_file); seed(conn, dcfg)
    first = dbm.session_rotate(conn, 'init'); dbm.session_mark_used(conn); conn.close()
    c = FakeClient()
    dispatcher.run_once(dcfg, client=c, max_cards=1)
    conn = dbm.connect(dcfg.paths.db_file)
    assert dbm.session_get(conn)['prev_id'] == first
    assert any('上下文' in t for t in c.texts)
