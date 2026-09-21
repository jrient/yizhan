import json
from gateway.events import render_text, from_api_item


def test_text_mentions():
    t, imgs = render_text('text', json.dumps({'text': '@_user_1 你好 @_user_2'}),
                          [{'key': '@_user_1', 'id': 'ou_bot', 'name': '机器人'}, {'key': '@_user_2', 'id': 'ou_x', 'name': '小白'}])
    assert t == '@机器人 你好 @小白' and imgs == []


def test_post_with_image():
    c = json.dumps({'title': '标题', 'content': [[{'tag': 'text', 'text': '第一行'}], [{'tag': 'img', 'image_key': 'img_1'}]]})
    t, imgs = render_text('post', c, [])
    assert '标题' in t and '第一行' in t and imgs == ['img_1']


def test_api_item_mentions_me():
    item = {'message_id': 'om_1', 'chat_id': 'oc_1', 'create_time': '1700000000000', 'msg_type': 'text',
            'body': {'content': json.dumps({'text': '@_user_1 hi'})},
            'mentions': [{'key': '@_user_1', 'id': 'ou_bot', 'name': 'bot', 'id_type': 'open_id'}],
            'sender': {'id': 'ou_a', 'sender_type': 'user'}}
    e = from_api_item(item, 'group', 'ou_bot')
    assert e.mentions_me and e.text == '@bot hi' and e.create_time == 1700000000000
    assert not from_api_item(item, 'group', 'ou_other').mentions_me
