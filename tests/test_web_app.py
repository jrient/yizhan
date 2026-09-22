"""路由层。data / control 大多打桩,这里只验路由、鉴权、CSRF、状态码。"""
import json

import pytest
from fastapi.testclient import TestClient

from gateway.web import control, data
from gateway.web.app import create_app
from gateway.web.auth import COOKIE_NAME
from gateway.web.settings import WebSettings

PW = 'let-me-in'


@pytest.fixture
def client(cfg, monkeypatch):
    app = create_app(WebSettings(password=PW, secret_key='c' * 64, session_hours=1))
    monkeypatch.setattr(data, 'run_systemctl', lambda args, timeout=10: (0, 'active'))
    return TestClient(app)


@pytest.fixture
def auth_client(client):
    r = client.post('/login', data={'password': PW}, follow_redirects=False)
    assert r.status_code == 302
    return client


def csrf(c):
    from gateway.web.auth import Auth
    a = c.app.state.auth
    return a.csrf_token(c.cookies.get(COOKIE_NAME))


# ---- 鉴权 ----
def test_pages_redirect_when_logged_out(client):
    for path in ('/', '/i/demo', '/new'):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 302 and r.headers['location'] == '/login'


def test_api_401_when_logged_out(client):
    assert client.get('/api/instances').status_code == 401


def test_login_page_and_bad_password(client):
    assert client.get('/login').status_code == 200
    r = client.post('/login', data={'password': 'nope'})
    assert r.status_code == 401 and '口令不对' in r.text


def test_login_then_index(auth_client):
    r = auth_client.get('/')
    assert r.status_code == 200 and 'demo' in r.text


def test_logout(auth_client):
    r = auth_client.post('/logout', headers={'X-CSRF': csrf(auth_client)}, follow_redirects=False)
    assert r.status_code == 302
    assert auth_client.get('/api/instances').status_code == 401


def test_healthz_is_public(client):
    assert client.get('/healthz').json() == {'ok': True}


# ---- CSRF ----
def test_post_without_csrf_is_403(auth_client):
    r = auth_client.post('/api/i/demo/action/rotate')
    assert r.status_code == 403 and 'CSRF' in r.json()['message']


def test_post_with_csrf_header(auth_client, monkeypatch):
    monkeypatch.setattr(control, 'action', lambda inst, name, **kw: {'ok': True, 'message': 'done'})
    r = auth_client.post('/api/i/demo/action/rotate', headers={'X-CSRF': csrf(auth_client)})
    assert r.status_code == 200 and r.json()['message'] == 'done'


def test_post_with_csrf_form_field(auth_client, monkeypatch):
    seen = {}
    def fake(inst, name, **kw):
        seen.update(kw); return {'ok': True, 'message': 'ok'}
    monkeypatch.setattr(control, 'action', fake)
    r = auth_client.post('/api/i/demo/action/reopen-card',
                         data={'csrf': csrf(auth_client), 'card_id': '7'})
    assert r.status_code == 200 and seen == {'card_id': '7'}      # csrf 不会混进参数


# ---- 只读接口 ----
def test_instances_shape(auth_client):
    rows = auth_client.get('/api/instances').json()
    assert [r['inst'] for r in rows] == ['demo']
    assert {'health', 'health_reason', 'units', 'pending'} <= set(rows[0])


def test_unknown_instance_404(auth_client):
    assert auth_client.get('/api/i/ghost/overview').status_code == 404
    assert auth_client.get('/i/ghost').status_code == 404


def test_overview_and_cards_and_session(auth_client, cfg, db):
    from gateway import cards as cardsmod, db as dbm
    from gateway.events import EventRow
    dbm.insert_event(db, EventRow(message_id='om_1', chat_id='oc_group1', chat_type='group',
                                  create_time=dbm.now_ms() - 60_000, sender_id='ou_a', sender_type='user',
                                  msg_type='text', text='问题', mentions=[{'key': 'k', 'id': 'ou_bot',
                                                                          'name': 'bot'}], mentions_me=True))
    cardsmod.assign_cards(db, cfg, dbm.now_ms())
    assert auth_client.get('/api/i/demo/overview').json()['card_states'] == {'open': 1}
    cards = auth_client.get('/api/i/demo/cards').json()
    assert cards['total'] == 1 and cards['items'][0]['id'] == 1
    assert auth_client.get('/api/i/demo/cards/1').json()['messages'][0]['text'] == '问题'
    assert auth_client.get('/api/i/demo/cards/99').status_code == 404
    assert auth_client.get('/api/i/demo/session').json()['total_cost_usd'] == 0


def test_db_missing_returns_409(auth_client, cfg):
    cfg.paths.db_file.unlink(missing_ok=True)
    assert auth_client.get('/api/i/demo/overview').status_code == 409


def test_config_read_masks(auth_client):
    v = auth_client.get('/api/i/demo/config').json()
    assert v['values']['app_secret'].startswith('••••')
    assert any(f['name'] == 'group_allow' and f['restart'] == 'listener' for f in v['fields'])


def test_attachment_404_and_400(auth_client):
    assert auth_client.get('/api/i/demo/attach/nope_key').status_code == 404
    assert auth_client.get('/api/i/demo/attach/...').status_code == 400


# ---- 写操作 ----
def test_action_unknown_returns_400(auth_client):
    r = auth_client.post('/api/i/demo/action/nuke', headers={'X-CSRF': csrf(auth_client)})
    assert r.status_code == 400 and not r.json()['ok']


def test_config_write_through_api(auth_client, cfg):
    from gateway import config as configmod
    r = auth_client.post('/api/i/demo/config', headers={'X-CSRF': csrf(auth_client)},
                         data={'quiet_seconds': '12'})
    assert r.status_code == 200 and r.json()['ok']
    assert configmod.load('demo').quiet_seconds == 12
    r = auth_client.post('/api/i/demo/config', headers={'X-CSRF': csrf(auth_client)},
                         data={'quiet_seconds': 'abc'})
    assert r.status_code == 400 and 'quiet_seconds' in r.json()['errors']


def test_wizard_init_validation(auth_client, tmp_path):
    r = auth_client.post('/api/new/init', headers={'X-CSRF': csrf(auth_client)},
                         data={'inst': 'x2', 'project_dir': '/definitely/not/here'})
    assert r.status_code == 400 and '不存在' in r.json()['message']
    proj = tmp_path / 'ok'; proj.mkdir()
    r = auth_client.post('/api/new/init', headers={'X-CSRF': csrf(auth_client)},
                         data={'inst': 'x2', 'project_dir': str(proj)})
    assert r.status_code == 200 and r.json()['ok']


# ---- SSE ----
def test_logs_stream(auth_client, cfg, monkeypatch):
    """真实的日志流是无限的,这里换成会结束的假流,只验路由把事件包成 SSE。"""
    def finite(inst, stream, since=0, q=None, stop=None):
        assert (inst, stream) == ('demo', 'claude')
        yield {'at': 1, 'level': 'info', 'text': '嗨'}
    from gateway.web import app as appmod
    monkeypatch.setattr(appmod.logs, 'stream_events', finite)
    with auth_client.stream('GET', '/api/i/demo/logs/claude') as r:
        assert r.status_code == 200
        payloads = [json.loads(l[5:].strip()) for l in r.iter_lines() if l.startswith('data:')]
    assert payloads == [{'at': 1, 'level': 'info', 'text': '嗨'}]


def test_logs_stream_signals_stop_not_close(auth_client, cfg, monkeypatch):
    """断线时不能对着可能正在线程里跑的生成器 close() —— 会 ValueError: generator
    already executing(2026-09-21 实测 500)。改成置 stop,让生成器自己收尾。"""
    seen = {}
    def finite(inst, stream, since=0, q=None, stop=None):
        seen['stop'] = stop
        seen['closed'] = False
        try:
            yield {'at': 1, 'level': 'info', 'text': '嗨'}
        except GeneratorExit:
            seen['closed'] = True
            raise
    from gateway.web import app as appmod
    monkeypatch.setattr(appmod.logs, 'stream_events', finite)
    with auth_client.stream('GET', '/api/i/demo/logs/claude') as r:
        list(r.iter_lines())
    assert seen['stop'] is not None and seen['stop'].is_set()


def test_logs_unknown_stream_400(auth_client):
    assert auth_client.get('/api/i/demo/logs/syslog').status_code == 400
