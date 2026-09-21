from gateway.web.auth import Auth, MAX_FAILS
from gateway.web.settings import WebSettings


class Clock:
    def __init__(self): self.t = 1_000_000.0
    def __call__(self): return self.t


def mk():
    c = Clock()
    return Auth(WebSettings(password='s3cret', secret_key='a' * 64, session_hours=1), clock=c), c


def test_password_ok_and_bad():
    a, _ = mk()
    assert a.check_password('1.1.1.1', 's3cret') == (True, '')
    ok, msg = a.check_password('1.1.1.1', 'wrong')
    assert not ok and '不对' in msg


def test_lockout_after_five_and_release(monkeypatch):
    a, c = mk()
    for _ in range(MAX_FAILS):
        a.check_password('2.2.2.2', 'wrong')
    ok, msg = a.check_password('2.2.2.2', 's3cret')      # 锁定期内正确口令也拒
    assert not ok and '分钟后' in msg
    assert a.check_password('3.3.3.3', 's3cret')[0]      # 只锁这个 IP
    c.t += 601
    assert a.check_password('2.2.2.2', 's3cret')[0]      # 10 分钟后解锁


def test_success_clears_failures():
    a, _ = mk()
    for _ in range(3):
        a.check_password('4.4.4.4', 'wrong')
    a.check_password('4.4.4.4', 's3cret')
    for _ in range(4):
        a.check_password('4.4.4.4', 'wrong')
    assert a.locked_for('4.4.4.4') == 0                  # 计数已清零,还没到 5 次


def test_cookie_roundtrip_and_expiry():
    a, c = mk()
    v = a.issue_cookie()
    assert a.verify_cookie(v)
    assert not a.verify_cookie(None) and not a.verify_cookie('forged')
    c2 = Clock()
    a2 = Auth(WebSettings(password='x', secret_key='b' * 64), clock=c2)
    assert not a2.verify_cookie(v)                       # 换密钥签名对不上


def test_cookie_expires(monkeypatch):
    import itsdangerous
    a, _ = mk()
    v = a.issue_cookie()
    real = itsdangerous.TimestampSigner.unsign
    def fake(self, value, max_age=None, **kw):
        raise itsdangerous.SignatureExpired('old')
    monkeypatch.setattr(itsdangerous.TimestampSigner, 'unsign', fake)
    assert not a.verify_cookie(v)


def test_csrf():
    a, _ = mk()
    v = a.issue_cookie()
    t = a.csrf_token(v)
    assert a.verify_csrf(v, t)
    assert not a.verify_csrf(v, 'x' * 32)
    assert not a.verify_csrf(v, None)
    assert not a.verify_csrf(a.issue_cookie(), t)        # 换了会话旧 token 失效
