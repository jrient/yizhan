"""前端静态资源的守门检查。

两类问题都真实发生过(2026-09-23):
- app.js 里在 ${…} 表达式内写了转义反引号,整个文件解析失败,所有页面的脚本一起失效;
- 引用了设计体系里不存在的 token(--chart-1),徽章没有颜色也没有任何报错。
页面是外包给 agy 写的,这两条在合并前必须机器检查,不能靠肉眼。
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / 'gateway' / 'web' / 'static'
OWN = [STATIC / 'app.js', STATIC / 'app.css']


@pytest.mark.skipif(shutil.which('node') is None, reason='没有 node')
def test_app_js_parses():
    r = subprocess.run(['node', '--check', str(STATIC / 'app.js')], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-800:]


def _defined_tokens() -> set[str]:
    out = set()
    for f in (STATIC / 'tokens.css', STATIC / 'base.css', STATIC / 'chart-tokens.css'):
        out |= set(re.findall(r'^\s*(--[a-z0-9-]+)\s*:', f.read_text(), re.M))
    return out


def test_every_css_var_used_is_defined():
    defined = _defined_tokens()
    missing = {}
    for f in OWN:
        text = re.sub(r'//[^\n]*|/\*.*?\*/', '', f.read_text(), flags=re.S)   # 注释里的示例不算
        for name in set(re.findall(r"var\((--[a-z0-9-]+)|css\('(--[a-z0-9-]+)'\)", text)):
            n = name[0] or name[1]
            if n not in defined:
                missing.setdefault(f.name, []).append(n)
    assert not missing, f'引用了设计体系里不存在的 token: {missing}'


def test_no_hardcoded_colors():
    for f in OWN:
        text = re.sub(r'//[^\n]*|/\*.*?\*/', '', f.read_text(), flags=re.S)
        # css() 用 'rgba(0, 0, 0, 0)' 当「token 没解析出来」的哨兵做比较,不是在涂颜色
        text = text.replace("'rgba(0, 0, 0, 0)'", '')
        hits = re.findall(r'#[0-9a-fA-F]{3,8}\b(?![-\w])|\brgba?\(', text)
        assert not hits, f'{f.name} 里有硬编码颜色: {hits[:5]}'


def test_no_escaped_template_placeholders():
    """普通模板串里的 \\${x} 会原样输出成 '${x}' 字面文本。"""
    text = (STATIC / 'app.js').read_text()
    assert '\\${' not in text and '\\`' not in text


UNTRUSTED = ('preview', 'text', 'summary', 'evidence', 'sender_name', 'note', 'detail', 'reason', 'label', 'name')


def test_untrusted_fields_are_escaped():
    """群消息来的字段必须过 esc() 才能进 innerHTML(2026-09-23 修过一次 XSS)。

    群里任何人发一条 <img onerror=…>,打开控制台的人就会执行它,而控制台能暂停服务、改配置。
    这里抓的是模板串里裸插的 ${x.preview} 这类写法。
    """
    text = (STATIC / 'app.js').read_text()
    raw = re.findall(r'\$\{\s*[\w.]+\.(' + '|'.join(UNTRUSTED) + r')\s*\}', text)
    assert not raw, f'这些字段没过 esc() 就插进了 HTML: {sorted(set(raw))}'


def test_list_fields_join_with_real_newline():
    """'\\\\n' 在 JS 源码里是反斜杠加 n。配置页用它拼名单,保存一次就把两个群合成一个不存在的 ID。"""
    text = (STATIC / 'app.js').read_text()
    assert "join('\\\\n')" not in text and 'join("\\\\n")' not in text


def test_zero_is_rendered_not_dropped():
    """整数 0 是有效值(轮换阈值 0=关)。用 `val || ''` 会把 0 渲染成空。"""
    text = (STATIC / 'app.js').read_text()
    assert "value=\"${esc(val || '')}\" style=\"width:100%\"" not in text


def test_error_slots_match_backend_order():
    """颜色跟着错误种类走:前端槽位必须和后端固定顺序一一对应,且不超过 8 类。"""
    from gateway.web.data import ERROR_ORDER
    text = (STATIC / 'app.js').read_text()
    m = re.search(r'const ERROR_SLOT = \{([^}]*)\}', text, re.S)
    slots = dict((k.strip(), int(v)) for k, v in re.findall(r'(\w+)\s*:\s*(\d+)', m.group(1)))
    assert len(ERROR_ORDER) <= 8
    assert slots == {k: i + 1 for i, k in enumerate(ERROR_ORDER)}


def test_no_stray_files_in_static():
    """设计代理两次在 static/ 留下改写用的临时脚本(fix_*.py、replace_js.js),会被当静态资源公开出去。"""
    expected = {'app.css', 'app.js', 'base.css', 'chart-tokens.css', 'htmx-ext-sse.js', 'htmx.min.js',
                'tokens.css', 'uplot.min.css', 'uplot.min.js', 'VERSIONS.md'}
    assert {p.name for p in STATIC.iterdir()} - expected == set()
