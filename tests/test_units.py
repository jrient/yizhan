import shutil
import subprocess

import pytest

from gateway.paths import REPO_ROOT, escape_inst, unescape_inst, unit

CN = '创视助手'
CN_ESC = r'\xe5\x88\x9b\xe8\xa7\x86\xe5\x8a\xa9\xe6\x89\x8b'


def test_unit_names():
    """drama-auto 的服务名必须不变;中文名转义成 systemd 认的形式(2026-09-24 创视助手起不来)。"""
    assert unit('listener', 'drama-auto') == 'yizhan-listener@drama-auto.service'
    assert unit('dispatcher', 'drama-auto', 'timer') == 'yizhan-dispatcher@drama-auto.timer'
    assert unit('listener', CN) == f'yizhan-listener@{CN_ESC}.service'


@pytest.mark.parametrize('inst', ['drama-auto', CN, 'a b', 'x.y_z:1', 'mix-创视_2'])
def test_roundtrip(inst):
    assert unescape_inst(escape_inst(inst)) == inst


def test_systemd_accepts_and_passes_escaped_name():
    """systemctl 认这个名字,且不再报 Invalid unit name。"""
    if not shutil.which('systemd-escape'):
        pytest.skip('没有 systemd-escape')
    # 非 ASCII 部分与 systemd-escape 一致
    want = subprocess.run(['systemd-escape', '--', CN], capture_output=True, text=True).stdout.strip()
    assert escape_inst(CN) == want


def test_unit_files_keep_percent_i():
    """ExecStart 保持 %i:改成 %I 会把 drama-auto 反转义成 drama/auto。"""
    for name in ('yizhan-listener@.service', 'yizhan-dispatcher@.service'):
        text = (REPO_ROOT / 'systemd' / name).read_text()
        exec_line = [l for l in text.splitlines() if l.startswith('ExecStart=')][0]
        assert exec_line.endswith(' %i'), exec_line
