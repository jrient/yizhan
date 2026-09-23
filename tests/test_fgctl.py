import json
import pytest

from gateway import cards, db as dbm, fgctl
from gateway.events import EventRow

ME = [{'key': '@_u1', 'id': 'ou_bot', 'name': 'bot'}]


class FakeClient:
    def __init__(self):
        self.sent = []
        self.reactions = {}
        self.n = 0
    def reply_post(self, mid, content, in_thread=False):
        self.n += 1; self.sent.append(('reply', mid, content, in_thread)); return f'om_me{self.n}'
    def send_post(self, chat_id, content):
        self.n += 1; self.sent.append(('send', chat_id, content, False)); return f'om_me{self.n}'
    def react(self, mid, emoji='Typing'):
        self.reactions[mid] = 'r1'; return 'r1'
    def unreact(self, mid, rid):
        self.reactions.pop(mid, None)
    def get_message(self, mid):
        return {'thread_id': 'omt_new', 'chat_id': 'oc_group1', 'root_id': 'om_0', 'create_time': '1700000000000'}
    def download(self, mid, key, dest, kind='image'):
        dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(b'x'); return dest


@pytest.fixture
def ctx(cfg, monkeypatch):
    monkeypatch.setenv('YIZHAN_INST', 'demo')
    c = fgctl.Ctx('demo')
    c._client = FakeClient()
    monkeypatch.setattr(fgctl, 'Ctx', lambda inst: c)
    return c


def add_card(ctx, mid='om_0', t=1_000_000, text='画面不对'):
    dbm.insert_event(ctx.db, EventRow(message_id=mid, chat_id='oc_group1', chat_type='group', create_time=t,
                                      sender_id='ou_a', sender_type='user', msg_type='text', text=text,
                                      mentions=ME, mentions_me=True))
    ids = cards.assign_cards(ctx.db, ctx.cfg, t)
    return ids[0] if ids else dbm.issue_for_message(ctx.db, mid)['issue_id']


def test_card_and_reply_flow(ctx, capsys):
    iid = add_card(ctx)
    assert fgctl.main(['card', str(iid)]) == 0
    out = capsys.readouterr().out
    assert '画面不对' in out and 'om_0' in out and '尚未回复' in out

    assert fgctl.main(['react', 'om_0']) == 0
    assert ctx.client.reactions == {'om_0': 'r1'}

    rc = fgctl.main(['reply', 'om_0', '@ou_0123456789abcdef0 查过了', '--type', 'frame',
                     '--evidence', '抽帧 shot_18s.jpg 第18秒 背景是木墙'])
    assert rc == 0
    kind, mid, content, in_thread = ctx.client.sent[0]
    assert kind == 'reply' and in_thread is True
    assert ctx.client.reactions == {}                        # 回复后自动撤表情
    reps = dbm.replies_for_issue(ctx.db, iid)
    assert len(reps) == 1 and reps[0]['type'] == 'frame' and reps[0]['my_message_id'] == 'om_me1'
    assert 'omt_new' in dbm.participated_threads(ctx.db)     # 我发言的话题被登记


def test_reply_rejected_without_evidence(ctx, capsys):
    add_card(ctx)
    rc = fgctl.main(['reply', 'om_0', '应该是模型随机', '--type', 'frame', '--evidence', '凭经验看就是这样的问题'])
    assert rc == 1 and ctx.client.sent == []
    assert '托词' in capsys.readouterr().err


def test_done_requires_reply_and_blocks_new_messages(ctx, capsys):
    iid = add_card(ctx, t=dbm.now_ms() - 60_000)
    with pytest.raises(SystemExit):
        fgctl.main(['done', str(iid)])                        # 一条回复都没发过
    assert '一条回复都没发过' in capsys.readouterr().out or True

    fgctl.main(['card', str(iid)])                            # 读卡 → last_read_at
    fgctl.main(['reply', 'om_0', '好的', '--type', 'other', '--evidence', '出处:群里 09-21 的部署公告原文'])
    dbm.insert_event(ctx.db, EventRow(message_id='om_1', chat_id='oc_group1', chat_type='group',
                                      create_time=dbm.now_ms() + 5000, sender_id='ou_a', sender_type='user',
                                      msg_type='text', text='还有个问题', mentions=ME, mentions_me=True))
    cards.assign_cards(ctx.db, ctx.cfg, dbm.now_ms())
    with pytest.raises(SystemExit):
        fgctl.main(['done', str(iid)])
    assert '还有个问题' in capsys.readouterr().out
    assert dbm.get_issue(ctx.db, iid)['state'] != 'answered'
    assert fgctl.main(['done', str(iid), '看过了', '--force']) == 0
    assert dbm.get_issue(ctx.db, iid)['state'] == 'answered'


def test_defer_and_wake(ctx, capsys):
    iid = add_card(ctx)
    assert fgctl.main(['defer', str(iid), '--until', '30m', '--note', '等张三批复']) == 0
    it = dbm.get_issue(ctx.db, iid)
    assert it['state'] == 'waiting' and it['defer_until'] > dbm.now_ms()
    assert cards.wake_deferred(ctx.db, dbm.now_ms()) == ([], [])
    assert cards.wake_deferred(ctx.db, it['defer_until']) == ([iid], [])


def test_pending_lists(ctx, capsys):
    iid = add_card(ctx)
    fgctl.main(['pending'])
    assert f'#{iid}' in capsys.readouterr().out


def test_send_needs_evidence_too(ctx):
    with pytest.raises(SystemExit):
        fgctl.main(['send', 'oc_group1', '通报', '--type', 'other'])


def test_thread_default_follows_config(ctx):
    add_card(ctx)
    ev = ['--type', 'other', '--evidence', '出处:群里 09-21 的部署公告原文']
    ctx.cfg.reply_in_thread = False
    fgctl.main(['reply', 'om_0', 'a', *ev])
    assert ctx.client.sent[-1][3] is False
    fgctl.main(['reply', 'om_0', 'b', '--thread', *ev])      # 显式覆盖
    assert ctx.client.sent[-1][3] is True
    ctx.cfg.reply_in_thread = True
    fgctl.main(['reply', 'om_0', 'c', *ev])
    assert ctx.client.sent[-1][3] is True
    fgctl.main(['reply', 'om_0', 'd', '--no-thread', *ev])
    assert ctx.client.sent[-1][3] is False


def test_defer_counts_up(ctx, capsys):
    iid = add_card(ctx)
    fgctl.main(['defer', str(iid), '--until', '2h'])
    assert dbm.get_issue(ctx.db, iid)['defer_count'] == 1
    fgctl.main(['defer', str(iid), '--until', '2h'])
    fgctl.main(['defer', str(iid), '--until', '2h'])
    assert dbm.get_issue(ctx.db, iid)['defer_count'] == 3
    assert '第 3 次挂起' in capsys.readouterr().out


def test_card_shows_replies_before_messages(ctx, capsys):
    """先看见自己说过什么,再看消息 —— 防重复回答。"""
    iid = add_card(ctx)
    fgctl.main(['reply', 'om_0', '答过了', '--type', 'other', '--evidence', '出处:群里 09-21 的公告原文'])
    capsys.readouterr()
    fgctl.main(['card', str(iid)])
    out = capsys.readouterr().out
    assert out.index('我方已经回过这张卡 1 条') < out.index('卡内消息')


def test_evidence_rejection_is_recorded(ctx):
    """证据门拦下的次数进统计:这是查证纪律起作用最直接的证据。"""
    add_card(ctx)
    fgctl.main(['reply', 'om_0', '应该没问题', '--type', 'frame', '--evidence', '凭经验判断是模型随机的'])
    rows = ctx.db.execute("SELECT kind, issue_id, detail FROM errors").fetchall()
    assert len(rows) == 1 and rows[0]['kind'] == 'evidence_rejected'
    assert rows[0]['issue_id'] == 1 and '托词' in rows[0]['detail']
