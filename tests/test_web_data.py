"""只读聚合。健康判定是纯函数,单独测;其余灌假库测。"""
import json
import time

import pytest

from gateway import cards as cardsmod, db as dbm
from gateway.events import EventRow
from gateway.web import data

ME = [{'key': '@_u1', 'id': 'ou_bot', 'name': 'bot'}]


@pytest.fixture(autouse=True)
def no_systemctl(monkeypatch):
    """默认把 systemctl 打桩成「都在跑」,要测别的情况各用例自己改。"""
    calls = []
    def fake(args, timeout=10):
        calls.append(args)
        if args[0] == 'is-active':
            return 0, 'active'
        if args[0] == 'show':
            return 0, 'ExecMainStatus=0\nExecMainExitTimestamp=Mon 2026-09-21 14:00:00 CST\nResult=success'
        return 0, ''
    monkeypatch.setattr(data, 'run_systemctl', fake)
    return calls


def ev(conn, mid, at, text='q', sender='ou_a'):
    dbm.insert_event(conn, EventRow(message_id=mid, chat_id='oc_group1', chat_type='group', create_time=at,
                                    sender_id=sender, sender_type='user', msg_type='text', text=text,
                                    mentions=ME, mentions_me=True))


# ---- health 纯函数 ----
def test_health_green():
    assert data.health({'db_state': 'ok', 'listener_active': 'active', 'heartbeat_ago_s': 3})[0] == 'green'


def test_health_grey_and_broken():
    assert data.health({'db_state': 'missing'})[0] == 'grey'
    assert data.health({'db_state': 'broken'})[0] == 'red'


def test_health_red_cases():
    base = {'db_state': 'ok', 'listener_active': 'active', 'heartbeat_ago_s': 3}
    assert data.health({**base, 'listener_active': 'inactive'})[0] == 'red'
    assert data.health({**base, 'heartbeat_ago_s': 700})[0] == 'red'
    c, why = data.health({**base, 'stuck': 2})
    assert c == 'red' and '2 张' in why
    c, why = data.health({**base, 'dispatcher_rc': 1})
    assert c == 'red' and '探活' in why


def test_health_yellow_cases():
    base = {'db_state': 'ok', 'listener_active': 'active', 'heartbeat_ago_s': 3, 'dispatcher_rc': 0}
    assert data.health({**base, 'paused': True})[0] == 'yellow'
    c, why = data.health({**base, 'oldest_open_min': 45})
    assert c == 'yellow' and '45' in why


def test_health_red_beats_yellow():
    assert data.health({'db_state': 'ok', 'listener_active': 'inactive', 'paused': True})[0] == 'red'


# ---- 库读取 ----
def test_open_ro_missing_and_broken(cfg, tmp_path):
    assert data.open_ro(tmp_path / 'nope.db') is None
    bad = tmp_path / 'bad.db'
    bad.write_bytes(b'not a database at all, really not')
    with pytest.raises(data.DbBroken):
        data.open_ro(bad)


def test_open_ro_is_readonly(cfg, db):
    conn = data.open_ro(cfg.paths.db_file)
    import sqlite3
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO alerts(kind,sent_at) VALUES('x',1)")


# ---- 汇总 ----
def test_instance_summary_counts(cfg, db):
    now = dbm.now_ms()
    ev(db, 'om_1', now - 60_000)
    ev(db, 'om_2', now - 30_000)
    cardsmod.assign_cards(db, cfg, now)
    dbm.insert_reply(db, 1, 'om_1', 'om_me', 'reply', 'other', 'ev' * 10, '回了')
    dbm.insert_round(db, issue_id=1, session_id='s', started_at=now - 10_000, ended_at=now, rc=0,
                     context_tokens=40_000, output_tokens=100, cost_usd=0.5, outcome='answered')
    dbm.heartbeat_touch(db, now)
    s = data.instance_summary('demo')
    assert s['today_msgs'] == 2 and s['today_cards'] == 1 and s['today_replies'] == 1
    assert s['today_cost_usd'] == 0.5 and s['context_tokens'] == 40_000
    assert s['pending'] == 1 and s['health'] == 'green'
    assert s['units']['listener'] == 'active'


def test_instance_summary_no_db(cfg):
    cfg.paths.db_file.unlink(missing_ok=True)
    s = data.instance_summary('demo')
    assert s['db_state'] == 'missing' and s['health'] == 'grey' and s['today_msgs'] == 0


def test_instances_one_bad_does_not_break_others(cfg, db, inst_env, monkeypatch):
    (inst_env / 'config' / 'broken.toml').write_text('app_id = "x"\n')      # 缺必填字段
    out = {r['inst']: r for r in data.instances()}
    assert set(out) == {'demo', 'broken'}
    assert out['demo']['health'] == 'green'
    assert out['broken']['health'] == 'grey' and '配置' in out['broken']['health_reason']


def test_list_instances_skips_web_and_example(inst_env, cfg):
    (inst_env / 'config' / 'web.toml').write_text('password="x"\n')
    (inst_env / 'config' / 'example.toml').write_text('\n')
    assert data.list_instances() == ['demo']


# ---- 详情 ----
def test_overview_hourly_and_states(cfg, db):
    now = dbm.now_ms()
    ev(db, 'om_1', now - 60_000)
    cardsmod.assign_cards(db, cfg, now)
    o = data.overview('demo')
    assert o['inst'] == 'demo' and o['card_states'] == {'open': 1}
    assert len(o['hourly']) >= 1 and sum(h['msgs'] for h in o['hourly']) == 1
    assert o['quiet_seconds'] == 30


def test_cards_list_and_first_reply(cfg, db):
    now = dbm.now_ms()
    ev(db, 'om_1', now - 120_000)
    cardsmod.assign_cards(db, cfg, now)
    iid = 1
    dbm.insert_reply(db, iid, 'om_1', 'om_me', 'reply', 'frame', 'e' * 20, '回复正文')
    out = data.cards('demo')
    assert out['total'] == 1
    it = out['items'][0]
    assert it['id'] == iid and it['n_msgs'] == 1 and it['state'] == 'open'
    assert 100 <= it['first_reply_s'] <= 140
    assert data.cards('demo', state='answered')['total'] == 0


def test_card_detail(cfg, db):
    now = dbm.now_ms()
    ev(db, 'om_1', now - 60_000, text='画面不对')
    db.execute("UPDATE events SET image_keys=? WHERE message_id='om_1'", (json.dumps(['img_k1']),))
    cardsmod.assign_cards(db, cfg, now)
    dbm.insert_reply(db, 1, 'om_1', 'om_me', 'reply', 'frame', '抽帧 a.jpg 第18秒', '查过了')
    d = data.card('demo', 1)
    assert d['messages'][0]['text'] == '画面不对'
    assert d['messages'][0]['images'] == ['/api/i/demo/attach/img_k1']
    assert d['replies'][0]['evidence'].startswith('抽帧')
    with pytest.raises(KeyError):
        data.card('demo', 999)


def test_session_view(cfg, db):
    sid = dbm.session_rotate(db, 'init')
    now = dbm.now_ms()
    for i, (ctx, cost) in enumerate([(10_000, 0.1), (20_000, 0.2)]):
        dbm.insert_round(db, issue_id=i + 1, session_id=sid, started_at=now - 1000 * (2 - i), ended_at=now,
                         rc=0, context_tokens=ctx, output_tokens=5, cost_usd=cost, outcome='answered')
    s = data.session('demo')
    assert s['session']['session_id'] == sid
    assert [r['context_tokens'] for r in s['rounds']] == [10_000, 20_000]
    assert s['total_cost_usd'] == 0.3 and s['context_tokens'] == 20_000
    assert s['rotations'][0]['session_id'] == sid


def test_attachment_path_rejects_traversal(cfg):
    p = data.attachment_path('demo', '../../etc/passwd')
    assert p.parent == cfg.paths.attachments.resolve()
    assert '..' not in p.name and p.name == 'etcpasswd'
    assert data.attachment_path('demo', 'img_v3_ab-1.png').name == 'img_v3_ab-1.png'
    with pytest.raises(ValueError):
        data.attachment_path('demo', '../..')


def test_timer_next_falls_back_to_monotonic(cfg, monkeypatch):
    """相对定时器只填单调时钟字段,实时字段是空的。"""
    import time as _t
    def fake(args, timeout=10):
        if args[0] == 'show' and 'timer' in args[1]:
            up = float(open('/proc/uptime').read().split()[0])
            soon = int((up + 30) * 1e6)
            return 0, f'NextElapseUSecRealtime=\nNextElapseUSecMonotonic={soon}'
        return 0, 'active'
    monkeypatch.setattr(data, 'run_systemctl', fake)
    out = data.timer_next('demo')
    assert out and len(out) == 8            # HH:MM:SS


def test_timer_next_none_when_infinity(cfg, monkeypatch):
    monkeypatch.setattr(data, 'run_systemctl',
                        lambda args, timeout=10: (0, 'NextElapseUSecRealtime=\nNextElapseUSecMonotonic=infinity'))
    assert data.timer_next('demo') is None


def test_overview_reports_dispatcher_active(cfg, db, monkeypatch):
    monkeypatch.setattr(data, 'run_systemctl',
                        lambda args, timeout=10: (0, 'activating') if args[0] == 'is-active' else (0, ''))
    o = data.overview('demo')
    assert o['dispatcher_active'] is True and o['timer_next'] is None


def test_labels_and_preview(cfg, db):
    assert data.state_label('answered') == '已答复' and data.state_label('stuck') == '卡死'
    assert data.alert_label('probe_failed') == 'claude 连不上'
    assert data.alert_label('stuck:7') == '卡 #7 卡死'
    assert data._preview('@机器人 @小白 第18秒\n背景不对') == '第18秒 背景不对'
    assert data._preview('字' * 80).endswith('…')


def test_cards_have_preview_source_and_reply_count(cfg, db):
    now = dbm.now_ms()
    ev(db, 'om_1', now - 60_000, text='@bot 圣狮第十六集第18秒背景是木墙')
    cardsmod.assign_cards(db, cfg, now)
    dbm.insert_reply(db, 1, 'om_1', 'om_me', 'reply', 'frame', '抽帧 a.jpg 第18秒', '查过了')
    dbm.insert_reply(db, 1, 'om_1', 'om_me2', 'reply', 'frame', '抽帧 b.jpg 第20秒', '补充')
    it = data.cards('demo')['items'][0]
    assert it['preview'] == '圣狮第十六集第18秒背景是木墙'
    assert it['source'] == '群' and it['n_replies'] == 2 and it['state_label'] == '待处理'
    assert it['last_reply_at'] is not None


def test_wall_hides_stale_alerts_and_shows_activity(cfg, db):
    now = dbm.now_ms()
    db.execute("INSERT INTO alerts(kind,sent_at,text) VALUES('probe_failed',?, '旧的')", (now - 2 * 86400_000,))
    ev(db, 'om_1', now - 60_000)
    s = data.instance_summary('demo')
    assert s['last_alert'] is None                    # 两天前的告警不再挂在卡片墙上
    assert s['last_msg_at'] == now - 60_000
    db.execute("INSERT INTO alerts(kind,sent_at,text) VALUES('stuck:3',?, '卡死了')", (now - 60_000,))
    s = data.instance_summary('demo')
    assert s['last_alert']['label'] == '卡 #3 卡死' and s['last_alert']['text'] == '卡死了'


def test_overview_alerts_and_states_are_labelled(cfg, db):
    now = dbm.now_ms()
    db.execute("INSERT INTO alerts(kind,sent_at,text) VALUES('listener_stale',?, 'x')", (now,))
    ev(db, 'om_1', now - 60_000)
    cardsmod.assign_cards(db, cfg, now)
    o = data.overview('demo')
    assert o['alerts'][0]['label'] == '监听心跳中断'
    assert o['card_state_list'] == [{'state': 'open', 'label': '待处理', 'n': 1}]


def test_stats_daily_buckets_fill_zero_days(cfg, db):
    now = dbm.now_ms()
    ev(db, 'om_1', now - 60_000)
    cardsmod.assign_cards(db, cfg, now)
    dbm.insert_reply(db, 1, 'om_1', 'om_me', 'reply', 'frame config', 'e' * 20, '回了')
    dbm.insert_round(db, issue_id=1, session_id='s', started_at=now - 5000, ended_at=now, rc=0,
                     context_tokens=1000, output_tokens=10, cost_usd=1.25, outcome='answered')
    dbm.record_error(db, 'probe_failed', 'x', at=now - 86400_000)
    dbm.record_error(db, 'evidence_rejected', 'y', at=now)
    s = data.stats('demo', 7)
    assert len(s['daily']) == 7 and s['daily'][-1]['msgs'] == 1       # 没数据的日子也在,补 0
    assert s['kpis']['cost_usd'] == 1.25 and s['kpis']['errors'] == 2
    assert s['kpis']['evidence_rejected'] == 1 and s['kpis']['answered_rate'] == 0.0
    kinds = [e['kind'] for e in s['errors_by_kind']]
    assert kinds == ['probe_failed', 'evidence_rejected']               # 固定顺序,不按数量排
    assert {r['type'] for r in s['reply_types']} == {'frame', 'config'} # 多类型拆开计
    assert s['senders'][0]['n'] == 1 and s['context'][0]['context_tokens'] == 1000


def test_stats_percentiles():
    assert data._pct([], 0.5) is None
    assert data._pct([10, 20, 30, 40], 0.5) == 25.0
    assert data._pct([100], 0.9) == 100.0


def test_stats_clamps_range(cfg, db):
    assert data.stats('demo', 999)['range']['days'] == 90
    assert data.stats('demo', 0)['range']['days'] == 1


def test_attachment_falls_back_to_png(cfg):
    """fgctl 存成 <key>.png,卡详情链接用裸 key。原来图片全部 404。"""
    cfg.paths.attachments.mkdir(parents=True, exist_ok=True)
    (cfg.paths.attachments / 'img_k1.png').write_bytes(b'png')
    assert data.attachment_path('demo', 'img_k1').name == 'img_k1.png'
    assert data.attachment_path('demo', 'img_k1').exists()


def test_perm_denied_alert_is_labelled_with_card():
    assert data.alert_label('perm_denied:18') == '卡 #18 权限拒绝'


def test_overview_now_fields(cfg, db):
    now = dbm.now_ms()
    ev(db, 'om_1', now - 120_000)
    cardsmod.assign_cards(db, cfg, now - 110_000)
    iid = db.execute('SELECT issue_id FROM issues').fetchone()[0]
    db.execute("INSERT INTO replies(issue_id,my_message_id,sent_at) VALUES(?,?,?)", (iid, 'om_r', now - 60_000))
    dbm.insert_round(db, issue_id=iid, session_id='s', started_at=now - 100_000, ended_at=now,
                     rc=0, context_tokens=400_000, output_tokens=1, cost_usd=0.1, outcome='answered',
                     context_window=1_000_000)
    o = data.overview('demo')
    assert [c['id'] for c in o['recent_cards']] == [iid]
    assert o['current_card'] is None and o['stuck_cards'] == []
    assert o['summary']['context_window'] == 1_000_000
    assert o['summary']['today_first_reply_p50_s'] is not None
    db.execute("UPDATE issues SET state='answering'")
    assert data.overview('demo')['current_card']['id'] == iid
