from gateway import db as dbm, listener
from gateway.events import EventRow

ME = [{'key': '@_u1', 'id': 'ou_bot', 'name': 'bot'}]


def ev(mid, **kw):
    d = dict(message_id=mid, chat_id='oc_group1', chat_type='group', create_time=1_700_000_000_000,
             sender_id='ou_a', sender_type='user', msg_type='text', text='hi', mentions=[], mentions_me=False)
    d.update(kw)
    return EventRow(**d)


class Trig:
    def __init__(self): self.calls = []
    def __call__(self, inst): self.calls.append(inst)


def test_drops_own_messages(db, cfg):
    t = Trig()
    assert listener.handle_message(db, cfg, ev('om_1', sender_type='app'), 'ou_bot', t) is False
    assert listener.handle_message(db, cfg, ev('om_2', sender_id='ou_bot'), 'ou_bot', t) is False
    assert dbm.new_events(db) == [] and t.calls == []


def test_idempotent_and_trigger_on_mention(db, cfg):
    t = Trig()
    assert listener.handle_message(db, cfg, ev('om_1', mentions=ME, mentions_me=True), 'ou_bot', t) is True
    assert listener.handle_message(db, cfg, ev('om_1', mentions=ME, mentions_me=True), 'ou_bot', t) is False
    assert t.calls == ['demo']
    assert dbm.heartbeat_get(db) > 0
    assert dbm.get_cursor(db, 'oc_group1') == 1_700_000_000_000


def test_no_trigger_without_mention(db, cfg):
    t = Trig()
    listener.handle_message(db, cfg, ev('om_1'), 'ou_bot', t)
    assert t.calls == []                      # 写库但不唤醒
    assert len(dbm.new_events(db)) == 1


def test_trigger_in_participated_thread(db, cfg):
    dbm.touch_thread(db, 'omt', 'oc_group1', 'om_0', 1, participated=True)
    t = Trig()
    listener.handle_message(db, cfg, ev('om_1', thread_id='omt'), 'ou_bot', t)
    assert t.calls == ['demo']


def test_p2p_triggers(db, cfg):
    t = Trig()
    listener.handle_message(db, cfg, ev('om_1', chat_id='oc_p', chat_type='p2p', sender_id='ou_boss'), 'ou_bot', t)
    assert t.calls == ['demo']
    assert db.execute("SELECT allowed FROM chats WHERE chat_id='oc_p'").fetchone()[0] == 1


def test_bot_added_alerts_unknown_chat(db, cfg):
    sent = []
    class C:
        def send_text(self, oid, text): sent.append((oid, text))
    listener.handle_bot_added(db, cfg, C(), 'oc_brand_new')
    assert sent and 'oc_brand_new' in sent[0][1] and sent[0][0] == 'ou_admin'
    sent.clear()
    listener.handle_bot_added(db, cfg, C(), 'oc_brand_new')   # 1 小时内不重复
    assert sent == []
    listener.handle_bot_added(db, cfg, C(), 'oc_group1')      # 白名单里的群不告警
    assert sent == []
