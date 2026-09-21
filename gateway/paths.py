"""实例相关路径。

配置放在**项目自己的 `config/` 目录**(仓库根下,已 gitignore,里面是明文密钥,权限 0600)。
状态(库、工作台、日志)仍在 ~/.local/state/yizhan/<inst>/ —— 那是运行期数据,不该混进代码仓库。
两者都可用 YIZHAN_CONFIG_DIR / YIZHAN_STATE_DIR 覆盖(测试与多用户部署用)。
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


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
