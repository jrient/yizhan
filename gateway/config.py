"""实例配置:<项目>/config/<inst>.toml(gitignore 里,0600)。"""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from .paths import Paths


# 这台机器上 claude 必须走本地代理,否则 403 Request not allowed(2026-09-21 实测)。
# 所有实例默认带上;实例的 [claude_env] 同名键覆盖它,写成空串就是不要这个变量。
DEFAULT_CLAUDE_ENV = {
    'https_proxy': 'http://127.0.0.1:7890',
    'http_proxy': 'http://127.0.0.1:7890',
}


# 实例只在这个时段内派单(2026-09-24 需求)。北京时间,支持跨午夜(如 22:00-06:00),
# 结束可写 24:00;实例配置 work_hours 覆盖,写成 "" 表示全天。
# 非工作时间 listener 照常收消息建卡,只是 dispatcher 不开新卡,开工后按顺序处理。
DEFAULT_WORK_HOURS = '06:00-24:00'
TZ = ZoneInfo('Asia/Shanghai')
_HM = re.compile(r'^(\d{2}):(\d{2})-(\d{2}):(\d{2})$')


def parse_work_hours(spec: str) -> tuple[int, int] | None:
    """'06:00-24:00' → (360, 1440) 分钟。空串 → None(全天)。格式不对抛 ValueError。"""
    spec = (spec or '').strip()
    if not spec:
        return None
    m = _HM.match(spec)
    if not m:
        raise ValueError(f'工作时间要写成 HH:MM-HH:MM,如 06:00-24:00,收到 {spec!r}')
    h1, m1, h2, m2 = map(int, m.groups())
    start, end = h1 * 60 + m1, h2 * 60 + m2
    if h1 > 23 or m1 > 59 or m2 > 59 or end > 1440 or start == end:
        raise ValueError(f'工作时间不合法: {spec!r}(小时 00-24、分钟 00-59,起止不能相同)')
    return start, end


def in_work_hours(spec: str, now: datetime | None = None) -> bool:
    rng = parse_work_hours(spec)
    if rng is None:
        return True
    now = (now or datetime.now(TZ)).astimezone(TZ)
    cur = now.hour * 60 + now.minute
    start, end = rng
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end          # 跨午夜


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
    # 定时 compact 的门槛:上一轮上下文超过这么多 token 才压缩,小会话压了反而变大还丢细节。0 = 每次都压。
    compact_at_tokens: int = 300_000
    # 给 claude 子进程补的环境变量,叠在 DEFAULT_CLAUDE_ENV 之上(默认已带本机代理)。
    # systemd 服务没有 shell 的代理变量,所以代理不能指望从环境继承。
    # 注意飞书接口不走代理(FeishuClient 关了 trust_env)。
    claude_env: dict = field(default_factory=dict)
    # 除原项目外还要让 claude 读得到的目录(逐个 --add-dir)。受限权限模式下,
    # 工作目录与 --add-dir 之外的路径一律读不到;drama-auto 抽帧要读 /data/QTS 下的成片。
    extra_dirs: list = field(default_factory=list)
    # 回复是否开话题。开=追问天然并进同一张卡;关=靠(会话,发起人)+10 分钟窗口归并。
    reply_in_thread: bool = True
    # 工作时段,见 DEFAULT_WORK_HOURS
    work_hours: str = DEFAULT_WORK_HOURS
    paths: Paths = field(default=None, repr=False)  # type: ignore[assignment]

    REQUIRED = ('app_id', 'app_secret', 'project_dir')


def load(inst: str) -> Config:
    paths = Paths(inst)
    if not paths.config_file.exists():
        raise ConfigError(f'配置不存在: {paths.config_file}(先跑 yizhan init {inst} --project <dir>)')
    return load_file(inst, paths.config_file)


def load_file(inst: str, path) -> Config:
    """按实例 inst 的规则解析任意一个 toml 文件。

    控制台保存配置时用它校验临时文件:原来是先把临时文件拷到正式路径上再 load,
    校验的那一瞬间 dispatcher 可能正好读到一份没验证过的配置(2026-09-23 审查发现)。
    """
    paths = Paths(inst)
    try:
        with open(path, 'rb') as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f'{path} 不是合法的 TOML: {e}') from e
    missing = [k for k in Config.REQUIRED if not data.get(k)]
    if missing:
        raise ConfigError(f'{path} 缺必填字段: {missing}')
    known = {k: v for k, v in data.items() if k in Config.__dataclass_fields__ and k not in ('inst', 'paths')}
    cfg = Config(inst=inst, paths=paths, **known)
    cfg.claude_bin = os.path.expanduser(cfg.claude_bin)
    cfg.project_dir = os.path.expanduser(cfg.project_dir)
    try:
        parse_work_hours(cfg.work_hours)
    except ValueError as e:
        raise ConfigError(f'{path} 的 work_hours: {e}') from e
    return cfg
