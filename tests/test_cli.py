import json
from pathlib import Path

from gateway import cli, db as dbm
from gateway.paths import Paths


def test_init_creates_workspace(inst_env, tmp_path, capsys):
    proj = tmp_path / 'myproj'; proj.mkdir()
    assert cli.main(['init', 'newinst', '--project', str(proj), '--skill', 'my-skill']) == 0
    p = Paths('newinst')
    ws = p.workspace
    assert (ws / 'CLAUDE.md').exists()
    skill = ws / '.claude' / 'skills' / 'my-skill' / 'SKILL.md'
    assert skill.exists() and 'fgctl card' in skill.read_text()
    ptoml = (ws / '.claude' / 'skills' / 'my-skill' / 'project.toml').read_text()
    assert str(proj) in ptoml
    cfgtxt = p.config_file.read_text()
    assert str(proj) in cfgtxt and 'my-skill' in cfgtxt
    assert oct(p.config_file.stat().st_mode)[-3:] == '600'
    assert json.loads((ws / '.claude' / 'settings.json').read_text())['permissions']['allow']
    assert p.db_file.exists()


def test_init_rejects_missing_project(inst_env, tmp_path):
    try:
        cli.main(['init', 'x', '--project', str(tmp_path / 'nope')])
        assert False
    except SystemExit as e:
        assert '不存在' in str(e)


def test_migrate_members(cfg, tmp_path, capsys):
    src = tmp_path / 'old'; src.mkdir()
    (src / 'members.json').write_text(json.dumps({'小白': 'ou_bai', '张三': 'ou_jf'}, ensure_ascii=False))
    assert cli.main(['migrate', 'demo', '--from-dir', str(src)]) == 0
    conn = dbm.connect(cfg.paths.db_file)
    assert dbm.members(conn) == {'小白': 'ou_bai', '张三': 'ou_jf'}


def test_rotate(cfg, capsys):
    assert cli.main(['rotate', 'demo', '--reason', 'test']) == 0
    conn = dbm.connect(cfg.paths.db_file)
    assert dbm.session_get(conn)['reason'] == 'test'


def test_init_from_dir_brings_reference(inst_env, tmp_path):
    proj = tmp_path / 'p'; proj.mkdir()
    old = tmp_path / 'oldskill'; (old / 'reference').mkdir(parents=True)
    (old / 'reference' / 'incidents.md').write_text('事故记录')
    (old / 'SKILL.md').write_text('旧流程')
    cli.main(['init', 'i2', '--project', str(proj), '--from-dir', str(old)])
    ref = Paths('i2').workspace / '.claude' / 'skills' / 'i2-feedback-group-reply' / 'reference'
    assert (ref / 'incidents.md').read_text() == '事故记录'
    assert (ref / 'legacy-skill.md').read_text() == '旧流程'


def _mk_repo_skill(tmp_path, monkeypatch, inst='demo', skill=None, body='仓库版'):
    skill = skill or cli.default_skill_name(inst)
    repo_skills = tmp_path / 'repo_skills'
    d = repo_skills / skill
    d.mkdir(parents=True)
    (d / 'SKILL.md').write_text(body)
    monkeypatch.setattr(cli, 'REPO_SKILLS', repo_skills)
    return d


def test_init_copies_repo_skill_not_symlink(inst_env, tmp_path, monkeypatch):
    _mk_repo_skill(tmp_path, monkeypatch, inst='inst9')
    proj = tmp_path / 'p9'; proj.mkdir()
    cli.main(['init', 'inst9', '--project', str(proj)])
    ws = Paths('inst9').workspace / '.claude' / 'skills' / 'inst9-feedback-group-reply'
    assert not ws.is_symlink()                       # 要真副本,不要软链
    assert (ws / 'SKILL.md').read_text() == '仓库版'
    # 改工作台那份不影响仓库那份
    (ws / 'SKILL.md').write_text('机器人改过')
    assert (tmp_path / 'repo_skills' / 'inst9-feedback-group-reply' / 'SKILL.md').read_text() == '仓库版'


def test_sync_skill_both_directions(cfg, tmp_path, monkeypatch, capsys):
    repo = _mk_repo_skill(tmp_path, monkeypatch, skill=cfg.skill)
    ws = cfg.paths.workspace / '.claude' / 'skills' / cfg.skill
    ws.mkdir(parents=True); (ws / 'SKILL.md').write_text('机器人改过')
    cli.main(['sync-skill', 'demo', '--direction', 'to-repo', '--yes'])
    assert (repo / 'SKILL.md').read_text() == '机器人改过'
    (repo / 'SKILL.md').write_text('人改过')
    cli.main(['sync-skill', 'demo', '--direction', 'to-workspace', '--yes'])
    assert (ws / 'SKILL.md').read_text() == '人改过'
    cli.main(['sync-skill', 'demo', '--direction', 'to-repo'])
    assert '一致' in capsys.readouterr().out


def test_sync_skill_refuses_without_yes(cfg, tmp_path, monkeypatch):
    repo = _mk_repo_skill(tmp_path, monkeypatch, skill=cfg.skill)
    ws = cfg.paths.workspace / '.claude' / 'skills' / cfg.skill
    ws.mkdir(parents=True); (ws / 'SKILL.md').write_text('不一样')
    try:
        cli.main(['sync-skill', 'demo', '--direction', 'to-repo'])
        assert False, '应该拒绝'
    except SystemExit as e:
        assert '--yes' in str(e)
    assert (repo / 'SKILL.md').read_text() == '仓库版'


def test_init_marks_workspace_trusted(inst_env, tmp_path, monkeypatch):
    """不标可信,claude 会忽略 settings.json 的整个 allow/deny 清单。"""
    fake_home = tmp_path / 'home'; fake_home.mkdir()
    monkeypatch.setenv('HOME', str(fake_home))
    (fake_home / '.claude.json').write_text('{"projects": {}}')
    proj = tmp_path / 'pt'; proj.mkdir()
    cli.main(['init', 'trustme', '--project', str(proj)])
    data = json.loads((fake_home / '.claude.json').read_text())
    ws = str(Paths('trustme').workspace)
    assert data['projects'][ws]['hasTrustDialogAccepted'] is True
    assert cli.trust_workspace(Paths('trustme').workspace) is False   # 已标过就不重复写


def test_web_init_writes_config_with_random_password(inst_env, capsys):
    assert cli.main(['web-init', '--port', '8899']) == 0
    from gateway.web import settings as ws
    p = ws.default_path()
    assert p.exists() and oct(p.stat().st_mode)[-3:] == '600'
    s = ws.load(p)
    assert s.port == 8899 and len(s.password) >= 12
    out = capsys.readouterr().out
    assert s.password in out and '只打印这一次' in out
    try:
        cli.main(['web-init'])
        assert False, '重复生成应该拒绝'
    except SystemExit as e:
        assert '已存在' in str(e)
    assert cli.main(['web-init', '--force']) == 0
    assert ws.load(p).password != s.password


def test_install_units_includes_web(inst_env, tmp_path, monkeypatch):
    fake_home = tmp_path / 'h'; (fake_home / '.config' / 'systemd' / 'user').mkdir(parents=True)
    monkeypatch.setenv('HOME', str(fake_home))
    monkeypatch.setattr(cli.subprocess, 'run', lambda *a, **k: None)
    cli.main(['install-units'])
    names = {p.name for p in (fake_home / '.config' / 'systemd' / 'user').glob('yizhan-*')}
    assert names == {'yizhan-listener@.service', 'yizhan-dispatcher@.service',
                     'yizhan-dispatcher@.timer', 'yizhan-web.service'}
    text = (fake_home / '.config' / 'systemd' / 'user' / 'yizhan-web.service').read_text()
    assert '-m gateway.web' in text and '{repo}' not in text


def _journal(lines):
    import json as _j
    out = []
    for us, msg in lines:
        out.append(_j.dumps({'MESSAGE': msg, '__REALTIME_TIMESTAMP': str(us)}))
    class R:
        stdout = '\n'.join(out)
    return R()


def test_backfill_counts_each_failure_once(cfg, monkeypatch, capsys):
    """一次探活失败在 journal 里有两行(告警一行 + main 一行),只能记一次。"""
    lines = [
        (1_000_000_000, 'INFO yizhan.alert alert[probe_failed] claude 探活失败,不动任何卡: 探活超时 30s'),
        (1_000_000_500, 'WARNING yizhan.dispatcher 探活失败,本轮不动任何卡'),
        (2_000_000_000, 'INFO yizhan.alert alert[probe_failed] 1h 内已发过,跳过: claude 探活失败'),
        (3_000_000_000, 'WARNING yizhan.dispatcher 卡 #3 超时被杀 idle=True hard=False'),
        (4_000_000_000, 'WARNING yizhan.dispatcher 卡 #4 超时被杀 idle=False hard=True'),
        (5_000_000_000, 'INFO yizhan.dispatcher 卡 #5 收尾 → stuck (rc=0)'),
    ]
    monkeypatch.setattr(cli.subprocess, 'run', lambda *a, **k: _journal(lines))
    cli.main(['backfill-errors', 'demo'])
    conn = dbm.connect(cfg.paths.db_file)
    got = dict(conn.execute('SELECT kind, COUNT(*) FROM errors GROUP BY kind').fetchall())
    assert got == {'probe_failed': 2, 'idle_killed': 1, 'hard_killed': 1, 'stuck': 1}
    cli.main(['backfill-errors', 'demo'])                  # 再跑一次不重复
    assert conn.execute('SELECT COUNT(*) FROM errors').fetchone()[0] == 5


def test_backfill_skips_period_already_recorded_at_runtime(cfg, monkeypatch):
    """运行时开始自己记之后,journal 里同一次故障已有记录,不能再回溯一遍。"""
    conn = dbm.connect(cfg.paths.db_file)
    dbm.record_error(conn, 'probe_failed', '运行时记录', at=2_000_000)       # 2_000_000 ms
    lines = [(1_000_000_000, 'alert[probe_failed] 早于运行时记录'),            # 1_000_000 ms → 回溯
             (3_000_000_000, 'alert[probe_failed] 晚于运行时记录')]            # 3_000_000 ms → 跳过
    monkeypatch.setattr(cli.subprocess, 'run', lambda *a, **k: _journal(lines))
    cli.main(['backfill-errors', 'demo'])
    assert conn.execute("SELECT COUNT(*) FROM errors WHERE detail LIKE '[journal 回溯]%'").fetchone()[0] == 1


def test_init_force_never_overwrites_config(cfg, capsys):
    """--force 用模板覆盖会把填好的密钥和白名单全丢掉。"""
    before = cfg.paths.config_file.read_text()
    cli.main(['init', 'demo', '--project', cfg.project_dir, '--force'])
    assert cfg.paths.config_file.read_text() == before
    assert '不会覆盖' in capsys.readouterr().out


def test_init_renders_project_path_into_settings(inst_env, tmp_path):
    """允许清单里原项目虚拟环境的绝对路径要按实例渲染,不能留着 {project_dir} 占位符。"""
    proj = tmp_path / 'pp'; proj.mkdir()
    cli.main(['init', 'setx', '--project', str(proj)])
    s = json.loads((Paths('setx').workspace / '.claude' / 'settings.json').read_text())
    assert f'Bash({proj}/.venv/bin/python:*)' in s['permissions']['allow']
    assert not any('{project_dir}' in r for r in s['permissions']['allow'])
    assert 'Bash(sed -i:*)' in s['permissions']['deny'] and 'Bash(sed:*)' not in s['permissions']['allow']
