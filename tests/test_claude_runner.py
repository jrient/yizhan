import json
import os
import sys
import time
import pytest
from pathlib import Path

from gateway import claude_runner as cr

FAKE = str(Path(__file__).parent / 'fakes' / 'fake_claude.py')


@pytest.fixture
def fcfg(cfg, tmp_path):
    cfg.claude_bin = FAKE
    cfg.idle_timeout_min = 1
    cfg.hard_timeout_min = 1
    return cfg


def run(cfg, tmp_path, mode='ok', **kw):
    os.environ['FAKE_MODE'] = mode
    os.environ['FAKE_RECORD'] = str(tmp_path / 'rec.jsonl')
    os.environ['CLAUDECODE'] = '1'
    try:
        return cr.run_claude(cfg, cfg.paths.workspace, 'do it', 'sess-1', **kw)
    finally:
        os.environ.pop('CLAUDECODE', None)


def test_ok_run(fcfg, tmp_path):
    r = run(fcfg, tmp_path)
    assert r.ok and r.result['result'] == 'done' and r.usage['output_tokens'] == 2
    rec = json.loads((tmp_path / 'rec.jsonl').read_text().splitlines()[0])
    assert rec['has_claudecode'] is False           # 环境变量被剥掉
    assert rec['inst'] == 'demo'
    assert rec['cwd'] == str(fcfg.paths.workspace)  # cwd 是工作台
    assert '--resume' in rec['argv'] and '--add-dir' in rec['argv']
    assert '--dangerously-skip-permissions' in rec['argv']


def test_resume_failure_detected(fcfg, tmp_path):
    r = run(fcfg, tmp_path, mode='resume_fail')
    assert r.resume_failed and not r.ok


def test_new_session_uses_session_id_flag(fcfg, tmp_path):
    run(fcfg, tmp_path, resume=False)
    rec = json.loads((tmp_path / 'rec.jsonl').read_text().splitlines()[0])
    assert '--session-id' in rec['argv'] and '--resume' not in rec['argv']


def test_idle_timeout_kills(fcfg, tmp_path):
    fcfg.idle_timeout_min = 1 / 60  # 1 秒
    t0 = time.time()
    r = run(fcfg, tmp_path, mode='hang')
    assert r.idle_killed and time.time() - t0 < 30


def test_crash(fcfg, tmp_path):
    r = run(fcfg, tmp_path, mode='crash')
    assert not r.ok and r.rc == 2 and 'boom' in r.stderr


def test_log_file_written(fcfg, tmp_path):
    lf = tmp_path / 'run.log'
    os.environ['FAKE_MODE'] = 'ok'
    cr.run_claude(fcfg, fcfg.paths.workspace, 'x', None, log_file=lf)
    assert '"type": "result"' in lf.read_text()


def test_probe(fcfg):
    os.environ['FAKE_MODE'] = 'ok'
    ok, msg = cr.probe(fcfg)
    assert ok
    fcfg.claude_bin = '/nonexistent/claude'
    ok, msg = cr.probe(fcfg)
    assert not ok and '找不到' in msg


def test_context_tokens_takes_max_not_sum():
    """顶层 usage 是整轮累计,同一份上下文被反复计进 cache_read;窗口占用要取单条最大。"""
    lines = [
        {'type': 'assistant', 'message': {'usage': {'input_tokens': 2, 'cache_read_input_tokens': 40_000}}},
        {'type': 'assistant', 'message': {'usage': {'input_tokens': 2, 'cache_read_input_tokens': 44_000,
                                                    'cache_creation_input_tokens': 1_000}}},
        {'type': 'result', 'usage': {'cache_read_input_tokens': 482_546}},
    ]
    assert cr.context_tokens(lines) == 45_002
    assert cr.context_tokens([]) == 0


def test_run_reports_context_tokens(fcfg, tmp_path):
    r = run(fcfg, tmp_path)
    assert r.context_tokens == 40
