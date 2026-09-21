from gateway.render import post_content, code_post_content, count_ats


def test_bold_split():
    c = post_content('a **b** c')
    assert c == [[{'tag': 'text', 'text': 'a '}, {'tag': 'text', 'text': 'b', 'style': ['bold']}, {'tag': 'text', 'text': ' c'}]]


def test_table_to_code_block():
    c = post_content('前言\n| 列一 | b |\n|---|---|\n| 甲 | 2 |\n后记')
    assert c[0] == [{'tag': 'text', 'text': '前言'}]
    assert c[1][0]['tag'] == 'code_block' and '列一' in c[1][0]['text'] and '----' in c[1][0]['text']
    assert c[2] == [{'tag': 'text', 'text': '后记'}]


def test_at_name_and_openid():
    c = post_content('@小白（李四） 看这 @ou_0123456789abcdef0 @无名', {'小白（李四）': 'ou_bai'})
    line = c[0]
    assert line[0] == {'tag': 'at', 'user_id': 'ou_bai'}
    assert {'tag': 'at', 'user_id': 'ou_0123456789abcdef0'} in line
    assert count_ats(c) == 2
    assert any(e.get('text', '').startswith('@') or e.get('text') == '@' for e in line)


def test_short_name_form():
    c = post_content('@小白 好', {'小白（李四）': 'ou_bai'})
    assert c[0][0] == {'tag': 'at', 'user_id': 'ou_bai'}


def test_code_post():
    c = code_post_content('@小白 稿子', 'print(1)', {'小白': 'ou_bai'})
    assert c[0][0] == {'tag': 'at', 'user_id': 'ou_bai'} and c[1][0]['tag'] == 'code_block'
