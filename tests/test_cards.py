from gateway import db as dbm, cards
from gateway.events import EventRow

ME = [{'key': '@_u1', 'id': 'ou_bot', 'name': 'bot'}]


def ev(mid, t, sender='ou_a', chat='oc_group1', ctype='group', thread=None, me=True):
    return EventRow(message_id=mid, chat_id=chat, chat_type=ctype, create_time=t, sender_id=sender,
                    sender_type='user', thread_id=thread, text='q', mentions=ME if me else [], mentions_me=me)


def test_three_messages_one_card(db, cfg):
    for i in range(3):
        dbm.insert_event(db, ev(f'om_{i}', 1_000_000 + i * 60_000))
    created = cards.assign_cards(db, cfg, 2_000_000)
    assert len(created) == 1
    assert len(dbm.issue_messages(db, created[0])) == 3


def test_gap_splits_cards(db, cfg):
    dbm.insert_event(db, ev('om_0', 1_000_000))
    dbm.insert_event(db, ev('om_1', 1_000_000 + 11 * 60_000))
    assert len(cards.assign_cards(db, cfg, 3_000_000)) == 2


def test_thread_followup_same_card_without_mention(db, cfg):
    dbm.insert_event(db, ev('om_0', 1_000_000, thread='omt_1'))
    dbm.touch_thread(db, 'omt_1', 'oc_group1', 'om_0', 1_000_000, participated=True)
    dbm.insert_event(db, ev('om_1', 1_000_000 + 5 * 3600_000, sender='ou_b', thread='omt_1', me=False))
    created = cards.assign_cards(db, cfg, 2_000_000)
    assert len(created) == 1 and len(dbm.issue_messages(db, created[0])) == 2


def test_admission(db, cfg):
    dbm.insert_event(db, ev('p_ok', 1, sender='ou_boss', chat='oc_p', ctype='p2p', me=False))
    dbm.insert_event(db, ev('p_no', 2, sender='ou_stranger', chat='oc_p2', ctype='p2p', me=False))
    dbm.insert_event(db, ev('g_no_mention', 3, me=False))
    dbm.insert_event(db, ev('g_other_chat', 4, chat='oc_unknown'))
    created = cards.assign_cards(db, cfg, 10)
    assert len(created) == 1
    assert dbm.get_event(db, 'p_ok')['card_state'] == 'carded'
    for m in ('p_no', 'g_no_mention', 'g_other_chat'):
        assert dbm.get_event(db, m)['card_state'] == 'ignored'


def test_quiet_window_and_pick(db, cfg):
    dbm.insert_event(db, ev('om_0', 1_000_000))
    (iid,) = cards.assign_cards(db, cfg, 1_000_000)
    assert cards.pick_card(db, cfg, 1_000_000 + 10_000) is None
    assert cards.pick_card(db, cfg, 1_000_000 + 31_000)['issue_id'] == iid


def test_finish_and_stuck(db, cfg):
    dbm.insert_event(db, ev('om_0', 1_000_000))
    (iid,) = cards.assign_cards(db, cfg, 1_000_000)
    cards.lock_card(db, cfg, iid, 'pid1', 2_000_000)
    assert dbm.get_issue(db, iid)['state'] == 'answering'
    assert cards.finish_card(db, cfg, iid) == 'open'
    cards.lock_card(db, cfg, iid, 'pid2', 2_000_000)
    assert cards.finish_card(db, cfg, iid) == 'open'
    cards.lock_card(db, cfg, iid, 'pid3', 2_000_000)
    assert cards.finish_card(db, cfg, iid) == 'stuck'


def test_answered_finish_and_defer_wake(db, cfg):
    dbm.insert_event(db, ev('om_0', 1_000_000))
    (iid,) = cards.assign_cards(db, cfg, 1_000_000)
    cards.lock_card(db, cfg, iid, 'p', 2_000_000)
    dbm.update_issue(db, iid, state='waiting', defer_until=3_000_000)
    assert cards.finish_card(db, cfg, iid) == 'waiting'
    assert cards.wake_deferred(db, 2_500_000) == []
    assert cards.wake_deferred(db, 3_000_000) == [iid]
    assert dbm.get_issue(db, iid)['state'] == 'open'


def test_waiting_card_new_message_reopens(db, cfg):
    dbm.insert_event(db, ev('om_0', 1_000_000, thread='omt'))
    (iid,) = cards.assign_cards(db, cfg, 1_000_000)
    dbm.update_issue(db, iid, state='waiting', defer_until=9_000_000)
    dbm.touch_thread(db, 'omt', 'oc_group1', 'om_0', 1_000_000, participated=True)
    dbm.insert_event(db, ev('om_1', 1_100_000, thread='omt', me=False))
    assert cards.assign_cards(db, cfg, 1_200_000) == []
    it = dbm.get_issue(db, iid)
    assert it['state'] == 'open' and it['defer_until'] is None


def test_recover_stale(db, cfg):
    dbm.insert_event(db, ev('om_0', 1_000_000))
    (iid,) = cards.assign_cards(db, cfg, 1_000_000)
    cards.lock_card(db, cfg, iid, 'dead', 2_000_000)
    assert cards.recover_stale(db, cfg) == [(iid, 'open')]
    assert dbm.get_issue(db, iid)['redispatch'] == 1


def test_next_ready_at(db, cfg):
    assert cards.next_ready_at(db, cfg, 1_000_000) is None
    dbm.insert_event(db, ev('om_0', 1_000_000))
    (iid,) = cards.assign_cards(db, cfg, 1_000_000)
    assert cards.next_ready_at(db, cfg, 1_000_000) == 1_030_000     # 最后一条消息 + 30 秒
    dbm.update_issue(db, iid, defer_until=1_500_000)
    assert cards.next_ready_at(db, cfg, 1_000_000) == 1_500_000     # defer 更晚就听 defer
