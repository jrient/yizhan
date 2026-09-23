"""告警:机器人私聊 alert_open_id;同类 1 小时内不重复。"""
from __future__ import annotations

import logging

from . import db as dbm

log = logging.getLogger('yizhan.alert')
DEDUP_MS = 3600_000


def alert(conn, client, cfg, kind: str, text: str) -> bool:
    if dbm.alert_recent(conn, kind, DEDUP_MS):
        log.info('alert[%s] 1h 内已发过,跳过: %s', kind, text)
        return False
    dbm.alert_record(conn, kind, text)
    log.warning('alert[%s] %s', kind, text)
    if not cfg.alert_open_id or client is None:
        return False
    try:
        client.send_text(cfg.alert_open_id, f'[yizhan/{cfg.inst}] {kind}\n{text}')
        return True
    except Exception as e:  # 告警本身失败不能拖垮主流程
        log.error('告警发送失败: %s', e)
        return False
