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


def test_claude_env_injected(fcfg, tmp_path, monkeypatch):
    """systemd 下没有 shell 的代理变量,claude 会 403,所以配置里的 env 必须传进子进程。"""
    fcfg.claude_env = {'https_proxy': 'http://127.0.0.1:7890'}
    env = cr.build_env(fcfg)
    assert env['https_proxy'] == 'http://127.0.0.1:7890'
    assert env['TZ'] == 'Asia/Shanghai' and env['YIZHAN_INST'] == 'demo'
    assert 'CLAUDECODE' not in env


def test_big_stderr_does_not_hang(fcfg, tmp_path):
    """stderr 刷 200KB+:原来没人实时读,管道写满后进程卡死,最后被当成无响应杀掉。"""
    fcfg.idle_timeout_min = 0.5
    t0 = time.time()
    r = run(fcfg, tmp_path, mode='spam_stderr')
    assert r.ok and not r.idle_killed and time.time() - t0 < 10
    assert r.result['result'] == 'done'
    assert r.stderr.endswith('\n') and len(r.stderr) <= 4000         # 只留尾巴


def test_line_written_in_two_chunks_is_not_lost(fcfg, tmp_path):
    """半行不能被当成坏 JSON 丢掉,否则本轮成本和用量都记成空。"""
    r = run(fcfg, tmp_path, mode='split_line')
    assert r.result is not None and r.result['total_cost_usd'] == 0.5
    assert r.usage == {'output_tokens': 7}


def test_last_line_before_exit_is_kept(fcfg, tmp_path):
    for _ in range(5):                       # 退出竞态是偶发的,多跑几次
        r = run(fcfg, tmp_path, mode='ok')
        assert r.result is not None


def test_extra_dirs_become_add_dir(fcfg):
    fcfg.extra_dirs = ['/data/QTS/成片', '~/x']
    argv = cr.build_argv(fcfg, 'p', 'sid', True)
    i = [k for k, a in enumerate(argv) if a == '--add-dir']
    assert [argv[k + 1] for k in i] == [fcfg.project_dir, '/data/QTS/成片', os.path.expanduser('~/x')]


def test_context_window_takes_largest_model():
    r = {'modelUsage': {'claude-opus-5[1m]': {'contextWindow': 1000000},
                        'claude-haiku-4-5': {'contextWindow': 200000}}}
    assert cr.context_window(r) == 1000000
    assert cr.context_window({}) is None and cr.context_window(None) is None
