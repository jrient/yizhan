"""写操作。systemctl 一律打桩,不真动服务。"""
import tomllib

import pytest

from gateway import config as configmod, db as dbm
from gateway.web import control


@pytest.fixture
def sysctl(monkeypatch):
    calls = []
    def fake(args, capture_output=True, text=True, timeout=None, check=False):
        calls.append(list(args))
        class R:
            returncode = 0
            stdout = ''
            stderr = ''
        return R()
    monkeypatch.setattr(control.subprocess, 'run', fake)
    monkeypatch.setattr(control.cli.subprocess, 'run', fake)
    return calls


# ---- 动作 ----
def test_pause_and_resume(cfg, sysctl):
    r = control.action('demo', 'pause')
    assert r['ok'] and cfg.paths.paused_file.exists()
    assert ['systemctl', '--user', 'stop', 'yizhan-dispatcher@demo.timer'] in sysctl
    r = control.action('demo', 'resume')
    assert r['ok'] and not cfg.paths.paused_file.exists()
    assert ['systemctl', '--user', 'start', 'yizhan-dispatcher@demo.timer'] in sysctl


def test_restart_and_dispatch_now(cfg, sysctl):
    assert control.action('demo', 'restart-listener')['ok']
    assert ['systemctl', '--user', 'restart', 'yizhan-listener@demo'] in sysctl
    assert control.action('demo', 'dispatch-now')['ok']
    assert ['systemctl', '--user', '--no-block', 'start', 'yizhan-dispatcher@demo.service'] in sysctl


def test_rotate(cfg, sysctl):
    r = control.action('demo', 'rotate')
    assert r['ok']
    conn = dbm.connect(cfg.paths.db_file)
    assert dbm.session_get(conn)['reason'] == 'manual(web)'


def test_unknown_action(cfg, sysctl):
    r = control.action('demo', 'drop-everything')
    assert not r['ok'] and '不认识' in r['message']


def test_systemctl_failure_surfaces(cfg, monkeypatch):
    def boom(args, **kw):
        class R:
            returncode = 1; stdout = ''; stderr = 'Unit not found.'
        return R()
    monkeypatch.setattr(control.subprocess, 'run', boom)
    r = control.action('demo', 'restart-listener')
    assert not r['ok'] and 'Unit not found' in r['message']


def test_systemctl_timeout(cfg, monkeypatch):
    def slow(args, **kw):
        raise control.subprocess.TimeoutExpired(args, 20)
    monkeypatch.setattr(control.subprocess, 'run', slow)
    r = control.action('demo', 'dispatch-now')
    assert not r['ok'] and '超过' in r['message']


def test_reopen_card_only_when_stuck(cfg, sysctl):
    conn = dbm.connect(cfg.paths.db_file)
    iid = dbm.create_issue(conn, 'oc_group1', None, 'ou_a', 'k', dbm.now_ms())
    conn.close()
    r = control.action('demo', 'reopen-card', card_id=iid)
    assert not r['ok'] and 'stuck' in r['message']
    conn = dbm.connect(cfg.paths.db_file)
    dbm.update_issue(conn, iid, state='stuck', redispatch=3)
    conn.close()
    r = control.action('demo', 'reopen-card', card_id=iid)
    assert r['ok']
    conn = dbm.connect(cfg.paths.db_file)
    it = dbm.get_issue(conn, iid)
    assert it['state'] == 'open' and it['redispatch'] == 0
    assert not control.action('demo', 'reopen-card', card_id=999)['ok']


# ---- 配置 ----
def test_config_read_masks_secret(cfg):
    out = control.config_read('demo')
    assert out['app_id'] == 'cli_test'
    assert out['app_secret'].startswith(control.MASK) and 'sec' not in out['app_secret']
    assert out['quiet_seconds'] == 30 and out['group_allow'] == ['oc_group1']


def test_config_write_roundtrip(cfg):
    r = control.config_write('demo', {'quiet_seconds': '15', 'reply_in_thread': 'false',
                                      'group_allow': 'oc_a\noc_b',
                                      'claude_env': 'https_proxy = http://127.0.0.1:7890'})
    assert r['ok'] and r['errors'] == {}
    c2 = configmod.load('demo')
    assert c2.quiet_seconds == 15 and c2.reply_in_thread is False
    assert c2.group_allow == ['oc_a', 'oc_b']
    assert c2.claude_env == {'https_proxy': 'http://127.0.0.1:7890'}
    assert c2.app_secret == 'sec'                      # 没提交就保持原值


def test_config_write_keeps_secret_when_masked(cfg):
    masked = control.config_read('demo')['app_secret']
    control.config_write('demo', {'app_secret': masked})
    assert configmod.load('demo').app_secret == 'sec'
    control.config_write('demo', {'app_secret': 'brand-new-secret'})
    assert configmod.load('demo').app_secret == 'brand-new-secret'


def test_config_write_rejects_bad_values_and_keeps_file(cfg):
    before = cfg.paths.config_file.read_text()
    r = control.config_write('demo', {'quiet_seconds': '半分钟'})
    assert not r['ok'] and 'quiet_seconds' in r['errors']
    assert cfg.paths.config_file.read_text() == before
    r = control.config_write('demo', {'project_dir': '/nope/nope'})
    assert not r['ok'] and r['errors']['project_dir'] == '目录不存在'
    r = control.config_write('demo', {'app_id': ''})
    assert not r['ok'] and r['errors']['app_id'] == '必填'
    assert cfg.paths.config_file.read_text() == before


def test_config_write_reports_needs_restart(cfg):
    r = control.config_write('demo', {'group_allow': 'oc_x'})
    assert r['ok'] and r['needs_restart'] == ['listener']
    r = control.config_write('demo', {'quiet_seconds': '31'})
    assert r['ok'] and r['needs_restart'] == []


def test_config_write_is_atomic_no_tmp_left(cfg):
    control.config_write('demo', {'quiet_seconds': '20'})
    assert not list(cfg.paths.config_dir.glob('*.tmp'))
    assert not list(cfg.paths.config_dir.glob('*.bak'))


def test_dump_toml_parses_back():
    text = control.dump_toml({'app_id': 'a"b', 'quiet_seconds': 5, 'reply_in_thread': True,
                              'p2p_allow': ['ou_1', 'ou_2'], 'claude_env': {'k': 'v'}})
    back = tomllib.loads(text)
    assert back == {'app_id': 'a"b', 'quiet_seconds': 5, 'reply_in_thread': True,
                    'p2p_allow': ['ou_1', 'ou_2'], 'claude_env': {'k': 'v'}}


# ---- 向导 ----
def test_wizard_init(inst_env, tmp_path, sysctl):
    proj = tmp_path / 'wproj'; proj.mkdir()
    r = control.wizard_init('neo', str(proj))
    assert r['ok'] and r['skill'] == 'neo-feedback-group-reply'
    from gateway.paths import Paths
    assert Paths('neo').config_file.exists() and Paths('neo').workspace.is_dir()
    assert not control.wizard_init('neo', str(proj))['ok']            # 重名
    assert not control.wizard_init('bad name!', str(proj))['ok']
    assert not control.wizard_init('neo2', '/nope')['ok']


def test_wizard_verify_reports_each_check(cfg, monkeypatch):
    class FakeClient:
        def __init__(self, *a): pass
        def token(self): return 't'
        def bot_open_id(self): return ''
    monkeypatch.setattr(control, 'FeishuClient', FakeClient)
    monkeypatch.setattr(control, 'probe', lambda c: (False, 'rc=1 403'))
    out = control.wizard_verify('demo', {'quiet_seconds': '30'})
    assert out['token']['ok'] and not out['bot']['ok'] and not out['probe']['ok']
    assert '长连接' in out['bot']['message'] and 'https_proxy' in out['probe']['message']
    assert out['ok'] is False


def test_wizard_verify_all_green(cfg, monkeypatch):
    class FakeClient:
        def __init__(self, *a): pass
        def token(self): return 't'
        def bot_open_id(self): return 'ou_bot'
    monkeypatch.setattr(control, 'FeishuClient', FakeClient)
    monkeypatch.setattr(control, 'probe', lambda c: (True, 'ok'))
    out = control.wizard_verify('demo', {})
    assert out['ok'] and out['bot']['open_id'] == 'ou_bot'


def test_wizard_verify_bad_credentials(cfg, monkeypatch):
    from gateway.feishu import FeishuError
    class FakeClient:
        def __init__(self, *a): pass
        def token(self): raise FeishuError(10003, 'invalid app_id')
    monkeypatch.setattr(control, 'FeishuClient', FakeClient)
    out = control.wizard_verify('demo', {})
    assert out['ok'] is False and 'app_id' in out['token']['message']


def test_wizard_enable(cfg, sysctl):
    r = control.wizard_enable('demo')
    assert r['ok']
    assert ['systemctl', '--user', 'enable', '--now', 'yizhan-listener@demo'] in sysctl
    assert ['systemctl', '--user', 'enable', '--now', 'yizhan-dispatcher@demo.timer'] in sysctl
