#!/usr/bin/env python3
"""假 claude:按 FAKE_MODE 表现不同行为,供 runner / dispatcher 测试用。"""
import json, os, sys, time

mode = os.environ.get('FAKE_MODE', 'ok')
argv = sys.argv[1:]
# 记录调用,便于断言参数
rec = os.environ.get('FAKE_RECORD')
if rec:
    with open(rec, 'a') as f:
        f.write(json.dumps({'argv': argv, 'cwd': os.getcwd(),
                            'has_claudecode': 'CLAUDECODE' in os.environ,
                            'inst': os.environ.get('YIZHAN_INST')}) + '\n')

if mode == 'resume_fail' and '--resume' in argv:
    sys.stderr.write('No conversation found with session ID: x\n')
    sys.exit(1)
if mode == 'hang':
    time.sleep(300)
if mode == 'spam_stderr':
    # 写满 stderr 管道(远超 64KB)。没人实时读的话进程会卡死在这里
    for _ in range(4000):
        sys.stderr.write('warning: ' + 'x' * 60 + '\n')
    sys.stderr.flush()
if mode == 'split_line':
    # result 行分两次写,中间停一下:读的人只拿到半行时不能把它丢掉
    line = json.dumps({'type': 'result', 'subtype': 'success', 'total_cost_usd': 0.5,
                       'usage': {'output_tokens': 7}})
    sys.stdout.write(line[:20]); sys.stdout.flush(); time.sleep(0.6)
    sys.stdout.write(line[20:] + '\n'); sys.stdout.flush()
    sys.exit(0)
if mode == 'denied':
    # 本轮有操作被权限系统拒绝
    hook = os.environ.get('FAKE_HOOK')
    if hook:
        exec(open(hook).read(), {'argv': argv})
    print(json.dumps({'type': 'result', 'subtype': 'success', 'total_cost_usd': 0.1,
                      'usage': {'output_tokens': 3},
                      'permission_denials': [{'tool_name': 'Bash', 'tool_use_id': 't1',
                                              'tool_input': {'command': 'python3 - <<EOF'}}]}), flush=True)
    sys.exit(0)
if mode.startswith('compact_'):
    # /compact:result 恒报 success,真实结果在 system/status 里
    kind = mode[len('compact_'):]
    print(json.dumps({'type': 'system', 'subtype': 'status', 'status': 'compacting'}), flush=True)
    if kind == 'ok':
        print(json.dumps({'type': 'system', 'subtype': 'compact_boundary',
                          'compact_metadata': {'trigger': 'manual', 'pre_tokens': 400000, 'post_tokens': 9000}}), flush=True)
        st = {'compact_result': 'success'}
    elif kind == 'noop':
        st = {'compact_result': 'failed', 'compact_error': 'Not enough messages to compact.'}
    else:
        st = {'compact_result': 'failed', 'compact_error': 'API Error: overloaded'}
    print(json.dumps({'type': 'system', 'subtype': 'status', 'status': None, **st}), flush=True)
    print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False,
                      'result': st.get('compact_error', ''), 'usage': {'output_tokens': 0}}), flush=True)
    sys.exit(0)
if mode == 'crash':
    sys.stderr.write('boom\n'); sys.exit(2)

hook = os.environ.get('FAKE_HOOK')
if hook:
    exec(open(hook).read(), {'argv': argv})

print(json.dumps({'type': 'assistant', 'message': {
    'content': 'working',
    'usage': {'input_tokens': 10, 'cache_read_input_tokens': 25, 'cache_creation_input_tokens': 5}}}), flush=True)
print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False,
                  'result': 'done', 'total_cost_usd': 0.0123,
                  'usage': {'input_tokens': 10, 'output_tokens': 2}}), flush=True)
