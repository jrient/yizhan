import json
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
