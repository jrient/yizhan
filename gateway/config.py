"""实例配置:<项目>/config/<inst>.toml(gitignore 里,0600)。"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field

from .paths import Paths


class ConfigError(Exception):
    pass


@dataclass
class Config:
    inst: str
    app_id: str
    app_secret: str
    project_dir: str
    skill: str = 'feedback-group-reply'
    alert_open_id: str = ''
    p2p_allow: list[str] = field(default_factory=list)
    group_allow: list[str] = field(default_factory=list)
    quiet_seconds: int = 30
    lock_minutes: int = 25
    max_redispatch: int = 3
    merge_gap_minutes: int = 10
    claude_bin: str = os.path.expanduser('~/.local/bin/claude')
    permission_args: list[str] = field(default_factory=lambda: ['--dangerously-skip-permissions'])
    idle_timeout_min: int = 15
    hard_timeout_min: int = 60
    probe_timeout_sec: int = 30
    # 兜底:主会话上下文超过这么多 token 就主动轮换。0 = 关(依赖 CLI 自带的自动压缩)。
    # 09-21 的自动压缩实验没跑到 100k 阈值,结论未定,所以留这个开关。
    rotate_at_tokens: int = 0
    # 给 claude 子进程补的环境变量。这台机器上 claude 必须走本地代理,否则报
    # 403 Request not allowed(2026-09-21 实测);systemd 服务没有这些变量,必须显式给。
    # 注意飞书接口不走代理(FeishuClient 关了 trust_env)。
    claude_env: dict = field(default_factory=dict)
    # 回复是否开话题。开=追问天然并进同一张卡;关=靠(会话,发起人)+10 分钟窗口归并。
    reply_in_thread: bool = True
    paths: Paths = field(default=None, repr=False)  # type: ignore[assignment]

    REQUIRED = ('app_id', 'app_secret', 'project_dir')


def load(inst: str) -> Config:
    paths = Paths(inst)
    if not paths.config_file.exists():
        raise ConfigError(f'配置不存在: {paths.config_file}(先跑 yizhan init {inst} --project <dir>)')
    with open(paths.config_file, 'rb') as f:
        data = tomllib.load(f)
    missing = [k for k in Config.REQUIRED if not data.get(k)]
    if missing:
        raise ConfigError(f'{paths.config_file} 缺必填字段: {missing}')
    known = {k: v for k, v in data.items() if k in Config.__dataclass_fields__ and k not in ('inst', 'paths')}
    cfg = Config(inst=inst, paths=paths, **known)
    cfg.claude_bin = os.path.expanduser(cfg.claude_bin)
    cfg.project_dir = os.path.expanduser(cfg.project_dir)
    return cfg
