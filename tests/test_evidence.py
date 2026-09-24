import pytest
from gateway.evidence import check, EvidenceError
from gateway import evidence


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


@pytest.mark.parametrize('ev', [
    # 2026-09-24 创视助手卡 #5 被误拒的两条真实证据
    '库里 generation_tasks 近3天 provider=ctyun 按小时计数 09时39/10时86/11时49,最新行 id 249839 model cdance2.5-0807',
    'seedance-advisor/SKILL.md 第3行写「天翼云 task_xxx」;库里任务249839(provider=ctyun)为最新一条',
    '任务 id: 249803 的 request_payload',
])
def test_config_accepts_real_row_and_line_refs(ev):
    assert evidence.check('config', ev, '正文')[0] == 'config'


@pytest.mark.parametrize('ev', ['我看了一下后台配置是这样的没错', '后台页面上显示的就是这个版本号', '库里就是这样的配置没有问题'])
def test_config_still_rejects_vague(ev):
    with pytest.raises(evidence.EvidenceError):
        evidence.check('config', ev, '正文')


def test_rejection_shows_accepted_examples():
    """拒绝提示要直接给能过的写法,机器人不用去翻源码(卡 #5 它去读 fgctl 被拦)。"""
    with pytest.raises(evidence.EvidenceError) as e:
        evidence.check('config', '我看了一下后台配置是这样的没错', '正文')
    assert '例如' in str(e.value)
