import json
from gateway.feishu import FeishuClient


class FakeResp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status
        self.text = json.dumps(payload); self.content = b''
    def json(self):
        return self._p


def make(responses):
    c = FeishuClient('a', 'b')
    c._token, c._token_exp = 'tok', 9e12
    calls = []
    def request(method, url, **kw):
        calls.append((method, url, kw))
        return responses.pop(0)
    c.http.request = request
    c.sleep = lambda s: calls.append(('sleep', s))
    return c, calls


def test_reply_payload_and_thread():
    c, calls = make([FakeResp({'code': 0, 'data': {'message_id': 'om_me'}})])
    mid = c.reply_post('om_1', [[{'tag': 'text', 'text': 'hi'}]], in_thread=True)
    assert mid == 'om_me'
    body = calls[0][2]['json']
    assert body['reply_in_thread'] is True and body['msg_type'] == 'post'
    assert json.loads(body['content'])['zh_cn']['content'][0][0]['text'] == 'hi'


def test_rate_limit_backoff():
    # /bot/v3/info 的 bot 在响应顶层,不在 data 下 —— 假数据要跟真接口一致
    c, calls = make([FakeResp({'code': 99991400, 'msg': 'limit'}), FakeResp({'code': 0, 'bot': {'open_id': 'ou_bot'}})])
    assert c.bot_open_id() == 'ou_bot'
    assert ('sleep', 1.0) in calls


def test_bot_open_id_reads_top_level_bot():
    """回归:曾把 bot 当成在 data 下,取到空串,群消息的 @我 判定因此全部失效。"""
    c, _ = make([FakeResp({'code': 0, 'bot': {'open_id': 'ou_bot'}})])
    assert c.bot_open_id() == 'ou_bot'
    # 真接口没有 data 字段,若哪天又回去读 data,这里会退回空串
    c2, _ = make([FakeResp({'code': 0, 'data': {'bot': {'open_id': 'ou_bot'}}})])
    assert c2.bot_open_id() == ''


def test_list_paginates():
    c, calls = make([FakeResp({'code': 0, 'data': {'items': [{'a': 1}], 'has_more': True, 'page_token': 'p2'}}),
                     FakeResp({'code': 0, 'data': {'items': [{'a': 2}], 'has_more': False}})])
    items = c.list_messages('chat', 'oc_1', start_ms=1_700_000_000_000)
    assert [i['a'] for i in items] == [1, 2]
    assert calls[0][2]['params']['start_time'] == '1700000000'
    assert calls[1][2]['params']['page_token'] == 'p2'
