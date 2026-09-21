import pytest
from gateway.evidence import check, EvidenceError


def bad(k, ev, text='正文', frag=''):
    with pytest.raises(EvidenceError) as e:
        check(k, ev, text)
    assert frag in str(e.value)


def test_gate():
    bad(None, 'x', frag='缺 --type')
    bad('bogus', 'x', frag='未知')
    bad('other', '', frag='缺 --evidence')
    bad('other', '无', frag='空的')
    bad('other', '凭经验判断这里肯定没问题', frag='托词')
    bad('other', '短证据', frag='太短')
    bad('frame', '看了视频,这段确实是背景不对没错', frag='帧文件名')
    bad('other', '出处是群里之前的讨论记录', text='已核实没有问题', frag='已核实')
    assert check('frame,time', '抽帧 out_18s.jpg 第18秒,date 输出 14:02', 'ok') == ('frame time', '抽帧 out_18s.jpg 第18秒,date 输出 14:02')
