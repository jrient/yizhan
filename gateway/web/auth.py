"""单口令登录。不做用户体系:现在自己用,以后分享给同事就是把口令给他。"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import defaultdict

from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

from .settings import WebSettings

MAX_FAILS = 5
LOCK_SECONDS = 600
COOKIE_NAME = 'yizhan_web'


class Auth:
    def __init__(self, settings: WebSettings, clock=time.time):
        self.s = settings
        self.clock = clock
        self.signer = TimestampSigner(settings.secret_key, salt='yizhan-web')
        self._fails: dict[str, list[float]] = defaultdict(list)

    # ---- 口令 ----
    def locked_for(self, ip: str) -> float:
        """还要锁多少秒。0 = 没锁。"""
        now = self.clock()
        fails = [t for t in self._fails[ip] if now - t < LOCK_SECONDS]
        self._fails[ip] = fails
        if len(fails) < MAX_FAILS:
            return 0.0
        return LOCK_SECONDS - (now - fails[-MAX_FAILS])

    def check_password(self, ip: str, pw: str) -> tuple[bool, str]:
        left = self.locked_for(ip)
        if left > 0:
            return False, f'错太多次了,{int(left / 60) + 1} 分钟后再试'
        if hmac.compare_digest(pw or '', self.s.password):
            self._fails.pop(ip, None)
            return True, ''
        self._fails[ip].append(self.clock())
        n = MAX_FAILS - len(self._fails[ip])
        return False, '口令不对' + (f',还能试 {n} 次' if 0 < n <= 2 else '')

    # ---- cookie ----
    def issue_cookie(self) -> str:
        return self.signer.sign(secrets.token_urlsafe(8)).decode()

    def verify_cookie(self, value: str | None) -> bool:
        if not value:
            return False
        try:
            self.signer.unsign(value, max_age=self.s.session_hours * 3600)
            return True
        except (BadSignature, SignatureExpired):
            return False

    # ---- CSRF ----
    def csrf_token(self, cookie_value: str | None) -> str:
        """和 cookie 绑定:换了会话,旧表单里的 token 就失效。"""
        return hmac.new(self.s.secret_key.encode(), (cookie_value or '').encode(),
                        hashlib.sha256).hexdigest()[:32]

    def verify_csrf(self, cookie_value: str | None, token: str | None) -> bool:
        return bool(token) and hmac.compare_digest(token, self.csrf_token(cookie_value))
