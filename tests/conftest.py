import os
import pytest


@pytest.fixture
def inst_env(tmp_path, monkeypatch):
    monkeypatch.setenv('YIZHAN_CONFIG_DIR', str(tmp_path / 'config'))
    monkeypatch.setenv('YIZHAN_STATE_DIR', str(tmp_path / 'state'))
    (tmp_path / 'config').mkdir()
    return tmp_path


@pytest.fixture
def cfg(inst_env, tmp_path):
    proj = tmp_path / 'proj'; proj.mkdir()
    (inst_env / 'config' / 'demo.toml').write_text(f'''
app_id = "cli_test"
app_secret = "sec"
project_dir = "{proj}"
alert_open_id = "ou_admin"
p2p_allow = ["ou_boss", "ou_admin"]
group_allow = ["oc_group1"]
quiet_seconds = 30
''')
    from gateway import config
    c = config.load('demo')
    c.paths.ensure()
    return c


@pytest.fixture
def db(cfg):
    from gateway import db as dbm
    conn = dbm.connect(cfg.paths.db_file)
    yield conn
    conn.close()
