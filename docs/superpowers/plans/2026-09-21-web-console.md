# 驿站 Web 控制台实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 `docs/superpowers/specs/2026-09-21-web-console-design.md` 落地 `gateway/web/` 包与 `yizhan-web.service`:卡片墙首页、五页签详情、新建向导、登录;后端接口先于页面,页面由 agy 产出。

**Architecture:** FastAPI 应用,`data.py` 只读聚合各实例 `state.db`,`control.py` 包 `cli.py` 做写操作,`logs.py` 把 journalctl / 文件流转成 SSE,`auth.py` 单口令 cookie。dispatcher 收尾多落 `rounds` 表,`db.session_rotate` 多落 `session_history`。模板与静态资源由 agy 按 ui-unify 体系产出,Claude 收尾验收。

**Tech Stack:** FastAPI、uvicorn、Jinja2、sse-starlette、itsdangerous、htmx(+sse 扩展)、uPlot;pytest + httpx TestClient。

---

## 文件结构

```
gateway/db.py                    +rounds / session_history 表与函数
gateway/dispatcher.py            process_card 收尾插 rounds
gateway/web/__init__.py
gateway/web/settings.py          WebSettings(host/port/password/session_hours) ← config/web.toml
gateway/web/data.py              只读聚合:instances()、overview()、cards()、card()、session()
gateway/web/control.py           动作、配置读写、向导三步;包 cli/config/feishu/claude_runner
gateway/web/logs.py              journalctl / 文件 → 事件行;SSE 生成器
gateway/web/auth.py              口令、cookie、失败锁定、CSRF
gateway/web/app.py               FastAPI 装配、路由、中间件;`python -m gateway.web` 入口
gateway/web/__main__.py
gateway/web/templates/           agy 产出(base.html, index.html, instance.html, new.html, login.html, partials/*)
gateway/web/static/              tokens.css base.css(复制) htmx.min.js htmx-ext-sse.js uplot.min.js uplot.min.css app.css app.js
systemd/yizhan-web.service
config/web.example.toml
tests/test_web_data.py test_web_control.py test_web_logs.py test_web_auth.py test_web_app.py test_rounds.py
```

## Task 1: rounds 与 session_history 表

**Files:** Modify `gateway/db.py`、`gateway/dispatcher.py`、`tests/fakes/fake_claude.py`;Test `tests/test_rounds.py`

- [ ] SCHEMA 追加:
```sql
CREATE TABLE IF NOT EXISTS rounds (
  round_id INTEGER PRIMARY KEY AUTOINCREMENT, issue_id INTEGER, session_id TEXT,
  started_at INTEGER, ended_at INTEGER, rc INTEGER, context_tokens INTEGER, output_tokens INTEGER,
  cost_usd REAL, outcome TEXT);
CREATE INDEX IF NOT EXISTS ix_rounds_issue ON rounds(issue_id);
CREATE TABLE IF NOT EXISTS session_history (
  session_id TEXT PRIMARY KEY, created_at INTEGER, reason TEXT, prev_id TEXT, ended_at INTEGER);
```
- [ ] `db.insert_round(conn, issue_id, session_id, started_at, ended_at, rc, context_tokens, output_tokens, cost_usd, outcome)`;`db.rounds(conn, limit=200)`;`db.session_history(conn)`
- [ ] `db.session_rotate` 里:新 id 插 `session_history`;旧 id 行 `ended_at=now`
- [ ] `dispatcher.process_card`:记 `started`,收尾后 `insert_round(...)`,`output_tokens` 取 `res.usage.get('output_tokens')`,`cost_usd` 取 `res.result.get('total_cost_usd')`,outcome 为 finish_card 返回值或 'rotated'
- [ ] `fake_claude.py` 的 result 行加 `"total_cost_usd": 0.01`
- [ ] 测试:跑一轮后 `rounds` 一行、字段正确;`session_rotate` 两次后 `session_history` 两行且首行 `ended_at` 非空
- [ ] `pytest -q` 全绿;commit `feat: rounds 与 session_history 表`

## Task 2: web 依赖与 settings

**Files:** Modify `pyproject.toml`;Create `gateway/web/__init__.py`、`gateway/web/settings.py`、`config/web.example.toml`;Test `tests/test_web_auth.py`(settings 部分)

- [ ] `pyproject.toml` 加 optional group `web = ["fastapi>=0.115","uvicorn[standard]>=0.30","jinja2>=3.1","python-multipart>=0.0.9","sse-starlette>=2","itsdangerous>=2"]`,dev 加 `httpx`;`uv pip install -e '.[dev,web]'`
- [ ] `settings.py`:
```python
@dataclass
class WebSettings:
    host: str = '0.0.0.0'; port: int = 8760; password: str = ''; session_hours: int = 72
    secret_key: str = ''   # 空则首次启动生成并写回
def load(path: Path | None = None) -> WebSettings  # 默认 Paths.config_dir/'web.toml';缺 password 抛 WebConfigError
```
- [ ] `config/web.example.toml` 四个字段带注释;`.gitignore` 已覆盖 `config/*.toml`,补 `!config/web.example.toml`
- [ ] 测试:tmp toml → 字段;缺 password 报错;secret_key 为空时 load 生成 32 字节并写回文件
- [ ] commit `feat(web): settings 与依赖`

## Task 3: auth.py

**Files:** Create `gateway/web/auth.py`;Test `tests/test_web_auth.py`

- [ ] 接口:
```python
class Auth:
    def __init__(self, settings: WebSettings, clock=time.time)
    def check_password(self, ip: str, pw: str) -> tuple[bool, str]   # (ok, msg);错 5 次锁 10 分钟 → (False,'locked')
    def issue_cookie(self) -> str                                    # itsdangerous TimestampSigner 签 'ok'
    def verify_cookie(self, value: str | None) -> bool               # 过期 session_hours 小时
    def csrf_token(self, cookie_value: str) -> str                   # HMAC(secret, cookie) 前 32 位
    def verify_csrf(self, cookie_value: str | None, token: str | None) -> bool
```
- [ ] 测试:对/错口令;5 次后锁定且正确口令也拒;10 分钟后解锁(clock 打桩);cookie 过期;csrf 对错
- [ ] commit `feat(web): 口令与 cookie`

## Task 4: data.py 只读聚合

**Files:** Create `gateway/web/data.py`;Test `tests/test_web_data.py`

- [ ] 工具:`list_instances() -> list[str]`(扫 `Paths.config_dir/*.toml` 排除 example/web);`open_ro(db_file) -> sqlite3.Connection | None`(`file:...?mode=ro`,不存在返 None,损坏抛 `DbBroken`)
- [ ] `unit_state(unit) -> str`(systemctl is-active,失败返 'unknown');`dispatcher_last(inst) -> {rc, at}`(systemctl show -p Result,ExecMainStatus,ExecMainExitTimestamp);`timer_next(inst) -> str | None`(systemctl show -p NextElapseUSecRealtime)。三者都经 `run_systemctl(args)` 单点调用,便于打桩
- [ ] `health(inst_row) -> (color, reason)` 纯函数,输入 dict:listener_active、heartbeat_ago_s、stuck、dispatcher_rc、oldest_open_min、paused、db_state。规则见 spec 3.1
- [ ] `instance_summary(inst) -> dict`(spec 4 的 `/api/instances` 一项);`instances()` 汇总,单实例异常不影响其它
- [ ] `overview(inst)`:数字块 + `hourly`(今日 0..now 每小时 msgs/cards,按本地时区)+ alerts 10 条 + units + timer_next
- [ ] `cards(inst, state=None, page=1, size=20)`;`first_reply_s` = 最早 reply.sent_at − issue.created_at;`card(inst, id)` 含 messages(附件路径转 `/api/i/{inst}/attach/{key}`)与 replies
- [ ] `session(inst)`:session 表 + rounds(倒序 200)+ session_history + `total_cost_usd`
- [ ] 测试(假库,复用 conftest 的 cfg/db,把 `run_systemctl` 打桩):健康四色各一例;今日统计与 hourly;first_reply_s;库不存在 → db_state='missing';损坏文件 → 'broken';多实例一坏不影响另一个
- [ ] commit `feat(web): 只读聚合`

## Task 5: control.py 写操作

**Files:** Create `gateway/web/control.py`;Test `tests/test_web_control.py`

- [ ] `action(inst, name, **kw) -> dict{ok, message}`:
  - pause/resume → `cli.cmd_pause/cmd_resume` 同逻辑(抽成 `cli.pause(inst)`/`cli.resume(inst)` 纯函数,cmd_* 调它们)
  - restart-listener → `systemctl --user restart yizhan-listener@inst`
  - rotate → `db.session_rotate(conn,'manual(web)')`
  - dispatch-now → `systemctl --user --no-block start yizhan-dispatcher@inst.service`
  - reopen-card(card_id) → 卡须为 stuck;`update_issue(state='open', redispatch=0)`
  - 未知 name → ok=False;subprocess 超时 20 秒 → ok=False 带 stderr 前 300 字
- [ ] 配置:`FIELDS` 元信息表(名、类型、说明、是否密钥、是否需重启);`config_read(inst) -> dict`(密钥 `'••••'+末4位`);`config_write(inst, form: dict) -> {ok, errors, needs_restart}`:逐字段转类型校验 → 密钥打码值则保留原值 → 用 `tomllib` 反向不可用,故用 `_dump_toml(dict)` 手写序列化(字符串/整数/布尔/列表/表 `[claude_env]`)→ 写 `.tmp` → `config.load` 校验 → `os.replace`;进程内 `threading.Lock`
- [ ] 向导:`wizard_init(inst, project_dir, skill=None)`(调 `cli.cmd_init` 的 argparse Namespace);`wizard_verify(inst, form)`:先 `config_write`,再 `{token: ok/err, bot_open_id: str|'' , probe: ok/msg}`;`wizard_enable(inst)`:`systemctl --user enable --now` 两单元
- [ ] 测试:`subprocess.run` 打桩断言各 action 参数;reopen 非 stuck 拒;config_write 类型错 → errors 且文件不变;密钥打码保留;写后 `config.load` 能读;`_dump_toml` 往返;向导 init 建目录、verify 用假 FeishuClient
- [ ] commit `feat(web): 动作、配置读写、向导`

## Task 6: logs.py

**Files:** Create `gateway/web/logs.py`;Test `tests/test_web_logs.py`

- [ ] `parse_journal_line(json_line) -> {at(ms), level, text}`(`__REALTIME_TIMESTAMP` 微秒→毫秒,PRIORITY→level)
- [ ] `parse_stream_line(json_line) -> {at, level, text}`:claude-stream 只留 assistant 的 text/tool_use 摘要与 result 行
- [ ] `tail_journal(unit, since_ms, q)`、`tail_file(path, since_ms, q)` 生成器,`-f` 子进程按行 yield,`q` 子串过滤;`stream_events(inst, stream, since_ms, q)` 按 stream 选择,journalctl 不可用时 dispatcher 退回 `logs/dispatcher.log`
- [ ] 测试:两个 parse 函数;`tail_file` 用临时文件追加行验证 since 与 q;`tail_journal` 把 `subprocess.Popen` 打桩
- [ ] commit `feat(web): 日志流`

## Task 7: app.py 路由与 SSE

**Files:** Create `gateway/web/app.py`、`gateway/web/__main__.py`;Test `tests/test_web_app.py`

- [ ] `create_app(settings: WebSettings | None = None) -> FastAPI`;中间件:未登录且路径非 `/login`、`/static` → 302;POST 需 CSRF(`X-CSRF` 头或表单 `csrf`)否则 403
- [ ] 路由按 spec 4 全部实现;页面路由渲染 `templates/`(先放最简占位模板,agy 后替换);`/api/i/{inst}/logs/{stream}` 用 `sse_starlette.EventSourceResponse` 包 `logs.stream_events`;`/api/i/{inst}/attach/{key}` 送附件文件
- [ ] `__main__.py`:`uvicorn.run(create_app(), host, port)`
- [ ] 测试(TestClient,data/control 打桩):未登录 302;登录后 200;CSRF 缺 403;`/api/instances` 空/单/坏三种;action 未知 400;SSE 端点首事件格式
- [ ] commit `feat(web): 路由`

## Task 8: 静态资源、systemd、install-units

**Files:** Create `gateway/web/static/*`、`systemd/yizhan-web.service`;Modify `gateway/cli.py`(install-units 复制 web 单元、`web-init` 子命令生成 web.toml 与随机口令)

- [ ] 复制 `~/.claude/skills/ui-unify/assets/{tokens,base}.css`;下载 htmx 2.x、htmx-ext-sse、uPlot 到 static(pin 版本,记 `static/VERSIONS.md`)
- [ ] `yizhan-web.service`:`ExecStart={repo}/.venv/bin/python -m gateway.web`,`Restart=always`,`Environment=TZ=Asia/Shanghai`
- [ ] `yizhan web-init [--port]`:写 `config/web.toml`(随机 16 位口令,打印一次)
- [ ] 测试:web-init 生成文件 0600 且 load 通过
- [ ] commit `feat(web): 静态资源与单元`

## Task 9: 派单 agy 做模板与前端

**Files:** `gateway/web/templates/**`、`gateway/web/static/app.css`、`static/app.js`(agy 产出)

- [ ] 写派单文档 `docs/superpowers/plans/2026-09-21-web-console-agy-brief.md`:spec 第 3、4 节原文;接口示例 JSON(用 Task 4-7 的测试夹具真实输出);ui-unify 四个文件路径与约束;模板变量清单(每个页面路由传给模板的上下文键);htmx 用法约定(局部刷新目标 id、SSE 扩展);交付清单
- [ ] `mcp__antigravity__use_antigravity`:mode `accept-edits`,Pro 模型,`thinking_depth: high`,`add_dirs` = `gateway/web/templates`、`gateway/web/static`、`~/.claude/skills/ui-unify`;prompt 指向派单文档
- [ ] `antigravity_result` 取回;跑 `python -m gateway.web` 起服务,curl 每个页面 200;模板引用的接口/变量全部存在
- [ ] commit `feat(web): 模板与前端(agy)`

## Task 10: 验收与交付

- [ ] `pytest -q` 全绿
- [ ] `yizhan install-units && yizhan web-init && systemctl --user enable --now yizhan-web`
- [ ] 浏览器打开 `http://127.0.0.1:8760`:登录 → 首页 → drama-auto 详情五页签 → 配置页保存一次(改 quiet_seconds 再改回)→ 新建向导走到第 2 步验证后取消
- [ ] 深浅两主题各截首页、详情概览、卡、配置、向导、登录;对照 `~/.claude/skills/ui-unify/references/migration.md` 验收清单
- [ ] README 加「Web 控制台」段;commit `docs: Web 控制台使用说明`
