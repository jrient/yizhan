"""Web 控制台自己的配置:config/web.toml(0600)。"""
from __future__ import annotations

import secrets
import tomllib
from dataclasses import dataclass
from pathlib import Path

from ..paths import Paths


class WebConfigError(Exception):
    pass


@dataclass
class WebSettings:
    host: str = '0.0.0.0'
    port: int = 8760
    password: str = ''
    session_hours: int = 72
    secret_key: str = ''
    path: Path | None = None


def default_path() -> Path:
    return Paths('web-placeholder').config_dir / 'web.toml'


def load(path: Path | None = None) -> WebSettings:
    path = Path(path) if path else default_path()
    if not path.exists():
        raise WebConfigError(f'配置不存在: {path}(跑 yizhan web-init 生成)')
    with open(path, 'rb') as f:
        data = tomllib.load(f)
    if not data.get('password'):
        raise WebConfigError(f'{path} 缺 password —— 没有口令就不起服务,局域网内谁都能进来')
    known = {k: v for k, v in data.items() if k in WebSettings.__dataclass_fields__ and k != 'path'}
    s = WebSettings(path=path, **known)
    if not s.secret_key:
        # cookie 与 CSRF 的签名密钥。首次启动生成并写回,不然重启一次所有人掉线。
        s.secret_key = secrets.token_hex(32)
        _append_secret(path, s.secret_key)
    return s


def _append_secret(path: Path, key: str):
    text = path.read_text(encoding='utf-8').rstrip('\n')
    text += f'\n\n# 自动生成,别改:cookie 与 CSRF 的签名密钥\nsecret_key = "{key}"\n'
    path.write_text(text, encoding='utf-8')
    path.chmod(0o600)


def write_initial(path: Path | None = None, port: int = 8760, host: str = '0.0.0.0') -> tuple[Path, str]:
    """生成 web.toml 与随机口令。返回 (路径, 明文口令) —— 口令只在这里打印一次。"""
    path = Path(path) if path else default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    pw = secrets.token_urlsafe(12)
    path.write_text(
        '# 驿站 Web 控制台。里面有口令,权限 0600,已在 .gitignore 里。\n'
        f'host = "{host}"          # 0.0.0.0 才能从局域网其他机器访问\n'
        f'port = {port}\n'
        f'password = "{pw}"\n'
        'session_hours = 72      # 登录后多久要重新输口令\n',
        encoding='utf-8')
    path.chmod(0o600)
    return path, pw
