# 驿站 yizhan 网关实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 `docs/superpowers/specs/2026-09-21-yizhan-design.md` 落地 `gateway/` 包(listener / dispatcher / fgctl / yizhan CLI)、工作台模板与 systemd 单元,可用 `yizhan init` 裂变到任意项目。

**Architecture:** listener 常驻收 wss 事件只写 SQLite;dispatcher 由 timer/listener 拉起,归卡 → 串行 `claude -p --resume <sid>` 处理 → 收尾;fgctl 是 claude 会话内唯一写库/发消息入口。所有飞书 HTTP 走 `gateway/feishu.py`,所有库操作走 `gateway/db.py`,纯逻辑(准入、归并、状态机、证据门、富文本渲染)独立成模块便于单测。

**Tech Stack:** Python 3.14、sqlite3(WAL)、lark_oapi(仅 ws)、requests、tomllib、pytest;systemd user units。

---

## 文件结构

```
pyproject.toml                       包元数据 + 入口 yizhan / fgctl;依赖 lark-oapi requests;dev: pytest
gateway/__init__.py
gateway/paths.py                     实例路径:config/state/workspace/attachments/logs;可用 YIZHAN_CONFIG_DIR / YIZHAN_STATE_DIR 环境变量覆盖(测试用)
gateway/config.py                    Config dataclass + load(inst)
gateway/db.py                        schema、connect()、常用查询(events/chats/threads/issues/replies/session/cursor/heartbeat)
gateway/render.py                    文本 → 飞书 post content(@、**加粗**、表格→code_block)—— 迁自 fg.py
gateway/evidence.py                  证据门 TYPES / check(kinds, evidence, text) —— 迁自 fg.py,raise EvidenceError 而非 sys.exit
gateway/feishu.py                    FeishuClient:token、bot_info、send/reply(post)、react/unreact、list_messages、get_message、download_resource;限流指数退避
gateway/events.py                    事件/消息 JSON → EventRow(dataclass);正文还原(mentions 换 @名、post 拆 text/img)
gateway/cards.py                     纯逻辑:admit()、merge_key()、is_quiet();库操作:assign_cards()、pick_card()、transitions
gateway/alerts.py                    alert(db, client, cfg, kind, text) 同类 1h 去重
gateway/claude_runner.py             run_claude(...):剥环境变量、stream-json 空闲超时、硬上限、杀进程组、解析 result
gateway/listener.py                  `python -m gateway.listener <inst>`
gateway/dispatcher.py                `python -m gateway.dispatcher <inst>`
gateway/fgctl.py                     `fgctl <cmd>`(读 YIZHAN_INST 环境变量)
gateway/cli.py                       `yizhan init|rotate|pause|resume|status|migrate`
gateway/templates/workspace/CLAUDE.md
gateway/templates/workspace/.claude/settings.json
gateway/templates/workspace/.claude/skills/feedback-group-reply/SKILL.md
gateway/templates/workspace/.claude/skills/feedback-group-reply/project.toml
gateway/templates/config.toml
systemd/yizhan-listener@.service  systemd/yizhan-dispatcher@.service  systemd/yizhan-dispatcher@.timer
tests/conftest.py                    tmp 路径 fixture、假配置、内存库
tests/test_render.py test_evidence.py test_events.py test_cards.py test_db.py test_claude_runner.py test_fgctl.py test_dispatcher.py test_listener.py test_cli.py
```

## 任务

### Task 1: 包骨架 + 路径/配置
- `pyproject.toml`(setuptools,`[project.scripts] yizhan=gateway.cli:main fgctl=gateway.fgctl:main`),`uv venv .venv && uv pip install -e .[dev]`。
- `paths.py`: `Paths(inst)` 属性 config_file、state_dir、db_file、attachments、logs、workspace;环境变量覆盖。
- `config.py`: 字段 app_id、app_secret、project_dir、skill、alert_open_id、p2p_allow、group_allow、quiet_seconds=30、lock_minutes=25、max_redispatch=3、claude_bin=~/.local/bin/claude、permission_args=["--dangerously-skip-permissions"]、idle_timeout_min=15、hard_timeout_min=60。
- 测试:tmp 配置文件 → load → 字段正确;缺必填字段报 ConfigError。

### Task 2: db.py
- 表按设计 3.2;`connect(path)` 开 WAL、`ensure_schema`。
- 函数:`insert_event(row) -> bool`(重复返回 False)、`upsert_chat`、`touch_thread`、`get/set_cursor`、`heartbeat_touch/get`、`session_get/session_rotate`、issue CRUD:`create_issue`、`add_issue_message`、`issue_messages`、`update_issue`、`open_issues`、`replies_for_issue`、`insert_reply`、`alert_recent`。
- 测试:幂等插入;状态更新;session 轮换保留 prev。

### Task 3: render.py + evidence.py(迁移)
- 从 fg.py 搬 `_disp_width/_render_table/_split_tables/_line_elems/post_content(text, members)`;members 从 db `members` 表(chat 成员映射,由 events 的 mentions 与 sender 累积)传入。
- evidence: `check(kinds:list[str], evidence:str, text:str) -> None` 抛 `EvidenceError(msg)`;`CHECKLIST` 常量。
- 测试:加粗拆分;表格进 code_block;@ou_ 转 at;托词/过短/类型不匹配/已核实短证据全拒;正常通过。

### Task 4: events.py
- `EventRow` dataclass:message_id、chat_id、chat_type、thread_id、root_id、parent_id、sender_id、sender_type、create_time、msg_type、mentions(list[{key,id,name}])、mentions_me、text、image_keys、raw。
- `from_ws_event(data, bot_open_id)`、`from_api_item(item, bot_open_id)`(补拉路径)、`render_text(msg_type, content, mentions)`。
- 测试:text 消息 @ 还原;post 拆图;mentions_me 按 open_id 判。

### Task 5: cards.py
- `admit(ev, cfg, participated_threads:set) -> bool`。
- `merge_key(ev) -> tuple`;`assign_cards(db, cfg, now_ms)`:遍历未归卡 events(按 create_time),准入不过 → 标 ignored 卡?(设计:`open → ignored`;实现为 event.card_state='ignored',不建卡);通过 → 找同 key 且 open/answering/waiting 的卡,无 thread 的还要 ≤10min 距该卡最后一条;否则新建。
- `pick_card(db, cfg, now_ms)`:最早 open、defer_until ≤ now、发起人最后消息距今 ≥ quiet_seconds。
- `finish_card(db, issue, cfg)`:answered/waiting → 解锁;否则 redispatch+1,≥max → stuck。`release_expired_locks`、`wake_deferred`。
- 测试:同人 3 条 → 1 卡;话题追问同卡;非白名单私聊 ignored;静默窗未到不派;重派超限 stuck。

### Task 6: feishu.py
- `FeishuClient(app_id, secret)`;`_req` 带 429/99991400 退避 3 次;`bot_open_id()`、`send_post(chat_id, content)`、`reply_post(mid, content, in_thread)`、`react/unreact`、`list_messages(container_type, id, page_size, sort, start, end)`、`get_message(mid)`、`download(mid, key, dest)`、`chat_members`。
- 测试:用 monkeypatch 假 requests,验证退避与 payload 形状。

### Task 7: claude_runner.py
- `run_claude(cfg, workspace, session_id, prompt, *, resume=True, log) -> RunResult(rc, result_json, idle_killed, resume_failed, usage)`;`start_new_session=True`,`os.killpg`;剥 `CLAUDECODE`、`CLAUDE_CODE_ENTRYPOINT`、`CLAUDE_CODE_SSE_PORT`;设 `YIZHAN_INST`、`TZ`;`--output-format stream-json --verbose`;stdout 按行读,空闲 idle_timeout 杀。
- 探活 `probe(cfg) -> bool`(`claude -p "回 ok"` 30s)。
- 测试:用假 claude 脚本(tests/fake_claude.sh)测正常 result 解析、空闲超时杀、resume 失败识别(stderr 含 "No conversation found")。

### Task 8: alerts.py
- `alert(db, client, cfg, kind, text)`:alerts 表查同 kind 1h 内有则跳过。

### Task 9: fgctl.py
命令:`card <id>`、`context <chat> [--since N]`、`reply <mid> <text> --type --evidence [--thread] [--no-thread]`(默认挂话题)、`replycode <mid> <lead> <file>`、`send <chat> <text>`、`sendcode <chat> <lead> <file>`、`done <id> [note]`、`defer <id> --until <min|HH:MM> [--note]`、`pending`、`pull <chat> --start --end`、`checklist`、`react/unreact`。
- reply 成功写 replies 表(issue 由 mid 反查)、撤 Typing 表情。
- `done` 前置检查:卡下 messages 中 create_time > 本轮开始(issue.locked_at)且未被 card 命令读过 → 拒绝并打印。简化:issue 表加 `last_read_at`,`card` 命令更新;done 时若存在 create_time > last_read_at 的消息则拒。
- 测试:done 拒绝新消息;defer 状态;证据门透传。

### Task 10: listener.py
- 启动:token、bot_open_id 存内存;补拉 chats/threads cursor 差量;ws 客户端注册 `p2_im_message_receive_v1` 与 `p2_im_chat_member_bot_added_v1`;每事件 heartbeat;@我或在我参与的话题(threads 表 participated=1)→ `systemctl --user start yizhan-dispatcher@<inst>`。
- 处理函数 `handle_message(db, cfg, ev, bot_open_id, trigger)` 独立可测。
- 测试:自身消息丢弃、重复忽略、@我触发 trigger、进群写 chats + 告警。

### Task 11: dispatcher.py
- flock `state_dir/dispatcher.lock`;probe;loop(assign、release locks、wake deferred、pick、run、finish);resume 失败 → rotate(new uuid,首轮 prompt 前置读记忆与 open 卡);watchdog;写 logs/dispatcher.log。
- `paused` 文件存在则退出。
- 测试:用假 claude 脚本走整轮;stuck;rotate。

### Task 12: cli.py + 模板 + systemd
- `yizhan init <inst> --project <dir> [--skill name]`:建 workspace 骨架、写 config 模板(0600)、打印后续步骤。`rotate`、`pause/resume`(touch/rm paused)、`status`、`migrate <inst> --from <drama-auto skill dir>`(导入 members.json / queue.json → issues 可选,最少导入 sent_log 到 replies 不做;做 members)。
- 模板 CLAUDE.md、SKILL.md(从现有 SKILL.md 改工具名、加第 5 步写记忆、一卡多问逐个回、不向控制台提问)、settings.json、project.toml。
- systemd 三个单元文件 + README 部署段。

### Task 13: 自检与文档
- `pytest -q` 全绿;`python -m gateway.dispatcher --help`;README 更新;commit。
