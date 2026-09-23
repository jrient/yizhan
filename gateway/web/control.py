"""写操作:服务动作、配置读写、新建向导。全部包已有的 cli / config / feishu / claude_runner。

web 是 toml 的唯一写入方(listener 与 dispatcher 只读),进程内加锁防两个浏览器同时保存。
"""
from __future__ import annotations

import json
import os
import tomllib
import shutil
import subprocess
import threading
from argparse import Namespace
from pathlib import Path

from .. import cli, config as configmod, db as dbm
from ..claude_runner import probe
from ..feishu import FeishuClient, FeishuError
from ..paths import Paths

_write_lock = threading.Lock()
ACTION_TIMEOUT = 20


def _sysctl(args: list[str]) -> tuple[bool, str]:
    try:
        p = subprocess.run(['systemctl', '--user', *args], capture_output=True, text=True,
                           timeout=ACTION_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, f'systemctl {" ".join(args)} 超过 {ACTION_TIMEOUT} 秒没返回'
    except FileNotFoundError as e:
        return False, str(e)
    if p.returncode != 0:
        return False, (p.stderr or p.stdout or '').strip()[:300]
    return True, (p.stdout or '').strip()


# ---- 动作 ----
def action(inst: str, name: str, **kw) -> dict:
    try:
        if name == 'pause':
            cli.pause(inst)
            return {'ok': True, 'message': f'{inst} 已暂停,timer 停了。现在可以在终端接管会话'}
        if name == 'resume':
            cli.resume(inst)
            return {'ok': True, 'message': f'{inst} 已恢复'}
        if name == 'restart-listener':
            ok, msg = _sysctl(['restart', f'yizhan-listener@{inst}'])
            return {'ok': ok, 'message': 'listener 已重启' if ok else f'重启失败: {msg}'}
        if name == 'dispatch-now':
            ok, msg = _sysctl(['--no-block', 'start', f'yizhan-dispatcher@{inst}.service'])
            return {'ok': ok, 'message': '已叫醒 dispatcher' if ok else f'失败: {msg}'}
        if name == 'rotate':
            cfg = configmod.load(inst)
            conn = dbm.connect(cfg.paths.db_file)
            sid = dbm.session_rotate(conn, 'manual(web)')
            conn.close()
            return {'ok': True, 'message': f'已换新会话 {sid[:8]}…,下一张卡会先读记忆与未结卡'}
        if name == 'reopen-card':
            return _reopen_card(inst, int(kw.get('card_id') or 0))
        return {'ok': False, 'message': f'不认识的动作: {name}'}
    except (configmod.ConfigError, OSError) as e:
        return {'ok': False, 'message': str(e)}


def _reopen_card(inst: str, card_id: int) -> dict:
    cfg = configmod.load(inst)
    conn = dbm.connect(cfg.paths.db_file)
    try:
        it = dbm.get_issue(conn, card_id)
        if it is None:
            return {'ok': False, 'message': f'没有卡 #{card_id}'}
        if it['state'] != 'stuck':
            return {'ok': False, 'message': f"卡 #{card_id} 现在是 {it['state']},只有 stuck 的卡才需要重开"}
        dbm.update_issue(conn, card_id, state='open', redispatch=0, lock_owner=None, lock_until=None)
        return {'ok': True, 'message': f'卡 #{card_id} 已重开,重派次数清零'}
    finally:
        conn.close()


# ---- 配置 ----
# (字段, 类型, 说明, 是否密钥, 改后要重启什么)
FIELDS: list[tuple] = [
    ('app_id', 'str', '飞书应用的 App ID,开发者后台「凭证与基础信息」页', False, 'listener'),
    ('app_secret', 'str', 'App Secret。页面上只显示末四位', True, 'listener'),
    ('project_dir', 'str', '机器人要读的项目目录,以 --add-dir 挂进去,只读不写', False, ''),
    ('skill', 'str', '这个实例用哪个 skill,工作台 .claude/skills/ 下的目录名', False, ''),
    ('alert_open_id', 'str', '出事时机器人私聊谁。你的 open_id,@ 机器人一句就能从日志里看到', False, ''),
    ('p2p_allow', 'list', '允许私聊的人的 open_id。不在名单里的私聊只记录不回', False, ''),
    ('group_allow', 'list', '允许响应的群 chat_id。进了新群会告警提醒,确认后加进来', False, 'listener'),
    ('quiet_seconds', 'int', '发起人最后一条消息静默多久才派单。同一人连发多条只回一次', False, ''),
    ('lock_minutes', 'int', '一张卡的锁多久过期', False, ''),
    ('max_redispatch', 'int', '一张卡最多重派几次,超了标 stuck 并告警', False, ''),
    ('merge_gap_minutes', 'int', '不开话题时,同一人的消息间隔多久内算同一张卡', False, ''),
    ('idle_timeout_min', 'int', 'claude 多久没输出算卡死,杀掉重来', False, ''),
    ('hard_timeout_min', 'int', '一轮最长跑多久', False, ''),
    ('rotate_at_tokens', 'int', '上下文超过多少 token 就主动换新会话。0 = 关,靠自动压缩', False, ''),
    ('reply_in_thread', 'bool', '回复开不开话题。开=追问自动并进同一张卡;关=靠时间窗归并', False, ''),
    ('claude_bin', 'str', 'claude 可执行文件路径', False, ''),
    ('probe_timeout_sec', 'int', '探活最多等几秒。代理慢时调大,否则会频繁报「claude 连不上」', False, ''),
    ('permission_args', 'list', 'claude 的权限参数。skip 会连 settings.json 的 deny 清单一起绕过', False, ''),
    ('claude_env', 'table', '给 claude 补的环境变量。这台机器要 https_proxy,不给就 403', False, ''),
]
MASK = '••••'


def _mask(v: str) -> str:
    return MASK + (v[-4:] if v and len(v) > 4 else '')


def config_read(inst: str) -> dict:
    cfg = configmod.load(inst)
    out = {}
    for name, kind, _desc, secret, _r in FIELDS:
        v = getattr(cfg, name, None)
        out[name] = _mask(v or '') if secret else v
    return out


def field_meta() -> list[dict]:
    return [{'name': n, 'type': t, 'desc': d, 'secret': s, 'restart': r} for n, t, d, s, r in FIELDS]


def _coerce(name: str, kind: str, raw):
    if kind == 'int':
        if isinstance(raw, bool) or (isinstance(raw, str) and not raw.strip().lstrip('-').isdigit()):
            raise ValueError('要填整数')
        return int(raw)
    if kind == 'bool':
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ('1', 'true', 'yes', 'on')
    if kind == 'list':
        if isinstance(raw, list):
            items = raw
        else:
            items = [x.strip() for x in str(raw).replace(',', '\n').split('\n')]
        return [x for x in items if x]
    if kind == 'table':
        if isinstance(raw, dict):
            return {str(k): str(v) for k, v in raw.items() if str(k).strip()}
        text = str(raw).strip()
        if text.startswith('['):
            raise ValueError('要填一个对象,形如 {"https_proxy": "http://…"},不是数组')
        if text.startswith('{'):
            # 配置页把 table 渲染成 JSON,这里必须认。不认的话保存一次就把
            # claude_env 清空,claude 立刻没代理变 403(2026-09-21 截图时发现)。
            try:
                obj = json.loads(text)
            except ValueError as e:
                raise ValueError(f'不是合法的 JSON: {e}') from e
            if not isinstance(obj, dict):
                raise ValueError('要填一个对象,形如 {"https_proxy": "http://…"}')
            return {str(k): str(v) for k, v in obj.items() if str(k).strip()}
        out = {}
        for line in text.split('\n'):
            if '=' in line:
                k, v = line.split('=', 1)
                if k.strip():
                    out[k.strip()] = v.strip().strip('"').strip("'")
        if text and not out:
            # 静默变成空表比报错更糟:claude_env 一空,claude 就没代理了
            raise ValueError('看不懂,用 JSON 对象或每行一条 key = value')
        return out
    return str(raw).strip()


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, int):
        return str(v)
    if isinstance(v, list):
        return '[' + ', '.join(_toml_value(str(x)) for x in v) + ']'   # 元素也要转义引号和反斜杠
    return '"' + str(v).replace('\\', '\\\\').replace('"', '\\"') + '"'


def dump_toml(values: dict, extra: dict | None = None) -> str:
    """按 FIELDS 顺序写出 toml,再追加 extra 里表单管不到的键。

    表(claude_env 这类 dict)一律放最后,不然后面的普通键会被算进表里。
    extra 的存在是因为:原来整份覆盖,手工加的、FIELDS 没列的键(比如 probe_timeout_sec)
    在网页上随便存一次就被悄悄删掉(2026-09-23 审查发现)。
    """
    lines, tables = [], []
    for name, kind, desc, _secret, _r in FIELDS:
        if name not in values:
            continue
        v = values[name]
        if kind == 'table':
            tables.append(f'\n# {desc}\n[{name}]\n' +
                          ''.join(f'{k} = {_toml_value(str(x))}\n' for k, x in (v or {}).items()))
            continue
        lines.append(f'# {desc}\n{name} = {_toml_value(v)}')
    for k, v in (extra or {}).items():
        if isinstance(v, dict):
            tables.append(f'\n[{k}]\n' + ''.join(f'{kk} = {_toml_value(vv)}\n' for kk, vv in v.items()))
        else:
            lines.append(f'{k} = {_toml_value(v)}')
    return '\n'.join(lines) + '\n' + ''.join(tables)


def config_write(inst: str, form: dict) -> dict:
    """校验 → 写临时文件 → config.load 再验 → 原子替换。任何一步不过,原文件不动。"""
    paths = Paths(inst)
    current = configmod.load(inst)
    errors: dict[str, str] = {}
    values: dict = {}
    for name, kind, _desc, secret, _r in FIELDS:
        raw = form.get(name)
        if raw is None:
            values[name] = getattr(current, name)
            continue
        if secret and (raw == '' or str(raw).startswith(MASK)):
            values[name] = getattr(current, name)      # 提交的是打码值 → 保留原密钥
            continue
        try:
            values[name] = _coerce(name, kind, raw)
        except ValueError as e:
            errors[name] = str(e)
    for req in ('app_id', 'app_secret', 'project_dir'):
        if not values.get(req):
            errors[req] = '必填'
    if values.get('project_dir') and not Path(values['project_dir']).is_dir():
        errors['project_dir'] = '目录不存在'
    if errors:
        return {'ok': False, 'errors': errors, 'needs_restart': []}

    with open(paths.config_file, 'rb') as f:
        raw = tomllib.load(f)
    known = {n for n, *_ in FIELDS}
    extra = {k: v for k, v in raw.items() if k not in known}
    text = dump_toml(values, extra)
    with _write_lock:
        tmp = paths.config_file.with_suffix('.toml.tmp')
        tmp.write_text(text, encoding='utf-8')
        tmp.chmod(0o600)
        try:
            configmod.load_file(inst, tmp)      # 直接验临时文件,正式文件全程不动
        except configmod.ConfigError as e:
            tmp.unlink(missing_ok=True)
            return {'ok': False, 'errors': {'_': str(e)}, 'needs_restart': []}
        os.replace(tmp, paths.config_file)

    changed = [n for n, _k, _d, _s, _r in FIELDS
               if n in form and values[n] != getattr(current, n)]
    restart = sorted({r for n, _k, _d, _s, r in FIELDS if n in changed and r})
    return {'ok': True, 'errors': {}, 'needs_restart': restart, 'changed': changed}


# ---- 新建向导 ----
def wizard_init(inst: str, project_dir: str, skill: str | None = None) -> dict:
    if not inst or not inst.replace('-', '').replace('_', '').isalnum():
        return {'ok': False, 'message': '实例名只能用字母、数字、- 和 _'}
    if Paths(inst).config_file.exists():
        return {'ok': False, 'message': f'{inst} 已经存在了'}
    if not Path(project_dir).is_dir():
        return {'ok': False, 'message': f'项目目录不存在: {project_dir}'}
    a = Namespace(inst=inst, project=project_dir, skill=skill or cli.default_skill_name(inst),
                  from_dir=None, force=False)
    cli.cmd_init(a)
    return {'ok': True, 'message': f'{inst} 的工作台与配置模板已生成', 'skill': a.skill}


def wizard_verify(inst: str, form: dict) -> dict:
    """先存配置,再当场验三样:飞书凭证、机器人能力、claude 能不能连上。"""
    saved = config_write(inst, form)
    if not saved['ok']:
        return {'ok': False, 'saved': saved}
    cfg = configmod.load(inst)
    out = {'saved': saved}
    client = FeishuClient(cfg.app_id, cfg.app_secret)
    try:
        client.token()
        out['token'] = {'ok': True, 'message': '凭证有效'}
    except FeishuError as e:
        out['token'] = {'ok': False, 'message': f'{e}(app_id / app_secret 填错了?)'}
        out['ok'] = False
        return out
    try:
        oid = client.bot_open_id()
    except FeishuError as e:
        oid = ''
        out['bot_error'] = str(e)
    out['bot'] = {'ok': bool(oid), 'open_id': oid,
                  'message': f'机器人 open_id {oid}' if oid else
                  '取不到机器人 open_id。到飞书开发者后台:① 添加机器人能力 ② 开 im:message 等权限 '
                  '③ 事件与回调选「长连接」并订阅 im.message.receive_v1 ④ 创建版本发布并等管理员审核'}
    ok, msg = probe(cfg)
    out['probe'] = {'ok': ok, 'message': msg if ok else
                    f'claude 连不上: {msg}。这台机器要走代理的话,在 claude_env 里填 https_proxy'}
    out['ok'] = bool(oid) and ok
    return out


def wizard_enable(inst: str) -> dict:
    ok1, m1 = _sysctl(['enable', '--now', f'yizhan-listener@{inst}'])
    ok2, m2 = _sysctl(['enable', '--now', f'yizhan-dispatcher@{inst}.timer'])
    if ok1 and ok2:
        return {'ok': True, 'message': f'{inst} 的 listener 与 timer 都起来了'}
    return {'ok': False, 'message': f'启用失败: {m1} {m2}'.strip()}
