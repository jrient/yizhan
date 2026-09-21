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

    if not paths.config_file.exists() or a.force:
        _render(TEMPLATES / 'config.toml', paths.config_file, mapping)
        paths.config_file.chmod(0o600)
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


def cmd_pause(a) -> int:
    cfg = configmod.load(a.inst)
    cfg.paths.ensure()
    cfg.paths.paused_file.write_text('paused\n')
    subprocess.run(['systemctl', '--user', 'stop', f'yizhan-dispatcher@{a.inst}.timer'], check=False)
    s = dbm.session_get(dbm.connect(cfg.paths.db_file))
    print(f'已暂停 {a.inst}。现在可以在终端接管这个会话:\n'
          f'  cd {cfg.paths.workspace} && claude --resume {s["session_id"] if s else "(还没有会话)"}\n'
          f'恢复: yizhan resume {a.inst}')
    return 0


def cmd_resume(a) -> int:
    cfg = configmod.load(a.inst)
    cfg.paths.paused_file.unlink(missing_ok=True)
    subprocess.run(['systemctl', '--user', 'start', f'yizhan-dispatcher@{a.inst}.timer'], check=False)
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
    s = sub.add_parser('install-units', help='把 systemd user 单元装到 ~/.config/systemd/user')
    s.set_defaults(fn=cmd_install_units)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == '__main__':
    raise SystemExit(main())
