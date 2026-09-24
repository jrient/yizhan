from gateway import shared_rules as sr


def test_sync_copies_and_adds_import(tmp_path, monkeypatch):
    src = tmp_path / 'src.md'; src.write_text('# 公共规矩 v1\n')
    monkeypatch.setattr(sr, 'SOURCE', src)
    ws = tmp_path / 'ws'; ws.mkdir()
    (ws / 'CLAUDE.md').write_text('# 机器人工作台\n\n正文\n')
    assert sr.sync(ws) is True
    assert (ws / 'shared-rules.md').read_text() == '# 公共规矩 v1\n'
    text = (ws / 'CLAUDE.md').read_text()
    assert text.count('@shared-rules.md') == 1 and '正文' in text


def test_sync_is_idempotent_and_follows_source(tmp_path, monkeypatch):
    src = tmp_path / 'src.md'; src.write_text('v1\n')
    monkeypatch.setattr(sr, 'SOURCE', src)
    ws = tmp_path / 'ws'; ws.mkdir(); (ws / 'CLAUDE.md').write_text('# x\n')
    sr.sync(ws)
    assert sr.sync(ws) is False                       # 没变就不动
    (ws / 'shared-rules.md').write_text('机器人自己改的\n')
    assert sr.sync(ws) is True                        # 工作台里被改了也会被覆盖回去
    assert (ws / 'shared-rules.md').read_text() == 'v1\n'
    src.write_text('v2\n')
    sr.sync(ws)
    assert (ws / 'shared-rules.md').read_text() == 'v2\n'
    assert (ws / 'CLAUDE.md').read_text().count('@shared-rules.md') == 1


def test_missing_source_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, 'SOURCE', tmp_path / 'nope.md')
    ws = tmp_path / 'ws'; ws.mkdir()
    assert sr.sync(ws) is False


def test_repo_source_exists_and_filename_is_ascii():
    """中文文件名的 @ 引用在 claude -p 下不生效(2026-09-24 实测),必须英文名。"""
    assert sr.SOURCE.is_file()
    assert sr.NAME.isascii()


def test_template_claude_md_imports_shared_rules():
    from gateway.paths import REPO_ROOT
    t = (REPO_ROOT / 'gateway' / 'templates' / 'workspace' / 'CLAUDE.md').read_text()
    assert '@shared-rules.md' in t
