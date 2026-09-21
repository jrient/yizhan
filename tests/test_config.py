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


def test_default_config_dir_is_in_repo(monkeypatch):
    """配置默认放项目自己的 config/ 下,不再是 ~/.config/yizhan。"""
    monkeypatch.delenv('YIZHAN_CONFIG_DIR', raising=False)
    from pathlib import Path
    from gateway.paths import Paths, REPO_ROOT
    p = Paths('demo')
    assert p.config_file == REPO_ROOT / 'config' / 'demo.toml'
    assert (REPO_ROOT / 'gateway' / 'paths.py').exists()      # REPO_ROOT 指的确实是仓库根
    assert str(p.state_dir).startswith(str(Path.home()))      # 状态仍在用户目录
