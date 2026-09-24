"""所有实例共用的一份「本来就不许做的事」。

只在仓库 shared/shared-rules.md 维护一份;dispatcher 每次唤起 claude 前同步到工作台,
并保证工作台 CLAUDE.md 用 `@shared-rules.md` 引用它(2026-09-24 需求:禁止事项写成公共记忆,
机器人一开始就不去试,而不是被拒之后再学)。

文件名必须是英文:CLAUDE.md 里 @ 引用中文文件名在 claude -p 下不生效(2026-09-24 实测)。
"""
from __future__ import annotations

import logging
from pathlib import Path

from .paths import REPO_ROOT

log = logging.getLogger('yizhan.shared_rules')

SOURCE = REPO_ROOT / 'shared' / 'shared-rules.md'
NAME = 'shared-rules.md'
IMPORT_LINE = '@' + NAME
IMPORT_BLOCK = f'\n## 公共规矩\n\n所有机器人通用的禁止事项和写法,务必遵守:\n\n{IMPORT_LINE}\n'


def sync(workspace: Path) -> bool:
    """把公共规矩同步进工作台。有改动返回 True。出错只记日志,不挡住处理卡。"""
    try:
        if not SOURCE.is_file():
            log.warning('公共规矩源文件不存在: %s', SOURCE)
            return False
        changed = False
        content = SOURCE.read_text(encoding='utf-8')
        dst = Path(workspace) / NAME
        if not dst.exists() or dst.read_text(encoding='utf-8') != content:
            dst.write_text(content, encoding='utf-8')
            changed = True
        cm = Path(workspace) / 'CLAUDE.md'
        text = cm.read_text(encoding='utf-8') if cm.exists() else '# 机器人工作台\n'
        if IMPORT_LINE not in text.split():
            cm.write_text(text.rstrip('\n') + '\n' + IMPORT_BLOCK, encoding='utf-8')
            changed = True
        if changed:
            log.info('公共规矩已同步到 %s', workspace)
        return changed
    except OSError as e:
        log.warning('同步公共规矩失败: %s', e)
        return False
