"""listener:常驻长连接,只写库,@我时立即拉起 dispatcher。

不绑定单个群:收全部消息,准入由 dispatcher 归卡时判。丢弃本应用自己发的消息
(否则我方回复回流成卡,自问自答死循环)。
"""
from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time

from . import config as configmod, db as dbm
from .alerts import alert
from .events import EventRow, from_api_item, from_ws_event
from .feishu import FeishuClient, FeishuError

log = logging.getLogger('yizhan.listener')


HEARTBEAT_SECONDS = 60


def start_heartbeat(db_file, stop: threading.Event | None = None, period: float = HEARTBEAT_SECONDS):
    """定期打心跳。

    只在收到消息时打是不够的:群里安静十分钟,dispatcher 的 watchdog 就当 listener 死了,
    发告警并重启一个本来好好的进程(2026-09-21 14:07 实测发生过)。
    线程自己开一条连接 —— sqlite 的连接默认不许跨线程用。
    """
    stop = stop or threading.Event()

    def beat():
        conn = dbm.connect(db_file)
        try:
            while not stop.wait(period):
                try:
                    dbm.heartbeat_touch(conn)
                except Exception as e:
                    log.warning('心跳写入失败: %s', e)
        finally:
            conn.close()

    t = threading.Thread(target=beat, name='heartbeat', daemon=True)
    t.start()
    return stop, t


_member_sync_at: dict[str, float] = {}
MEMBER_SYNC_COOLDOWN = 600


def sync_members(conn, client, chat_id: str, force: bool = False) -> int:
    """把群成员的名字拉进 members 表。

    飞书消息事件只带发送人 open_id,不带名字;原来名字只能从「有人 @ 过他」里学到,
    没被 @ 过的人在控制台和 fgctl 里一直显示成 ou_xxx(2026-09-23)。
    同一个群 10 分钟内最多拉一次。
    """
    now = time.time()
    if not force and now - _member_sync_at.get(chat_id, 0) < MEMBER_SYNC_COOLDOWN:
        return 0
    _member_sync_at[chat_id] = now
    try:
        names = client.chat_members(chat_id)
    except Exception as e:
        log.warning('同步群成员失败 %s: %s', chat_id, e)
        return 0
    for oid, name in names.items():
        if oid and name:
            conn.execute('INSERT OR REPLACE INTO members(open_id,name) VALUES(?,?)', (oid, name))
    log.info('群 %s 同步成员 %d 人', chat_id, len(names))
    return len(names)


def trigger_dispatcher(inst: str):
    # --no-block 是必须的:dispatcher 是 oneshot,不加会一直等到它处理完整轮卡
    # (实测 2026-09-21:收到第一条消息就卡了 10 秒超时,期间收不了新事件)。
    try:
        subprocess.run(['systemctl', '--user', '--no-block', 'start', f'yizhan-dispatcher@{inst}.service'],
                       check=False, capture_output=True, timeout=10)
    except Exception as e:
        log.warning('拉起 dispatcher 失败: %s', e)


def handle_message(conn, cfg, ev: EventRow, bot_open_id: str, trigger=None, client=None) -> bool:
    """返回是否写入了新事件。"""
    if ev.sender_type == 'app' or ev.sender_id == bot_open_id:
        return False                                   # 自身/其他机器人的消息一律丢弃
    dbm.heartbeat_touch(conn)
    if not dbm.insert_event(conn, ev):
        return False
    allowed = ev.chat_id in cfg.group_allow if ev.chat_type != 'p2p' else ev.sender_id in cfg.p2p_allow
    dbm.upsert_chat(conn, ev.chat_id, ev.chat_type, allowed=allowed, active_ms=ev.create_time)
    if ev.thread_id:
        dbm.touch_thread(conn, ev.thread_id, ev.chat_id, ev.root_id, ev.create_time)
    dbm.set_cursor(conn, ev.chat_id, ev.create_time)
    if client is not None and ev.chat_type != 'p2p' and ev.sender_id and not dbm.member_name(conn, ev.sender_id):
        sync_members(conn, client, ev.chat_id)          # 见到不认识的人就补一次名单
    participated = dbm.participated_threads(conn)
    wake = ev.mentions_me or (ev.thread_id in participated if ev.thread_id else False) or ev.chat_type == 'p2p'
    log.info('event %s chat=%s thread=%s @me=%s wake=%s', ev.message_id, ev.chat_id, ev.thread_id, ev.mentions_me, wake)
    if wake and trigger:
        trigger(cfg.inst)
    return True


def handle_bot_added(conn, cfg, client, chat_id: str, chat_type='group'):
    is_new = dbm.upsert_chat(conn, chat_id, chat_type, allowed=chat_id in cfg.group_allow)
    sync_members(conn, client, chat_id, force=True)
    if chat_id not in cfg.group_allow:
        alert(conn, client, cfg, f'new_chat:{chat_id}',
              f'已进入新群 {chat_id},当前不在 group_allow,不会响应。要启用请加进 '
              f'{cfg.paths.config_file} 的 group_allow 并重启 listener。')
    return is_new


def backfill(conn, cfg, client: FeishuClient, bot_open_id: str) -> int:
    """启动补拉:长连接断开期间的消息按不重投设计,自己按 cursor 拉差量。"""
    n = 0
    for chat_id in cfg.group_allow:
        since = dbm.get_cursor(conn, chat_id)
        try:
            items = client.list_messages('chat', chat_id, start_ms=since + 1 if since else None, page_size=50)
        except FeishuError as e:
            log.warning('补拉 %s 失败: %s', chat_id, e)
            continue
        for it in items:
            ev = from_api_item(it, 'group', bot_open_id)
            if handle_message(conn, cfg, ev, bot_open_id, trigger=None):
                n += 1
        # 话题内的回复不出现在 chat 容器列表里,按已知话题再拉一遍
        for t in conn.execute('SELECT thread_id FROM threads WHERE chat_id=? AND last_active>=?',
                              (chat_id, dbm.now_ms() - 3 * 86400_000)).fetchall():
            try:
                for it in client.list_messages('thread', t[0], page_size=50,
                                               start_ms=dbm.get_cursor(conn, t[0]) + 1 or None):
                    ev = from_api_item(it, 'group', bot_open_id)
                    if handle_message(conn, cfg, ev, bot_open_id, trigger=None):
                        n += 1
                    dbm.set_cursor(conn, t[0], ev.create_time)
            except FeishuError as e:
                log.warning('补拉话题 %s 失败: %s', t[0], e)
    log.info('补拉入库 %d 条', n)
    if n:
        trigger_dispatcher(cfg.inst)
    return n


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print('用法: python -m gateway.listener <inst>', file=sys.stderr)
        return 2
    cfg = configmod.load(argv[0])
    cfg.paths.ensure()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    conn = dbm.connect(cfg.paths.db_file)
    client = FeishuClient(cfg.app_id, cfg.app_secret)
    bot = client.bot_open_id()
    log.info('bot open_id=%s;群白名单=%s', bot, cfg.group_allow)
    dbm.heartbeat_touch(conn)
    start_heartbeat(cfg.paths.db_file)
    for chat_id in cfg.group_allow:
        sync_members(conn, client, chat_id, force=True)
    backfill(conn, cfg, client, bot)

    import lark_oapi as lark

    def on_message(data):
        try:
            handle_message(conn, cfg, from_ws_event(data, bot), bot, trigger=trigger_dispatcher, client=client)
        except Exception as e:
            log.exception('处理消息失败: %s', e)

    def on_bot_added(data):
        try:
            chat_id = data.event.chat_id
            handle_bot_added(conn, cfg, client, chat_id)
        except Exception as e:
            log.exception('处理进群事件失败: %s', e)

    def ignore(_data):
        """订阅里带上但我们不处理的事件。

        不注册的话 SDK 每收到一条就打两行 ERROR「processor not found」,
        日志页翻不动(2026-09-21 截图时满屏都是表情事件)。
        """
        return None

    builder = (lark.EventDispatcherHandler.builder('', '')
               .register_p2_im_message_receive_v1(on_message))
    for name in ('register_p2_im_message_reaction_created_v1',
                 'register_p2_im_message_reaction_deleted_v1',
                 'register_p2_im_message_recalled_v1'):
        fn = getattr(builder, name, None)
        if fn:
            builder = fn(ignore)
    for name in ('register_p2_im_chat_member_bot_added_v1', 'register_p2_im_chat_member_bot_deleted_v1'):
        fn = getattr(builder, name, None)
        if fn and 'added' in name:
            builder = fn(on_bot_added)
    handler = builder.build()
    ws = lark.ws.Client(cfg.app_id, cfg.app_secret, event_handler=handler, log_level=lark.LogLevel.INFO)
    log.info('长连接启动 inst=%s', cfg.inst)
    ws.start()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
