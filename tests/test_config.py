import pytest
from gateway import config


def test_load(cfg):
    assert cfg.app_id == 'cli_test'
    assert cfg.group_allow == ['oc_group1']
    assert cfg.quiet_seconds == 30
    assert cfg.max_redispatch == 3
    assert cfg.paths.db_file.name == 'state.db'


def test_missing_required(inst_env):
    (inst_env / 'config' / 'bad.toml').write_text('app_id = "x"\n')
    with pytest.raises(config.ConfigError):
        config.load('bad')


def test_missing_file(inst_env):
    with pytest.raises(config.ConfigError):
        config.load('nope')
