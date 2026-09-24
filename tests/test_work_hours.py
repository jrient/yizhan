from datetime import datetime

import pytest

from gateway import config as configmod
from gateway.config import ConfigError, in_work_hours, parse_work_hours


def at(h, m=0):
    return datetime(2026, 9, 24, h, m, tzinfo=configmod.TZ)


def test_default_is_6_to_24():
    assert configmod.DEFAULT_WORK_HOURS == '06:00-24:00'


@pytest.mark.parametrize('spec,now,want', [
    ('06:00-24:00', at(5, 59), False),
    ('06:00-24:00', at(6, 0), True),
    ('06:00-24:00', at(23, 59), True),
    ('06:00-24:00', at(0, 0), False),
    ('22:00-06:00', at(23, 0), True),     # 跨午夜
    ('22:00-06:00', at(3, 0), True),
    ('22:00-06:00', at(6, 0), False),
    ('22:00-06:00', at(12, 0), False),
    ('', at(3, 0), True),                 # 留空 = 全天
    ('00:00-24:00', at(3, 0), True),
])
def test_in_work_hours(spec, now, want):
    assert in_work_hours(spec, now) is want


@pytest.mark.parametrize('bad', ['6-24', '06:00', '25:00-26:00', '06:00-06:00', 'abc'])
def test_bad_spec_rejected(bad):
    with pytest.raises(ValueError):
        parse_work_hours(bad)


def test_bad_spec_fails_config_load(inst_env, tmp_path):
    proj = tmp_path / 'p'; proj.mkdir()
    (inst_env / 'config' / 'x.toml').write_text(
        f'app_id="a"\napp_secret="b"\nproject_dir="{proj}"\nwork_hours="6-24"\n')
    with pytest.raises(ConfigError):
        configmod.load('x')


def test_missing_key_uses_default(inst_env, tmp_path):
    proj = tmp_path / 'p'; proj.mkdir()
    (inst_env / 'config' / 'y.toml').write_text(f'app_id="a"\napp_secret="b"\nproject_dir="{proj}"\n')
    assert configmod.load('y').work_hours == '06:00-24:00'
