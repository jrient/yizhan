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
    ref = Paths('i2').workspace / '.claude' / 'skills' / 'feedback-group-reply' / 'reference'
    assert (ref / 'incidents.md').read_text() == '事故记录'
    assert (ref / 'legacy-skill.md').read_text() == '旧流程'
