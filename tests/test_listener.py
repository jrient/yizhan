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


def test_trigger_uses_no_block(monkeypatch):
    seen = {}
    def fake_run(argv, **kw):
        seen['argv'] = argv; seen['timeout'] = kw.get('timeout')
        class R: pass
        return R()
    monkeypatch.setattr(listener.subprocess, 'run', fake_run)
    listener.trigger_dispatcher('demo')
    # oneshot 的 start 会阻塞到跑完,必须 --no-block,否则 listener 期间收不了事件
    assert '--no-block' in seen['argv'] and seen['argv'][-1] == 'yizhan-dispatcher@demo.service'


def test_heartbeat_ticks_without_messages(cfg, db, monkeypatch):
    """群里安静时也要打心跳,否则 watchdog 会误判 listener 死了并重启它。"""
    import threading
    dbm.heartbeat_touch(db, 1000)
    stop = threading.Event()
    _, t = listener.start_heartbeat(cfg.paths.db_file, stop, period=0.02)
    for _ in range(100):
        if dbm.heartbeat_get(db) > 1000:
            break
        __import__('time').sleep(0.02)
    stop.set(); t.join(timeout=2)
    assert dbm.heartbeat_get(db) > 1000


class MemberClient:
    def __init__(self, names): self.names, self.calls = names, 0
    def chat_members(self, chat_id):
        self.calls += 1
        return self.names
    def send_text(self, *a): return 'om'


def test_unknown_sender_triggers_member_sync(db, cfg):
    """飞书事件只带 open_id,没被 @ 过的人原来一直显示成 ou_xxx。"""
    listener._member_sync_at.clear()
    c = MemberClient({'ou_a': '张三（张三）', 'ou_b': '小白'})
    listener.handle_message(db, cfg, ev('om_1'), 'ou_bot', None, client=c)
    assert dbm.member_name(db, 'ou_a') == '张三（张三）' and c.calls == 1
    listener.handle_message(db, cfg, ev('om_2', sender_id='ou_new'), 'ou_bot', None, client=c)
    assert c.calls == 1                       # 10 分钟冷却,不会每条消息都拉


def test_known_sender_does_not_sync(db, cfg):
    listener._member_sync_at.clear()
    dbm.insert_event(db, ev('om_0', mentions=[{'key': 'k', 'id': 'ou_a', 'name': '已知'}]))
    c = MemberClient({})
    listener.handle_message(db, cfg, ev('om_1'), 'ou_bot', None, client=c)
    assert c.calls == 0


def test_bot_added_syncs_members(db, cfg):
    listener._member_sync_at.clear()
    c = MemberClient({'ou_x': '新同事'})
    listener.handle_bot_added(db, cfg, c, 'oc_group1')
    assert dbm.member_name(db, 'ou_x') == '新同事'


def test_after_cursor():
    assert listener._after(0) is None
    assert listener._after(1000) == 1001


class ListClient:
    def __init__(self, items): self.items = items
    def list_messages(self, *a, **k): return self.items
    def send_text(self, *a): return 'om'


def _api(mid, t, app=False):
    return {'message_id': mid, 'create_time': str(t),
            'sender': {'sender_type': 'app' if app else 'user', 'id': 'ou_x'}}


def test_ws_missing_detects_messages_not_in_db(db, cfg):
    """心跳只能证明进程活着;对账才能证明消息确实在进来。"""
    now = dbm.now_ms()
    dbm.insert_event(db, ev('om_have', create_time=now - 5 * 60_000))
    c = ListClient([
        _api('om_have', now - 5 * 60_000),        # 已入库
        _api('om_lost', now - 6 * 60_000),        # 漏了
        _api('om_bot', now - 6 * 60_000, app=True),  # 机器人自己的,本来就不入库
        _api('om_fresh', now - 30_000),           # 刚发的,还在推送延迟余量内
        _api('om_old', now - 3 * 3600_000),       # 太久以前,不在对账窗口
        {'message_id': 'om_sys', 'create_time': str(now - 6 * 60_000), 'msg_type': 'system',
         'sender': {'sender_type': '', 'id': ''}},  # 进群通知,本来就不进库
    ])
    assert listener.ws_missing(db, c, ['oc_group1'], now) == ['om_lost']


def test_ws_watchdog_exits_on_missed_messages(cfg, db):
    import threading
    now = dbm.now_ms()
    exited = threading.Event()
    stop, t = listener.start_ws_watchdog(cfg, cfg.paths.db_file, ListClient([_api('om_lost', now - 6 * 60_000)]),
                                         period=0.02, exit_fn=exited.set)
    assert exited.wait(3)
    stop.set(); t.join(timeout=2)
    conn = dbm.connect(cfg.paths.db_file)
    assert conn.execute("SELECT COUNT(*) FROM errors WHERE kind='feishu_error'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM alerts WHERE kind='ws_missed'").fetchone()[0] == 1


def test_ws_watchdog_quiet_when_all_present(cfg, db):
    import threading, time as _t
    exited = threading.Event()
    stop, t = listener.start_ws_watchdog(cfg, cfg.paths.db_file, ListClient([]), period=0.02, exit_fn=exited.set)
    _t.sleep(0.2)
    stop.set(); t.join(timeout=2)
    assert not exited.is_set()
