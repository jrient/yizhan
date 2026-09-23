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


def test_cost_is_per_round_delta_not_session_cumulative(dcfg, tmp_path, monkeypatch):
    """CLI 的 total_cost_usd 是会话累计。存进 rounds 的必须是本轮增量,否则控制台会重复累加。"""
    import json as _j, os
    conn = dbm.connect(dcfg.paths.db_file)
    for i in range(2):
        dbm.insert_event(conn, EventRow(message_id=f'om_{i}', chat_id='oc_group1', chat_type='group',
                                        create_time=dbm.now_ms() - 120_000 - i * 700_000,
                                        sender_id=f'ou_{i}', sender_type='user', msg_type='text',
                                        text='q', mentions=ME, mentions_me=True))
    conn.close()
    # 假 claude 每次报的累计值:第一次 1.0,第二次 3.5
    counter = tmp_path / 'n'
    hook = tmp_path / 'hook.py'
    hook.write_text(
        "import sqlite3,os,json,sys\n"
        "if '回 ok' in ' '.join(argv): sys.exit(0)\n"          # 探活不计数
        f"p={str(counter)!r}\n"
        "n=int(open(p).read()) if os.path.exists(p) else 0\n"
        "open(p,'w').write(str(n+1))\n"
        f"c=sqlite3.connect({str(dcfg.paths.db_file)!r}); c.execute(\"UPDATE issues SET state='answered' WHERE state='answering'\"); c.commit()\n"
        "print(json.dumps({'type':'result','subtype':'success','total_cost_usd':[1.0,3.5][min(n,1)],'usage':{'output_tokens':1}}),flush=True)\n"
        "sys.exit(0)\n")
    os.environ['FAKE_HOOK'] = str(hook)
    try:
        dispatcher.run_once(dcfg, client=FakeClient(), max_cards=4)
    finally:
        os.environ.pop('FAKE_HOOK')
    conn = dbm.connect(dcfg.paths.db_file)
    rs = dbm.rounds(conn)
    cums = [r['cost_cum_usd'] for r in rs]
    deltas = [r['cost_usd'] for r in rs]
    assert cums[-2:] == [1.0, 3.5]
    assert deltas[-2:] == [pytest.approx(1.0), pytest.approx(2.5)]    # 不是 1.0 和 3.5
