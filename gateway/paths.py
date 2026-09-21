"""实例相关路径。可用环境变量 YIZHAN_CONFIG_DIR / YIZHAN_STATE_DIR 覆盖(测试与多用户部署用)。"""
from __future__ import annotations

import os
from pathlib import Path


class Paths:
    def __init__(self, inst: str):
        if not inst or '/' in inst:
            raise ValueError(f'非法实例名: {inst!r}')
        self.inst = inst
        cfg_dir = os.environ.get('YIZHAN_CONFIG_DIR') or os.path.expanduser('~/.config/yizhan')
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
