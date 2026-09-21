"""文本 → 飞书 post 富文本 content。

飞书 text 消息不解析 Markdown(**xxx** 原样显示星号),post 支持 style:["bold"];post 没有表格标签,
Markdown 表格排成等宽文本塞进 code_block 才对得齐。@ 人必须发 <at user_id>,纯文本「@小白」不提醒。
迁自 drama-auto fg.py(2026-09)。
"""
from __future__ import annotations

import re

_BOLD_RE = re.compile(r'\*\*(.+?)\*\*', re.S)
_TABLE_ROW_RE = re.compile(r'^\s*\|.*\|\s*$')
_TABLE_SEP_RE = re.compile(r'^\s*\|[\s:|-]+\|\s*$')
_OID_RE = re.compile(r'@(ou_[0-9a-f]{16,})')


def at_index(members: dict[str, str]) -> dict[str, str]:
    """{可用来 @ 的写法(小写): open_id}。含全名、括号前的名、去掉 -实习 的名、括号内的名。"""
    idx: dict[str, str] = {}
    for name, oid in members.items():
        forms = {name}
        base = re.split(r'[（(]', name, maxsplit=1)[0].strip()
        if base:
            forms.add(base)
        forms.add(re.sub(r'-\s*实习$', '', name).strip())
        inner = re.search(r'[（(]([^（）()]+)[）)]', name)
        if inner:
            forms.add(inner.group(1).strip())
        for f in forms:
            if f:
                idx.setdefault(f.lower(), oid)
    return idx


def disp_width(t: str) -> int:
    w = 0
    for c in t or '':
        w += 2 if ('一' <= c <= '鿿' or '　' <= c <= '〿' or '＀' <= c <= '￯') else 1
    return w


def _pad(t, width):
    return (t or '') + ' ' * max(0, width - disp_width(t))


def render_table(rows: list[list[str]]) -> str:
    ncol = max(len(r) for r in rows)
    rows = [r + [''] * (ncol - len(r)) for r in rows]
    w = [max(disp_width(r[i]) for r in rows) for i in range(ncol)]
    out = []
    for n, r in enumerate(rows):
        out.append('  '.join(_pad(c, w[i]) for i, c in enumerate(r)).rstrip())
        if n == 0:
            out.append('  '.join('-' * w[i] for i in range(ncol)))
    return '\n'.join(out)


def split_tables(text: str) -> list[tuple[str, str]]:
    blocks, buf, tbl = [], [], []

    def flush_text():
        if buf:
            blocks.append(('text', '\n'.join(buf))); buf.clear()

    def flush_tbl():
        if tbl:
            rows = [[c.strip() for c in ln.strip().strip('|').split('|')] for ln in tbl if not _TABLE_SEP_RE.match(ln)]
            if len(rows) >= 2:
                flush_text(); blocks.append(('table', render_table(rows)))
            else:
                buf.extend(tbl)
            tbl.clear()

    for ln in (text or '').split('\n'):
        if _TABLE_ROW_RE.match(ln):
            tbl.append(ln)
        else:
            flush_tbl(); buf.append(ln)
    flush_tbl(); flush_text()
    return blocks


def line_elems(line: str, idx: dict[str, str]) -> list[dict]:
    elems: list[dict] = []
    names = sorted(idx, key=len, reverse=True)

    def emit_text(seg):
        last = 0
        for m in _BOLD_RE.finditer(seg):
            if m.start() > last:
                elems.append({'tag': 'text', 'text': seg[last:m.start()]})
            elems.append({'tag': 'text', 'text': m.group(1), 'style': ['bold']})
            last = m.end()
        if last < len(seg):
            elems.append({'tag': 'text', 'text': seg[last:]})

    pos = 0
    while pos < len(line):
        if line[pos] != '@':
            nxt = line.find('@', pos)
            if nxt < 0:
                emit_text(line[pos:]); break
            emit_text(line[pos:nxt]); pos = nxt
            continue
        m = _OID_RE.match(line[pos:])
        if m:
            elems.append({'tag': 'at', 'user_id': m.group(1)}); pos += m.end(); continue
        hit = None
        for n in names:
            if line[pos + 1:pos + 1 + len(n)].lower() == n:
                hit = n; break
        if hit:
            elems.append({'tag': 'at', 'user_id': idx[hit]}); pos += 1 + len(hit)
        else:
            emit_text('@'); pos += 1
    return elems or [{'tag': 'text', 'text': ''}]


def post_content(text: str, members: dict[str, str] | None = None) -> list[list[dict]]:
    idx = at_index(members or {})
    content: list[list[dict]] = []
    for kind, body in split_tables(text):
        if kind == 'table':
            content.append([{'tag': 'code_block', 'language': 'PLAIN', 'text': body}])
        else:
            content.extend(line_elems(ln, idx) for ln in body.split('\n'))
    return content or [[{'tag': 'text', 'text': ''}]]


def code_post_content(lead: str, body: str, members: dict[str, str] | None = None, language='PLAIN') -> list[list[dict]]:
    """一行引导语(可含 @)+ 一个代码块。"""
    return [line_elems(lead, at_index(members or {})), [{'tag': 'code_block', 'language': language, 'text': body}]]


def count_ats(content: list[list[dict]]) -> int:
    return sum(1 for line in content for e in line if e.get('tag') == 'at')
