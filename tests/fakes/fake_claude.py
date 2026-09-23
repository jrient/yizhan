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
