import json
from pathlib import Path

import pytest

from gateway import compact, db as dbm, dispatcher

FAKE = str(Path(__file__).parent / 'fakes' / 'fake_claude.py')


@pytest.fixture
def ccfg(cfg, tmp_path, monkeypatch):
    cfg.claude_bin = FAKE
    cfg.idle_timeout_min = 0.5
    cfg.paths.ensure()
    monkeypatch.setenv('FAKE_MODE', 'ok')
    monkeypatch.setenv('FAKE_RECORD', str(tmp_path / 'rec.jsonl'))
    return cfg


def _ready_session(cfg):
    conn = dbm.connect(cfg.paths.db_file)
    sid = dbm.session_rotate(conn, 'init')
    conn.execute('UPDATE session SET fresh=0')
    conn.commit()
    return sid


def test_compact_resumes_session_with_slash_compact(ccfg, tmp_path):
    sid = _ready_session(ccfg)
    assert compact.run(ccfg) == 'ok'
    argv = json.loads((tmp_path / 'rec.jsonl').read_text().splitlines()[-1])['argv']
    assert '/compact' in argv and argv[argv.index('--resume') + 1] == sid


def test_compact_skips_fresh_or_paused(ccfg, tmp_path):
    assert compact.run(ccfg) == 'no_session'
    _ready_session(ccfg)
    ccfg.paths.paused_file.write_text('paused\n')
    assert compact.run(ccfg) == 'paused'
    assert not (tmp_path / 'rec.jsonl').exists()


def test_compact_gives_up_while_dispatcher_holds_lock(ccfg):
    _ready_session(ccfg)
    fh = dispatcher.acquire_lock(ccfg.paths.lock_file)
    try:
        assert compact.run(ccfg, wait_s=0.2, poll_s=0.05) == 'busy'
    finally:
        fh.close()
