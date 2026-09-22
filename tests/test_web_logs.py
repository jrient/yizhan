import json
import threading
import time

import pytest

from gateway.web import logs


def test_parse_journal_line():
    line = json.dumps({'MESSAGE': 'hello', '__REALTIME_TIMESTAMP': '1789972800123456', 'PRIORITY': '4'})
    ev = logs.parse_journal_line(line)
    assert ev['text'] == 'hello' and ev['level'] == 'warn' and ev['at'] == 1789972800123
    assert logs.parse_journal_line('not json') is None
    assert logs.parse_journal_line(json.dumps({'PRIORITY': '6'})) is None


def test_parse_journal_binary_message():
    ev = logs.parse_journal_line(json.dumps({'MESSAGE': list(b'hi'), 'PRIORITY': '6'}))
    assert ev['text'] == 'hi'


def test_parse_stream_line():
    a = logs.parse_stream_line(json.dumps({'type': 'assistant', 'message': {'content': [
        {'type': 'text', 'text': '在查了'},
        {'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'fgctl card 1'}}]}}))
    assert '在查了' in a['text'] and '· Bash' in a['text'] and 'fgctl card 1' in a['text']
    r = logs.parse_stream_line(json.dumps({'type': 'result', 'subtype': 'success', 'duration_ms': 40148,
                                           'total_cost_usd': 0.6357, 'usage': {'output_tokens': 700}}))
    assert '[result] success' in r['text'] and '40s' in r['text'] and '$0.6357' in r['text']
    assert logs.parse_stream_line(json.dumps({'type': 'system'})) is None
    assert logs.parse_stream_line('{bad') is None


def test_result_error_is_error_level():
    r = logs.parse_stream_line(json.dumps({'type': 'result', 'subtype': 'error_max_turns', 'is_error': True}))
    assert r['level'] == 'error'


def test_tail_file_backfill_since_and_filter(tmp_path):
    p = tmp_path / 'claude-stream.jsonl'
    rows = [{'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': t}]}}
            for t in ('第一条', '第二条 keyword', '第三条')]
    p.write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
    got = list(logs.tail_file(p, _once=True))
    assert [g['text'] for g in got] == ['第一条', '第二条 keyword', '第三条']
    got = list(logs.tail_file(p, q='keyword', _once=True))
    assert [g['text'] for g in got] == ['第二条 keyword']
    got = list(logs.tail_file(p, since_ms=int(time.time() * 1000) + 60_000, _once=True))
    assert got == []


def test_tail_file_missing_file(tmp_path):
    assert list(logs.tail_file(tmp_path / 'nope.jsonl', _once=True)) == []


def test_tail_journal_uses_popen(monkeypatch):
    seen = {}
    class FakeProc:
        stdout = iter([json.dumps({'MESSAGE': 'a', 'PRIORITY': '6'}),
                       json.dumps({'MESSAGE': 'b keyword', 'PRIORITY': '3'})])
        def terminate(self): seen['terminated'] = True
    def fake_popen(argv, **kw):
        seen['argv'] = argv
        return FakeProc()
    monkeypatch.setattr(logs.subprocess, 'Popen', fake_popen)
    got = list(logs.tail_journal('yizhan-listener@demo.service', q='keyword'))
    assert [g['text'] for g in got] == ['b keyword'] and got[0]['level'] == 'error'
    assert '-f' in seen['argv'] and '-o' in seen['argv'] and seen['terminated']


def test_stream_events_rejects_unknown(cfg):
    with pytest.raises(ValueError):
        list(logs.stream_events('demo', 'syslog'))


def test_stream_events_claude_reads_file(cfg):
    (cfg.paths.logs / 'claude-stream.jsonl').write_text(
        json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': '你好'}]}}) + '\n')
    gen = logs.stream_events('demo', 'claude')
    assert next(gen)['text'] == '你好'
    gen.close()


def test_stream_events_falls_back_without_journal(cfg, monkeypatch):
    monkeypatch.setattr(logs, 'journal_available', lambda: False)
    (cfg.paths.logs / 'dispatcher.log').write_text('2026-09-21 14:00:00,1 ERROR yizhan.dispatcher 炸了\n')
    gen = logs.stream_events('demo', 'dispatcher')
    first = next(gen)
    assert '退回读' in first['text']
    second = next(gen)
    assert '炸了' in second['text'] and second['level'] == 'error'
    gen.close()
    gen = logs.stream_events('demo', 'listener')
    assert 'journalctl' in next(gen)['text']
    gen.close()


# ---- 停止:断线时不能对着正在执行的生成器调 close() ----
def test_tail_file_stops_on_event(tmp_path):
    """跟文件的循环本来只能靠 close() 打断,现在给 stop:先补完历史再收尾。"""
    p = tmp_path / 'x.log'
    p.write_text('2026-09-22 14:00:00,1 INFO 老的一行\n')
    stop = threading.Event()
    out = []
    t = threading.Thread(target=lambda: out.extend(
        logs.tail_file(p, parse=logs._parse_plain, poll=0.01, stop=stop)))
    t.start()
    time.sleep(0.05)
    stop.set()
    t.join(5)
    assert not t.is_alive() and [o['text'] for o in out] == ['2026-09-22 14:00:00,1 INFO 老的一行']


def test_tail_journal_stop_terminates_process(monkeypatch):
    stop = threading.Event()
    seen = {}
    class Blocking:
        """stdout 一直不出行,只有 terminate 能解开 —— 真的 journalctl -f 就是这样。"""
        def __init__(self):
            self.done = threading.Event()
            self.stdout = self._lines()
        def _lines(self):
            self.done.wait(5)
            return
            yield                                        # 让它是生成器,别在构造时就跑
        def terminate(self):
            seen['terminated'] = True
            self.done.set()
        def poll(self): return None
    proc = Blocking()
    monkeypatch.setattr(logs.subprocess, 'Popen', lambda argv, **kw: proc)
    out = []
    t = threading.Thread(target=lambda: out.extend(
        logs.tail_journal('yizhan-listener@demo.service', stop=stop)))
    t0 = time.time()
    t.start()
    time.sleep(0.05)                                     # 先让它真的堵在读 stdout 上
    stop.set()
    t.join(5)
    assert not t.is_alive() and seen.get('terminated') and out == []
    assert time.time() - t0 < 2                          # 是 stop 解的,不是等超时


def test_stream_events_passes_stop_down(cfg, monkeypatch):
    seen = {}
    monkeypatch.setattr(logs, 'journal_available', lambda: True)
    def fake_tail(unit, since_ms=0, q=None, lines=200, stop=None):
        seen['stop'] = stop
        yield {'at': 1, 'level': 'info', 'text': 'x'}
    monkeypatch.setattr(logs, 'tail_journal', fake_tail)
    stop = threading.Event()
    gen = logs.stream_events('demo', 'listener', stop=stop)
    next(gen)
    gen.close()
    assert seen['stop'] is stop
