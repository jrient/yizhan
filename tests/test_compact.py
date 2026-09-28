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


def _ready_session(cfg, ctx=400_000):
    conn = dbm.connect(cfg.paths.db_file)
    sid = dbm.session_rotate(conn, 'init')
    conn.execute('UPDATE session SET fresh=0, ctx_tokens=?', (ctx,))
    conn.commit()
    return sid


def _ctx(cfg):
    return dbm.session_get(dbm.connect(cfg.paths.db_file))['ctx_tokens']


def _errors(cfg):
    return dbm.connect(cfg.paths.db_file).execute("SELECT COUNT(*) FROM errors WHERE kind='compact_failed'").fetchone()[0]


def test_compact_resumes_session_with_slash_compact(ccfg, tmp_path, monkeypatch):
    monkeypatch.setenv('FAKE_MODE', 'compact_ok')
    sid = _ready_session(ccfg)
    assert compact.run(ccfg) == 'ok'
    assert _ctx(ccfg) == 9000                     # 压完记下新大小,下个整点不会再压
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


def test_compact_skips_small_context(ccfg, tmp_path):
    ccfg.compact_at_tokens = 300_000
    _ready_session(ccfg, ctx=299_999)
    assert compact.run(ccfg) == 'small'
    _ready_session(ccfg, ctx=None)                # 老会话还没记过大小:等下一张卡记上再说
    assert compact.run(ccfg) == 'small'
    assert not (tmp_path / 'rec.jsonl').exists()


def test_compact_threshold_zero_always_runs(ccfg, monkeypatch):
    monkeypatch.setenv('FAKE_MODE', 'compact_ok')
    ccfg.compact_at_tokens = 0
    _ready_session(ccfg, ctx=None)
    assert compact.run(ccfg) == 'ok'


def test_compact_not_enough_messages_is_noop_not_error(ccfg, monkeypatch):
    monkeypatch.setenv('FAKE_MODE', 'compact_noop')
    _ready_session(ccfg)
    assert compact.run(ccfg) == 'noop'
    assert _errors(ccfg) == 0


def test_compact_real_failure_counts_as_error_despite_result_success(ccfg, monkeypatch):
    monkeypatch.setenv('FAKE_MODE', 'compact_err')
    _ready_session(ccfg)
    assert compact.run(ccfg) == 'failed'
    assert _errors(ccfg) == 1
