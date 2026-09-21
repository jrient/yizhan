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
