"""实例相关路径。

配置放在**项目自己的 `config/` 目录**(仓库根下,已 gitignore,里面是明文密钥,权限 0600)。
状态(库、工作台、日志)仍在 ~/.local/state/yizhan/<inst>/ —— 那是运行期数据,不该混进代码仓库。
两者都可用 YIZHAN_CONFIG_DIR / YIZHAN_STATE_DIR 覆盖(测试与多用户部署用)。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 服务名里的实例部分。只转义 systemd 不认的字符(中文、空格等 → \\xNN),`-` 原样保留。
# 不能照搬 systemd-escape:它把 `-` 也转成 \\x2d,现有 drama-auto 的服务名就全变了;
# 单元文件也不能改用 %I:%I 反转义时把 `-` 当 `/`,drama-auto 会变成 drama/auto。
# 所以单元文件保持 %i,程序启动时用 unescape_inst 自己还原(2026-09-24 创视助手起不来)。
_SAFE = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:_.-')


def escape_inst(inst: str) -> str:
    out = []
    for i, ch in enumerate(inst):
        if ch in _SAFE and not (i == 0 and ch == '.'):
            out.append(ch)
        else:
            out.extend(f'\\x{b:02x}' for b in ch.encode('utf-8'))
    return ''.join(out)


def unescape_inst(s: str) -> str:
    """systemd 的 %i 传进来的是转义形式,还原成原名。普通名字原样返回。"""
    if '\\x' not in s:
        return s
    raw = re.sub(rb'\\x([0-9a-fA-F]{2})', lambda m: bytes([int(m.group(1), 16)]), s.encode('utf-8'))
    return raw.decode('utf-8')


def unit(kind: str, inst: str, suffix: str = 'service') -> str:
    """实例的 systemd 服务名。kind = listener / dispatcher / compact;所有拼服务名的地方都走这里。"""
    return f'yizhan-{kind}@{escape_inst(inst)}.{suffix}'


class Paths:
    def __init__(self, inst: str):
        if not inst or '/' in inst:
            raise ValueError(f'非法实例名: {inst!r}')
        self.inst = inst
        cfg_dir = os.environ.get('YIZHAN_CONFIG_DIR') or str(REPO_ROOT / 'config')
        st_dir = os.environ.get('YIZHAN_STATE_DIR') or os.path.expanduser('~/.local/state/yizhan')
        self.config_dir = Path(cfg_dir)
        self.config_file = self.config_dir / f'{inst}.toml'
        self.state_dir = Path(st_dir) / inst
        self.db_file = self.state_dir / 'state.db'
        self.attachments = self.state_dir / 'attachments'
        self.logs = self.state_dir / 'logs'
        self.workspace = self.state_dir / 'workspace'
        self.lock_file = self.state_dir / 'dispatcher.lock'
        self.paused_file = self.state_dir / 'paused'

    def ensure(self) -> 'Paths':
        for d in (self.config_dir, self.state_dir, self.attachments, self.logs, self.workspace):
            d.mkdir(parents=True, exist_ok=True)
        return self
