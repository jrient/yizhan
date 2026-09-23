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
    for f in (STATIC / 'tokens.css', STATIC / 'base.css'):
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
