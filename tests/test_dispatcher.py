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
    assert '处理问题卡 #1' in _spawned_prompt(tmp_path / 'rec.jsonl')
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
    # max_wait_sec=0:不等静默窗,直接交给 timer(等待行为另有用例)
    conn = dbm.connect(dcfg.paths.db_file)
    dbm.insert_event(conn, EventRow(message_id='om_x', chat_id='oc_group1', chat_type='group',
                                    create_time=dbm.now_ms(), sender_id='ou_a', sender_type='user',
                                    msg_type='text', text='刚发的', mentions=ME, mentions_me=True))
    conn.close()
    assert dispatcher.run_once(dcfg, client=FakeClient(), max_wait_sec=0) == 0


def test_threshold_rotate(dcfg):
    dcfg.rotate_at_tokens = 5           # 假 claude 的 assistant 消息报 input 40 → 超阈值
    conn = dbm.connect(dcfg.paths.db_file); seed(conn, dcfg)
    first = dbm.session_rotate(conn, 'init'); dbm.session_mark_used(conn); conn.close()
    c = FakeClient()
    dispatcher.run_once(dcfg, client=c, max_cards=1)
    conn = dbm.connect(dcfg.paths.db_file)
    assert dbm.session_get(conn)['prev_id'] == first
    assert any('上下文' in t for t in c.texts)


def test_waits_out_quiet_window_instead_of_exiting(dcfg, monkeypatch):
    """消息刚到就被唤起时,只差静默窗应该等,不能空转退出等 timer 再来。"""
    conn = dbm.connect(dcfg.paths.db_file)
    dbm.insert_event(conn, EventRow(message_id='om_now', chat_id='oc_group1', chat_type='group',
                                    create_time=dbm.now_ms(), sender_id='ou_a', sender_type='user',
                                    msg_type='text', text='刚发的', mentions=ME, mentions_me=True))
    conn.close()
    slept = []
    monkeypatch.setattr(dispatcher.time, 'sleep', lambda s: (slept.append(s), dcfg.__setattr__('quiet_seconds', 0)))
    assert dispatcher.run_once(dcfg, client=FakeClient(), max_cards=2) == 1
    assert slept and slept[0] > 20            # 等了将近 30 秒的静默窗


def test_does_not_wait_for_far_future_card(dcfg):
    conn = dbm.connect(dcfg.paths.db_file)
    dbm.insert_event(conn, EventRow(message_id='om_now', chat_id='oc_group1', chat_type='group',
                                    create_time=dbm.now_ms(), sender_id='ou_a', sender_type='user',
                                    msg_type='text', text='x', mentions=ME, mentions_me=True))
    conn.close()
    assert dispatcher.run_once(dcfg, client=FakeClient(), max_wait_sec=1) == 0   # 超过上限就交给 timer


def _spawned_prompt(rec_file):
    """取派卡那次的命令行。探活也会被假 claude 记一行,要跳过。"""
    import json as _j
    for line in rec_file.read_text().splitlines():
        argv = ' '.join(_j.loads(line)['argv'])
        if '问题卡' in argv:
            return argv
    raise AssertionError('没有派卡的记录,只有: ' + rec_file.read_text()[:300])


def test_followup_card_gets_different_prompt(dcfg, tmp_path):
    """defer 醒来的卡必须换提示词,否则会把已经答过的问题再答一遍(2026-09-21 卡 #8)。"""
    conn = dbm.connect(dcfg.paths.db_file)
    seed(conn, dcfg)
    cards.assign_cards(conn, dcfg, dbm.now_ms())
    dbm.insert_reply(conn, 1, 'om_0', 'om_me', 'reply', 'other', '证据' * 8, '之前答过的话')
    dbm.update_issue(conn, 1, state='waiting', defer_until=dbm.now_ms() - 1000, defer_count=1)
    conn.close()
    dispatcher.run_once(dcfg, client=FakeClient(), max_cards=1)
    prompt = _spawned_prompt(tmp_path / 'rec.jsonl')
    assert '复查问题卡 #1' in prompt and '这不是新问题' in prompt
    assert '你已经回过 1 条' in prompt and '什么都不要发' in prompt
    assert '按六步处理' not in prompt
    conn = dbm.connect(dcfg.paths.db_file)
    assert dbm.get_issue(conn, 1)['woke_from_defer'] == 0      # 标记用完即清


def test_normal_card_keeps_original_prompt(dcfg, tmp_path):
    conn = dbm.connect(dcfg.paths.db_file); seed(conn, dcfg); conn.close()
    dispatcher.run_once(dcfg, client=FakeClient(), max_cards=1)
    prompt = _spawned_prompt(tmp_path / 'rec.jsonl')
    assert '处理问题卡 #1' in prompt and '按六步处理' in prompt and '复查' not in prompt


def test_defer_exhausted_alerts(dcfg):
    conn = dbm.connect(dcfg.paths.db_file)
    seed(conn, dcfg)
    cards.assign_cards(conn, dcfg, dbm.now_ms())
    dbm.update_issue(conn, 1, state='waiting', defer_until=dbm.now_ms() - 1000,
                     defer_count=cards.MAX_DEFER)
    conn.close()
    c = FakeClient()
    dispatcher.run_once(dcfg, client=c, max_cards=1)
    assert any('挂起' in t and 'stuck' in t for t in c.texts)
    conn = dbm.connect(dcfg.paths.db_file)
    assert dbm.get_issue(conn, 1)['state'] == 'stuck'


def test_no_probe_when_nothing_to_do(dcfg, monkeypatch):
    """没卡可处理就别探活。一天几百轮空转,每轮起一个 claude 纯属浪费。"""
    calls = []
    monkeypatch.setattr(dispatcher.claude_runner, 'probe',
                        lambda cfg: (calls.append(1), (True, 'ok'))[1])
    assert dispatcher.run_once(dcfg, client=FakeClient(), max_wait_sec=0) == 0
    assert calls == []


def test_probes_once_when_there_is_work(dcfg, monkeypatch):
    calls = []
    monkeypatch.setattr(dispatcher.claude_runner, 'probe',
                        lambda cfg: (calls.append(1), (True, 'ok'))[1])
    conn = dbm.connect(dcfg.paths.db_file)
    for i in range(2):
        dbm.insert_event(conn, EventRow(message_id=f'om_{i}', chat_id='oc_group1', chat_type='group',
                                        create_time=dbm.now_ms() - 120_000 - i * 700_000,
                                        sender_id=f'ou_{i}', sender_type='user', msg_type='text',
                                        text='q', mentions=ME, mentions_me=True))
    conn.close()
    dispatcher.run_once(dcfg, client=FakeClient(), max_cards=4)
    assert len(calls) == 1          # 两张卡也只探一次


def test_probe_failure_touches_no_card(dcfg, monkeypatch):
    monkeypatch.setattr(dispatcher.claude_runner, 'probe', lambda cfg: (False, '探活超时 30s'))
    conn = dbm.connect(dcfg.paths.db_file); seed(conn, dcfg); conn.close()
    c = FakeClient()
    with pytest.raises(dispatcher.ProbeFailed):
        dispatcher.run_once(dcfg, client=c, max_cards=1)
    conn = dbm.connect(dcfg.paths.db_file)
    it = dbm.get_issue(conn, 1)
    assert it['state'] == 'open' and it['redispatch'] == 0      # 卡原封不动
    assert any('探活失败' in t for t in c.texts)
