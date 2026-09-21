"""飞书开放平台 HTTP 客户端(tenant token)。限流 99991400 / HTTP 429 指数退避 3 次。"""
from __future__ import annotations

import json
import time
from pathlib import Path

import requests

BASE = 'https://open.feishu.cn/open-apis'


class FeishuError(Exception):
    def __init__(self, code, msg, data=None):
        super().__init__(f'feishu code={code} {msg}')
        self.code, self.msg, self.data = code, msg, data


class FeishuClient:
    def __init__(self, app_id: str, app_secret: str, timeout: int = 20):
        self.app_id, self.app_secret, self.timeout = app_id, app_secret, timeout
        self.http = requests.Session()
        self.http.trust_env = False
        self._token: str | None = None
        self._token_exp = 0.0
        self.sleep = time.sleep

    # ---- auth ----
    def token(self) -> str:
        if self._token and time.time() < self._token_exp:
            return self._token
        r = self.http.post(f'{BASE}/auth/v3/tenant_access_token/internal',
                           json={'app_id': self.app_id, 'app_secret': self.app_secret}, timeout=self.timeout).json()
        if r.get('code') != 0:
            raise FeishuError(r.get('code'), r.get('msg'))
        self._token = r['tenant_access_token']
        self._token_exp = time.time() + int(r.get('expire', 7200)) - 300
        return self._token

    def _req(self, method: str, path: str, *, params=None, json_body=None, raw=False, timeout=None):
        delay = 1.0
        for attempt in range(4):
            resp = self.http.request(method, f'{BASE}{path}', params=params, json=json_body,
                                     headers={'Authorization': 'Bearer ' + self.token()},
                                     timeout=timeout or self.timeout)
            if resp.status_code == 429:
                self.sleep(delay); delay *= 2; continue
            if raw:
                return resp
            try:
                r = resp.json()
            except ValueError:
                raise FeishuError(resp.status_code, resp.text[:200])
            if r.get('code') in (99991400, 99991405):
                self.sleep(delay); delay *= 2; continue
            if r.get('code') == 99991663 or r.get('code') == 99991668:  # token 失效
                self._token = None
                continue
            if r.get('code') != 0:
                raise FeishuError(r.get('code'), r.get('msg'), r.get('data'))
            return r.get('data') or {}
        raise FeishuError(429, '限流重试耗尽')

    # ---- bot / chat ----
    def bot_open_id(self) -> str:
        d = self._req('GET', '/bot/v3/info')
        return (d.get('bot') or {}).get('open_id') or ''

    def chat_info(self, chat_id: str) -> dict:
        return self._req('GET', f'/im/v1/chats/{chat_id}')

    def chat_members(self, chat_id: str) -> dict[str, str]:
        """{open_id: name};没权限时返回 {}。"""
        try:
            d = self._req('GET', f'/im/v1/chats/{chat_id}/members', params={'page_size': 100})
        except FeishuError:
            return {}
        return {m['member_id']: m['name'] for m in d.get('items', [])}

    # ---- messages ----
    def list_messages(self, container_type: str, container_id: str, *, page_size=50, sort='ByCreateTimeAsc',
                      start_ms: int | None = None, end_ms: int | None = None, max_pages: int = 20) -> list[dict]:
        params = {'container_id_type': container_type, 'container_id': container_id,
                  'sort_type': sort, 'page_size': page_size}
        if start_ms:
            params['start_time'] = str(start_ms // 1000)
        if end_ms:
            params['end_time'] = str(end_ms // 1000)
        items: list[dict] = []
        for _ in range(max_pages):
            d = self._req('GET', '/im/v1/messages', params=params)
            items.extend(d.get('items') or [])
            if not d.get('has_more') or not d.get('page_token'):
                break
            params['page_token'] = d['page_token']
        return items

    def get_message(self, message_id: str) -> dict | None:
        d = self._req('GET', f'/im/v1/messages/{message_id}')
        it = d.get('items') or []
        return it[0] if it else None

    def _post_payload(self, content: list[list[dict]]) -> dict:
        return {'msg_type': 'post', 'content': json.dumps({'zh_cn': {'content': content}}, ensure_ascii=False)}

    def send_post(self, chat_id: str, content: list[list[dict]]) -> str:
        d = self._req('POST', '/im/v1/messages', params={'receive_id_type': 'chat_id'},
                      json_body={'receive_id': chat_id, **self._post_payload(content)})
        return d.get('message_id', '')

    def send_text(self, receive_id: str, text: str, id_type: str = 'open_id') -> str:
        d = self._req('POST', '/im/v1/messages', params={'receive_id_type': id_type},
                      json_body={'receive_id': receive_id, 'msg_type': 'text',
                                 'content': json.dumps({'text': text}, ensure_ascii=False)})
        return d.get('message_id', '')

    def reply_post(self, message_id: str, content: list[list[dict]], in_thread: bool = False) -> str:
        body = self._post_payload(content)
        if in_thread:
            body['reply_in_thread'] = True
        d = self._req('POST', f'/im/v1/messages/{message_id}/reply', json_body=body)
        return d.get('message_id', '')

    def edit_post(self, message_id: str, content: list[list[dict]]):
        self._req('PUT', f'/im/v1/messages/{message_id}', json_body=self._post_payload(content))

    def react(self, message_id: str, emoji: str = 'Typing') -> str:
        d = self._req('POST', f'/im/v1/messages/{message_id}/reactions',
                      json_body={'reaction_type': {'emoji_type': emoji}})
        return d.get('reaction_id', '')

    def unreact(self, message_id: str, reaction_id: str):
        try:
            self._req('DELETE', f'/im/v1/messages/{message_id}/reactions/{reaction_id}')
        except FeishuError:
            pass

    def download(self, message_id: str, key: str, dest: Path, kind: str = 'image') -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            return dest
        resp = self._req('GET', f'/im/v1/messages/{message_id}/resources/{key}', params={'type': kind},
                         raw=True, timeout=60)
        if resp.status_code != 200:
            raise FeishuError(resp.status_code, resp.text[:200])
        dest.write_bytes(resp.content)
        return dest
