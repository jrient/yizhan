"""发言前的证据门:说不出证据就发不出去。迁自 drama-auto fg.py。

写进 memory 没用——memory 是被动召回的背景材料,不构成执行门,在「群里有人等」的压力下想不起来。
"""
from __future__ import annotations

import re


class EvidenceError(Exception):
    pass


_WEASEL = ['凭经验', '凭印象', '常识', '我记得', '应该是', '推测', '猜', '一般来说', '按理', '大概', '估计', '差不多']
_EMPTY = {'无', '没有', 'n/a', 'na', '-', '略', '同上'}

TYPES = {
    'frame':  ('画面内容:画错/漏拍/多人/背景不对/朝向站位', '帧文件名 + 秒数',
               r'\.jpe?g|\.png|第\s*\d+\s*秒|\bt?\d+s\b|抽帧'),
    'config': ('提示词、配置、版本现状', '库里的行 id / 文件名+行号',
               r'id\s*=|SELECT|prompt_history|prompts|\.py|\.ts|\.tsx|:\d+|库查|查库'),
    'time':   ('任何时间点', 'date 输出或 create_time 字段',
               r'date|create_time|created_at|UTC|CST|\d{1,2}:\d{2}'),
    'error':  ('失败原因', '实发 payload 或 error 字段原文',
               r'error|payload|Error|返回|状态码|\d{3}\b|traceback'),
    'script': ('剧本内容', '那一集原文的场号与原句', r'\d+-\d+|原文|剧本|正文|episode|manifest'),
    'other':  ('其他(答疑、操作指引、转述已确认的事)', '出处即可', None),
}

CHECKLIST = '\n'.join(f"  {k:<7} {v[0]}\n{'':<10}-> 证据里必须有:{v[1]}" for k, v in TYPES.items()) + """

  另外两条通用规则(不校验,但必须做到):
  · 「是模型随机」只能是排除一切之后的最后结论,不能是第一反应
  · 用了 grep/awk 过滤,先看一眼未过滤的原始输出,确认没丢东西"""


def check(kinds_raw: str | None, ev: str | None, text: str) -> tuple[str, str]:
    """返回 (类型串, 证据串);不合格抛 EvidenceError。"""
    kinds = [k.strip() for k in (kinds_raw or '').replace(',', ' ').split() if k.strip()]
    ev = (ev or '').strip()
    if not kinds:
        raise EvidenceError('拒发:缺 --type。这条回答的是哪一类问题?\n\n' + CHECKLIST +
                            '\n\n多类用逗号分隔。但先想一下:一条消息只回一个问题,真需要好几类吗?')
    bad = [k for k in kinds if k not in TYPES]
    if bad:
        raise EvidenceError(f'拒发:未知的 --type {bad}。可选:{list(TYPES)}')
    if not ev:
        raise EvidenceError('拒发:缺 --evidence。这条回复的每个结论,证据出处是什么?\n'
                            '写不出来,说明是在猜 —— 那就别发,去查,或者改口说「这条我还没核实」。\n\n'
                            + '\n'.join(f'  {k} 需要:{TYPES[k][1]}' for k in kinds))
    low = ev.lower()
    if low in _EMPTY:
        raise EvidenceError('拒发:证据是空的。没有出处就不要发,先去查。')
    hit = [w for w in _WEASEL if w in low]
    if hit:
        raise EvidenceError(f'拒发:证据里出现了托词 {hit}。这不是出处,是猜测的另一种说法。\n'
                            '要么去查拿到真出处,要么在正文里明说「这条我没核实」再发。')
    if len(ev) < 12:
        raise EvidenceError(f'拒发:证据太短({len(ev)} 字),看不出是真查过还是应付。')
    for k in kinds:
        pat = TYPES[k][2]
        if pat and not re.search(pat, ev, re.I):
            raise EvidenceError(f'拒发:--type {k} 的证据里找不到「{TYPES[k][1]}」。\n  {TYPES[k][0]}\n'
                                f'  现在的证据:{ev[:120]}\n这类结论必须拿得出这一项,否则就是在凭印象说。')
    if re.search(r'已(经)?核实|已确认|确认过|逐字核对', text or '') and len(ev) < 25:
        raise EvidenceError(f'拒发:正文里写了「已核实/已确认」,但证据只有 {len(ev)} 字。\n'
                            '这四个字只能在真的逐字核对过之后用。')
    return ' '.join(kinds), ev
