"""飞书消息(事件推送或 API 列表项)→ EventRow。两条路径归一到同一结构。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EventRow:
    message_id: str
    chat_id: str
    chat_type: str | None
    create_time: int
    sender_id: str | None = None
    sender_type: str | None = None
    thread_id: str | None = None
    root_id: str | None = None
    parent_id: str | None = None
    msg_type: str | None = None
    text: str = ''
    image_keys: list[str] = field(default_factory=list)
    mentions: list[dict] = field(default_factory=list)
    mentions_me: bool = False
    raw: Any = None


def render_text(msg_type: str, content: str | None, mentions: list[dict]) -> tuple[str, list[str]]:
    """消息正文 → (纯文本, 图片 key 列表)。@ 占位符换成「@名字」。"""
    try:
        body = json.loads(content or '{}')
    except Exception:
        body = {}
    imgs: list[str] = []
    if msg_type == 'text':
        text = body.get('text', '')
        for mt in mentions:
            if mt.get('key'):
                text = text.replace(mt['key'], '@' + (mt.get('name') or mt.get('id') or ''))
    elif msg_type == 'post':
        text = (body.get('title') or '') + ('\n' if body.get('title') else '')
        for line in body.get('content', []):
            for e in line:
                tag = e.get('tag')
                if tag == 'text':
                    text += e.get('text', '')
                elif tag == 'img':
                    imgs.append(e['image_key'])
                elif tag == 'at':
                    text += '@' + (e.get('user_name') or e.get('user_id') or '')
                elif tag == 'a':
                    text += e.get('text', '') + '(' + e.get('href', '') + ')'
                elif tag == 'code_block':
                    text += '\n```\n' + e.get('text', '') + '\n```\n'
            text += '\n'
    elif msg_type == 'image':
        text = '[image]'
        if body.get('image_key'):
            imgs.append(body['image_key'])
    elif msg_type in ('file', 'audio', 'media', 'sticker'):
        text = f'[{msg_type}] ' + (body.get('file_name') or body.get('file_key') or '')
    else:
        text = f'[{msg_type}] ' + (content or '')[:300]
    return text.strip(), imgs


def _norm_mentions(ms) -> list[dict]:
    out = []
    for m in ms or []:
        if isinstance(m, dict):
            oid = (m.get('id') or {}).get('open_id') if isinstance(m.get('id'), dict) else m.get('id')
            out.append({'key': m.get('key'), 'id': oid, 'name': m.get('name')})
        else:  # lark_oapi 对象
            idobj = getattr(m, 'id', None)
            oid = getattr(idobj, 'open_id', None) if idobj is not None and not isinstance(idobj, str) else idobj
            out.append({'key': getattr(m, 'key', None), 'id': oid, 'name': getattr(m, 'name', None)})
    return out


def _me(mentions: list[dict], bot_open_id: str | None) -> bool:
    return bool(bot_open_id) and any(m.get('id') == bot_open_id for m in mentions)


def from_ws_event(data, bot_open_id: str | None) -> EventRow:
    """lark_oapi P2ImMessageReceiveV1 → EventRow。"""
    m = data.event.message
    s = data.event.sender
    mentions = _norm_mentions(m.mentions)
    text, imgs = render_text(m.message_type, m.content, mentions)
    sid = getattr(getattr(s, 'sender_id', None), 'open_id', None)
    raw = None
    try:
        raw = json.loads(getattr(data, 'raw', None).content) if getattr(data, 'raw', None) else None
    except Exception:
        raw = None
    return EventRow(message_id=m.message_id, chat_id=m.chat_id, chat_type=m.chat_type,
                    create_time=int(m.create_time or 0), sender_id=sid, sender_type=getattr(s, 'sender_type', None),
                    thread_id=m.thread_id or None, root_id=m.root_id or None, parent_id=m.parent_id or None,
                    msg_type=m.message_type, text=text, image_keys=imgs, mentions=mentions,
                    mentions_me=_me(mentions, bot_open_id), raw=raw)


def from_api_item(item: dict, chat_type: str | None, bot_open_id: str | None) -> EventRow:
    """GET /im/v1/messages 列表项 → EventRow(补拉路径)。"""
    mentions = _norm_mentions(item.get('mentions'))
    body = item.get('body') or {}
    text, imgs = render_text(item.get('msg_type'), body.get('content'), mentions)
    if item.get('deleted'):
        text = '[recalled]'
    sender = item.get('sender') or {}
    return EventRow(message_id=item['message_id'], chat_id=item['chat_id'], chat_type=chat_type,
                    create_time=int(item.get('create_time') or 0), sender_id=sender.get('id'),
                    sender_type=sender.get('sender_type'), thread_id=item.get('thread_id') or None,
                    root_id=item.get('root_id') or None, parent_id=item.get('parent_id') or None,
                    msg_type=item.get('msg_type'), text=text, image_keys=imgs, mentions=mentions,
                    mentions_me=_me(mentions, bot_open_id), raw=item)
