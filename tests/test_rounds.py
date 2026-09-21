"""每轮的账:rounds 表;会话轮换史:session_history 表。页面的成本曲线与轮换列表读这两张。"""
from pathlib import Path

import pytest

from gateway import db as dbm, dispatcher
from gateway.events import EventRow

FAKE = str(Path(__file__).parent / 'fakes' / 'fake_claude.py')
ME = [{'key': '@_u1', 'id': 'ou_bot', 'name': 'bot'}]


@pytest.fixture
def dcfg(cfg, tmp_path, monkeypatch):
    cfg.claude_bin = FAKE
    monkeypatch.setenv('FAKE_MODE', 'ok')
    return cfg


class FakeClient:
    def send_text(self, oid, text):
        return 'om_alert'


def test_round_written_after_dispatch(dcfg, tmp_path):
    conn = dbm.connect(dcfg.paths.db_file)
    dbm.insert_event(conn, EventRow(message_id='om_0', chat_id='oc_group1', chat_type='group',
                                    create_time=dbm.now_ms() - 120_000, sender_id='ou_a', sender_type='user',
                                    msg_type='text', text='问题', mentions=ME, mentions_me=True))
    hook = tmp_path / 'hook.py'
    hook.write_text("import sqlite3\n"
                    f"c=sqlite3.connect({str(dcfg.paths.db_file)!r})\n"
                    "c.execute(\"UPDATE issues SET state='answered'\"); c.commit()\n")
    conn.close()
    import os
    os.environ['FAKE_HOOK'] = str(hook)
    try:
        dispatcher.run_once(dcfg, client=FakeClient(), max_cards=1)
    finally:
        os.environ.pop('FAKE_HOOK')
    conn = dbm.connect(dcfg.paths.db_file)
    rs = dbm.rounds(conn)
    assert len(rs) == 1
    r = rs[0]
    assert r['issue_id'] == 1 and r['outcome'] == 'answered' and r['rc'] == 0
    assert r['cost_usd'] == pytest.approx(0.0123)
    assert r['output_tokens'] == 2 and r['context_tokens'] == 40
    assert r['ended_at'] >= r['started_at'] and r['session_id'] == dbm.session_get(conn)['session_id']


def test_round_records_stuck(dcfg):
    conn = dbm.connect(dcfg.paths.db_file)
    dbm.insert_event(conn, EventRow(message_id='om_0', chat_id='oc_group1', chat_type='group',
                                    create_time=dbm.now_ms() - 120_000, sender_id='ou_a', sender_type='user',
                                    msg_type='text', text='q', mentions=ME, mentions_me=True))
    conn.close()
    dispatcher.run_once(dcfg, client=FakeClient())
    conn = dbm.connect(dcfg.paths.db_file)
    outcomes = [r['outcome'] for r in dbm.rounds(conn)]
    assert outcomes == ['open', 'open', 'stuck']          # 重派两次后卡死


def test_session_history(db):
    a = dbm.session_rotate(db, 'init')
    b = dbm.session_rotate(db, 'resume_failed')
    h = dbm.session_history(db)
    assert [r['session_id'] for r in h] == [b, a]
    assert h[1]['ended_at'] is not None and h[1]['reason'] == 'init'
    assert h[0]['ended_at'] is None and h[0]['prev_id'] == a
