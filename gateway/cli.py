"""yizhan:实例管理。裂变到新项目只需 init + 填凭证 + enable。"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import config as configmod, db as dbm
from .paths import Paths

TEMPLATES = Path(__file__).parent / 'templates'
# 仓库里的 skill 源(进版本库)。工作台拿到的是**完整副本**,不是软链:
# 机器人会自己改运行副本,两边隔离,要同步显式跑 yizhan sync-skill(用户 2026-09-21 要求)。
REPO_SKILLS = Path(__file__).resolve().parent.parent / 'skills'


def default_skill_name(inst: str) -> str:
    """skill 名带实例前缀。原项目以 --add-dir 挂进来,它自己的 .claude/skills 里
    常有同名的旧 skill,不加前缀两个同名 skill 会撞车(2026-09-21)。"""
    return f'{inst}-feedback-group-reply'


def repo_skill_dir(inst: str, skill: str) -> Path:
    return REPO_SKILLS / skill


def workspace_skill_dir(paths, skill: str) -> Path:
    return paths.workspace / '.claude' / 'skills' / skill


def trust_workspace(workspace: Path) -> bool:
    """把工作台标成已信任。

    不标的话 claude 会整个忽略 .claude/settings.json 的 allow/deny
    (启动时打印 Ignoring N permissions.allow entries),阶段二切到
    --permission-mode dontAsk 之后就变成什么都不许干(2026-09-21 实测)。
    """
    cfg_file = Path(os.path.expanduser('~/.claude.json'))
    try:
        data = json.loads(cfg_file.read_text(encoding='utf-8')) if cfg_file.exists() else {}
    except Exception:
        return False
    proj = data.setdefault('projects', {}).setdefault(str(workspace), {})
    if proj.get('hasTrustDialogAccepted') is True:
        return False
    proj['hasTrustDialogAccepted'] = True
    cfg_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    return True


def _render(src: Path, dst: Path, mapping: dict[str, str]):
    text = src.read_text(encoding='utf-8')
    for k, v in mapping.items():
        text = text.replace('{' + k + '}', v)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text, encoding='utf-8')


def cmd_init(a) -> int:
    paths = Paths(a.inst)
    a.skill = a.skill or default_skill_name(a.inst)
    project = str(Path(a.project).expanduser().resolve())
    if not Path(project).is_dir():
        sys.exit(f'项目目录不存在: {project}')
    paths.ensure()
    mapping = {'project_dir': project, 'skill': a.skill}

    repo_skill = repo_skill_dir(a.inst, a.skill)
    if repo_skill.is_dir():             # 仓库里已有这个实例的 skill → 整份复制进工作台
        dst = workspace_skill_dir(paths, a.skill)
        if dst.exists() and not a.force:
            print(f'  跳过已存在的工作台 skill: {dst}')
        else:
            shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(repo_skill, dst)
            print(f'  从仓库复制 skill: {repo_skill} → {dst}')

    ws_tpl = TEMPLATES / 'workspace'
    n = 0
    for src in ws_tpl.rglob('*'):
        if src.is_dir():
            continue
        rel = src.relative_to(ws_tpl)
        # skill 目录按 --skill 改名
        parts = list(rel.parts)
        if len(parts) >= 4 and parts[:3] == ['.claude', 'skills', 'feedback-group-reply']:
            # 模板目录名是通用的,落地时换成带实例前缀的真名
            parts[2] = a.skill
        dst = paths.workspace / Path(*parts)
        if dst.exists() and not a.force:
            print(f'  跳过已存在: {dst}')
            continue
        _render(src, dst, mapping); n += 1
    (paths.workspace / 'notes').mkdir(exist_ok=True)

    if a.from_dir:                      # 把旧 skill 的参考资料带过来
        src = Path(a.from_dir).expanduser()
        dstdir = paths.workspace / '.claude' / 'skills' / a.skill / 'reference'
        dstdir.mkdir(parents=True, exist_ok=True)
        for f in list((src / 'reference').glob('*')) if (src / 'reference').is_dir() else []:
            shutil.copy2(f, dstdir / f.name); n += 1
        if (src / 'SKILL.md').exists():
            shutil.copy2(src / 'SKILL.md', dstdir / 'legacy-skill.md'); n += 1
        print(f'  已带入旧 skill 的参考资料 → {dstdir}')

    # --force 只刷新工作台文件,**绝不**覆盖已有配置:里面是填好的密钥和白名单,
    # 用模板一覆盖就全丢了(2026-09-23 审查发现)。要重置配置请手工删掉再 init。
    if not paths.config_file.exists():
        _render(TEMPLATES / 'config.toml', paths.config_file, mapping)
        paths.config_file.chmod(0o600)
    elif a.force:
        print(f'  配置已存在,--force 不会覆盖它: {paths.config_file}')
    conn = dbm.connect(paths.db_file); conn.close()
    if trust_workspace(paths.workspace):
        print('  已把工作台标为可信(否则 settings.json 的允许清单会被忽略)')

    print(f'实例 {a.inst} 已初始化({n} 个文件):')
    print(f'  配置    {paths.config_file}   ← 填 app_id / app_secret / alert_open_id / group_allow')
    print(f'  工作台  {paths.workspace}')
    print(f'  库      {paths.db_file}')
    print('\n接下来:')
    print(f'  1. 编辑 {paths.config_file}(见第 4 节:飞书应用要开长连接并订阅 im.message.receive_v1)')
    print(f'  2. systemctl --user enable --now yizhan-listener@{a.inst} yizhan-dispatcher@{a.inst}.timer')
    print(f'  3. 把机器人拉进群,@ 一句验证;journalctl --user -u yizhan-listener@{a.inst} -f')
    return 0


def cmd_sync_skill(a) -> int:
    """仓库副本与工作台副本之间**显式**同步。两份平时隔离,不会自动跟随。"""
    cfg = configmod.load(a.inst)
    skill = a.skill or cfg.skill
    repo, ws = repo_skill_dir(a.inst, skill), workspace_skill_dir(cfg.paths, skill)
    src, dst = (repo, ws) if a.direction == 'to-workspace' else (ws, repo)
    if not src.is_dir():
        sys.exit(f'源不存在: {src}')
    if dst.exists() and not a.force:
        import filecmp
        d = filecmp.dircmp(str(src), str(dst))
        changed = d.diff_files + d.left_only + d.right_only
        if not changed:
            print('两份内容一致,无需同步')
            return 0
        print(f'将覆盖 {dst},受影响: {changed}')
        if not a.yes:
            sys.exit('加 --yes 确认覆盖')
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)
    print(f'已复制 {src} → {dst}')
    return 0


def cmd_rotate(a) -> int:
    cfg = configmod.load(a.inst)
    conn = dbm.connect(cfg.paths.db_file)
    old = dbm.session_get(conn)
    sid = dbm.session_rotate(conn, a.reason)
    print(f'会话已轮换: {old["session_id"] if old else "(无)"} → {sid}\n'
          '下一张卡会以新会话开始,首轮会先读记忆与未结卡。')
    return 0


def pause(inst: str) -> str:
    """停 timer 并落 paused 文件。web 与命令行共用这一份。"""
    cfg = configmod.load(inst)
    cfg.paths.ensure()
    cfg.paths.paused_file.write_text('paused\n')
    subprocess.run(['systemctl', '--user', 'stop', f'yizhan-dispatcher@{inst}.timer'],
                   check=False, capture_output=True, timeout=20)
    s = dbm.session_get(dbm.connect(cfg.paths.db_file))
    return s['session_id'] if s else ''


def resume(inst: str):
    cfg = configmod.load(inst)
    cfg.paths.paused_file.unlink(missing_ok=True)
    subprocess.run(['systemctl', '--user', 'start', f'yizhan-dispatcher@{inst}.timer'],
                   check=False, capture_output=True, timeout=20)


def cmd_pause(a) -> int:
    cfg = configmod.load(a.inst)
    sid = pause(a.inst)
    s = {'session_id': sid} if sid else None
    print(f'已暂停 {a.inst}。现在可以在终端接管这个会话:\n'
          f'  cd {cfg.paths.workspace} && claude --resume {s["session_id"] if s else "(还没有会话)"}\n'
          f'恢复: yizhan resume {a.inst}')
    return 0


def cmd_resume(a) -> int:
    resume(a.inst)
    print(f'已恢复 {a.inst}')
    return 0


def cmd_status(a) -> int:
    cfg = configmod.load(a.inst)
    conn = dbm.connect(cfg.paths.db_file)
    s = dbm.session_get(conn)
    hb = dbm.heartbeat_get(conn)
    import datetime
    fmt = lambda ms: datetime.datetime.fromtimestamp(ms / 1000).strftime('%m-%d %H:%M:%S') if ms else '(无)'
    print(f'实例 {a.inst}  项目 {cfg.project_dir}')
    print(f'  暂停中: {cfg.paths.paused_file.exists()}')
    print(f'  会话:   {s["session_id"] if s else "(还没有)"} fresh={s["fresh"] if s else "-"} '
          f'建于 {fmt(s["created_at"]) if s else "-"}')
    print(f'  心跳:   {fmt(hb)}')
    counts = dict(conn.execute('SELECT state, COUNT(*) FROM issues GROUP BY state').fetchall())
    print(f'  卡:     {counts or "(空)"}')
    for unit in (f'yizhan-listener@{a.inst}', f'yizhan-dispatcher@{a.inst}.timer'):
        r = subprocess.run(['systemctl', '--user', 'is-active', unit], capture_output=True, text=True)
        print(f'  {unit}: {r.stdout.strip() or "unknown"}')
    return 0


def cmd_migrate(a) -> int:
    """把旧 drama-auto 目录里的 members.json 导进库(名字→open_id,用于 @ 人)。"""
    cfg = configmod.load(a.inst)
    conn = dbm.connect(cfg.paths.db_file)
    src = Path(a.from_dir).expanduser()
    n = 0
    mf = src / 'members.json'
    if mf.exists():
        for name, oid in json.loads(mf.read_text(encoding='utf-8')).items():
            conn.execute('INSERT OR REPLACE INTO members(open_id,name) VALUES(?,?)', (oid, name)); n += 1
    print(f'导入成员 {n} 条。旧的 queue/threads/events/sent_log 不迁移(卡是新语义),原文件请自行归档。')
    return 0


def cmd_web_init(a) -> int:
    from .web import settings as websettings
    path = websettings.default_path()
    if path.exists() and not a.force:
        sys.exit(f'{path} 已存在。要重置口令加 --force')
    path, pw = websettings.write_initial(path, port=a.port, host=a.host)
    print(f'已生成 {path}\n  口令: {pw}   ← 只打印这一次,记下来\n  端口: {a.port}\n\n'
          f'启动: systemctl --user enable --now yizhan-web\n'
          f'访问: http://<本机地址>:{a.port}')
    return 0


def cmd_backfill_errors(a) -> int:
    """从 journald 回溯历史错误进 errors 表。可重复跑,不会重复记。

    errors 表是 2026-09-23 才加的,之前的探活失败只在 journal 里有;告警表每小时去重,不能用来统计。
    """
    import json as _json
    cfg = configmod.load(a.inst)
    conn = dbm.connect(cfg.paths.db_file)
    p = subprocess.run(['journalctl', '--user', '-u', f'yizhan-dispatcher@{a.inst}.service',
                        '-o', 'json', '--no-pager'], capture_output=True, text=True)
    # 每种故障只认「一次故障恰好一行」的那种日志(2026-09-23 审查发现原来会重复计):
    #  · 探活失败:alert() 每次调用恰好打一行 alert[probe_failed](发出的 warning 或去重跳过的 info);
    #    不认 main 的「探活失败,本轮不动任何卡」,那是同一次故障的第二行
    #  · 超时:按 idle=True / hard=True 区分,原来一律记成 hard_killed
    #  · 卡死:收尾那一行
    patterns = [('alert[probe_failed]', 'probe_failed'),
                ('超时被杀 idle=True', 'idle_killed'),
                ('超时被杀 idle=False hard=True', 'hard_killed'),
                ('收尾 → stuck', 'stuck')]
    # 运行时开始自己记 errors 之后,journal 里的同一次故障已经有记录了,只回溯那之前的历史
    r = conn.execute("SELECT MIN(at) FROM errors WHERE detail NOT LIKE '[journal 回溯]%'").fetchone()
    cutoff = r[0] if r and r[0] else None
    added = skipped = 0
    for line in p.stdout.splitlines():
        try:
            o = _json.loads(line)
        except ValueError:
            continue
        msg = o.get('MESSAGE')
        if isinstance(msg, list):
            msg = bytes(msg).decode('utf-8', 'replace')
        if not msg:
            continue
        kind = next((k for pat, k in patterns if pat in msg), None)
        if not kind:
            continue
        at = int(int(o['__REALTIME_TIMESTAMP']) / 1000)
        if cutoff and at >= cutoff:
            continue
        if conn.execute('SELECT 1 FROM errors WHERE kind=? AND at=?', (kind, at)).fetchone():
            skipped += 1
            continue
        dbm.record_error(conn, kind, '[journal 回溯] ' + msg.split('yizhan.')[-1][:300], at=at)
        added += 1
    print(f'回溯完成:新增 {added} 条,已存在跳过 {skipped} 条')
    return 0


def cmd_install_units(a) -> int:
    dst = Path(os.path.expanduser('~/.config/systemd/user'))
    dst.mkdir(parents=True, exist_ok=True)
    src = Path(__file__).resolve().parent.parent / 'systemd'
    repo = Path(__file__).resolve().parent.parent
    for f in src.glob('yizhan-*'):
        text = f.read_text().replace('{repo}', str(repo))
        (dst / f.name).write_text(text)
        print(f'  写入 {dst / f.name}')
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=False)
    print('已 daemon-reload。启用某实例:\n'
          '  systemctl --user enable --now yizhan-listener@<inst> yizhan-dispatcher@<inst>.timer')
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser('yizhan', description='驿站:飞书 → Claude Code 网关')
    sub = p.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('init', help='初始化一个实例(工作台骨架 + 配置模板)')
    s.add_argument('inst'); s.add_argument('--project', required=True)
    s.add_argument('--skill', help='默认 <inst>-feedback-group-reply')
    s.add_argument('--from-dir', dest='from_dir', help='旧 skill 目录,带入 reference/ 与旧 SKILL.md')
    s.add_argument('--force', action='store_true'); s.set_defaults(fn=cmd_init)
    s = sub.add_parser('sync-skill', help='仓库副本 ↔ 工作台副本,显式同步')
    s.add_argument('inst'); s.add_argument('--skill')
    s.add_argument('--direction', choices=['to-workspace', 'to-repo'], default='to-repo',
                   help='to-repo:把机器人改过的收回仓库(默认);to-workspace:用仓库那份覆盖工作台')
    s.add_argument('--yes', action='store_true'); s.add_argument('--force', action='store_true')
    s.set_defaults(fn=cmd_sync_skill)
    s = sub.add_parser('rotate', help='换一个新主会话'); s.add_argument('inst')
    s.add_argument('--reason', default='manual'); s.set_defaults(fn=cmd_rotate)
    s = sub.add_parser('pause', help='停 timer,交给人工接管'); s.add_argument('inst'); s.set_defaults(fn=cmd_pause)
    s = sub.add_parser('resume', help='恢复自动处理'); s.add_argument('inst'); s.set_defaults(fn=cmd_resume)
    s = sub.add_parser('status', help='看会话、心跳、卡与服务状态'); s.add_argument('inst'); s.set_defaults(fn=cmd_status)
    s = sub.add_parser('migrate', help='从旧 skill 目录导入成员名单'); s.add_argument('inst')
    s.add_argument('--from-dir', dest='from_dir', required=True); s.set_defaults(fn=cmd_migrate)
    s = sub.add_parser('web-init', help='生成 Web 控制台的配置与随机口令')
    s.add_argument('--port', type=int, default=8760)
    s.add_argument('--host', default='0.0.0.0')
    s.add_argument('--force', action='store_true', help='覆盖已有配置(会换口令)')
    s.set_defaults(fn=cmd_web_init)
    s = sub.add_parser('backfill-errors', help='从 journal 回溯历史错误进统计(可重复跑)')
    s.add_argument('inst'); s.set_defaults(fn=cmd_backfill_errors)
    s = sub.add_parser('install-units', help='把 systemd user 单元装到 ~/.config/systemd/user')
    s.set_defaults(fn=cmd_install_units)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == '__main__':
    raise SystemExit(main())
