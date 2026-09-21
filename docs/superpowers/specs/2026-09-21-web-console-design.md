# 驿站 Web 控制台设计

日期:2026-09-21  状态:已审(用户 09-21 逐段确认)
起因:实例管理与监控目前全靠 `yizhan status`、`journalctl`、手改 toml。现在自己用,后续要分享给同事,需要一个浏览器页面。
访问地址:局域网 `http://127.0.0.1:8760`。

## 0. 核心决策

| 决策 | 结论 | 依据 |
|---|---|---|
| 与现有服务的关系 | 独立的第三个 systemd 单元 `yizhan-web.service`,读同一份 `state.db`、调 `systemctl --user`、写 `config/*.toml`;listener / dispatcher 一行不改 | 用户选 A:改动最小,不推翻已跑稳的东西 |
| 技术栈 | FastAPI + Jinja 模板 + htmx 局部刷新 + SSE;前端零构建,全 Python,装现有 `.venv` | 用户选 A:与现有结构一致,不引入 node;API 层留着,以后要换前端不用重写后端 |
| 布局 | 首页卡片墙(每实例一张卡),点进详情页(五个页签) | 用户在线框图里选 B |
| 配置编辑 | 表单,字段旁带说明,密钥打码 | 用户选 A:字段固定十来个,说明文字对分享给同事最有用 |
| 视觉 | 套 `ui-unify` 统一设计体系(Linear 暗色为主、浅色跟随、强调 Supabase 绿),颜色只能来自 token | 用户要求「看一下我们的 ui skills」 |
| 分工 | 后端、接口、测试、验收 = Claude;全部模板与前端 = agy(Pro 档,thinking high) | 用户全局规则:前端页面设计一律委托 agy |
| 访问控制 | 单口令 + HttpOnly cookie;不做用户体系 | 现在自己用,后续分享给同事;预留升级空间 |
| 监控范围 | 健康、活跃度、卡明细、会话、日志、成本,六项全要 | 用户 09-21 定 |

## 1. 需求

1. 一眼看到所有实例是否健康,不健康的原因是什么。
2. 每个实例:今天收了多少消息、开了多少卡、回了多少条,趋势按小时。
3. 每张卡能点开看消息原文、机器人每条回复及其证据、耗时、重派次数。
4. 当前会话 id、活了多久、上下文占用、每轮 token 用量、轮换历史。
5. listener / dispatcher / claude 三路日志实时滚动、可过滤。
6. 在页面上暂停、恢复、重启监听、轮换会话、立即派一轮、重开 stuck 卡。
7. 在页面上改配置,改错了保存不进去,改对了提示要重启什么。
8. 在页面上新建实例:填名字和目录 → 填凭证 → 当场验证 → 一键启用。
9. 有口令,密钥不在页面上明文出现。
10. 与其他后台工具视觉一致,深浅两主题都能看。

## 2. 架构

```
浏览器 ──HTTP/SSE──▶ yizhan-web.service  (FastAPI + Jinja + htmx, 0.0.0.0:8760)
                          │
        ┌─────────────────┼──────────────────┬───────────────────┐
        ▼                 ▼                  ▼                   ▼
  state.db (只读)   config/*.toml (读写)   systemctl --user    journalctl -o json
  每实例一个        写前 config.load 校验   复用 cli.py 的函数   listener / dispatcher
                                                                 + logs/claude-stream.jsonl
```

代码位置 `gateway/web/`:

| 文件 | 职责 |
|---|---|
| `app.py` | FastAPI 应用、路由、口令中间件、CSRF、SSE 端点 |
| `data.py` | 只读聚合:实例健康、今日统计、按小时序列、卡列表/详情、会话与 usage、告警 |
| `control.py` | 写操作:pause/resume/restart/rotate/dispatch-now/reopen-card、配置读写校验、新建向导三步;全部包 `cli.py` 与 `config.py` 现有函数,不重复实现 |
| `logs.py` | `journalctl -f -o json` 与 `claude-stream.jsonl` 的行流,SSE 生成器,关键字过滤 |
| `auth.py` | 口令校验、cookie 签发、失败计数与 IP 锁定 |
| `templates/` | Jinja 模板(agy 产出) |
| `static/` | `tokens.css`、`base.css`(自 ui-unify 复制)、`htmx.min.js`、`uplot.min.js`、页面自有 `app.css` / `app.js` |

配置 `config/web.toml`(0600):
```toml
host = "0.0.0.0"
port = 8760
password = "填这里"
session_hours = 72
```

活跃度、token 统计从 `events / issues / replies / session` 表现场聚合,不建统计表(每天几十条的量级)。

## 3. 页面

### 3.1 首页 · 卡片墙(`GET /`)

每实例一张卡:健康点、心跳距今、今日消息数 / 卡数、待处理卡数、上下文 token、最近一条告警、是否暂停。按钮:暂停/恢复、详情。右上「新建实例」。30 秒自动刷新(htmx 轮询 `/api/instances`)。

健康点判定:
- **红**:listener 单元不是 active;或心跳 > 10 分钟;或有 stuck 卡;或最近一次探活失败(读 `alerts` 表 `probe_failed` 1 小时内)
- **黄**:有卡 open > 30 分钟;或已暂停(`paused` 文件存在)
- **绿**:其余
- **灰**:`state.db` 不存在(已 init 未启用)或损坏

### 3.2 详情页(`GET /i/{inst}`),五个页签

| 页签 | 内容 | 操作 |
|---|---|---|
| 概览 | 四个数字块(心跳、今日消息/卡、待处理、上下文);今日按小时的消息数与卡数折线;最近 10 条告警;服务状态(listener 在否、timer 下次触发、暂停否) | 暂停 · 恢复 · 重启监听 · 轮换会话 · 立即派一轮 |
| 卡 | 表:编号、状态、发起人(名字或 open_id)、消息数、建于、首次回复耗时、重派次数;按状态筛选、分页 20。点行展开:消息原文(附件缩略)、我方每条回复的正文 + type + evidence + 时间 | stuck 卡「重开」(→ open,redispatch 清零) |
| 会话 | 当前 session id、建于、fresh、上下文 token;每轮 usage 柱状图(来自 `claude-stream.jsonl` 的 result 行);轮换历史带原因(解析 `dispatcher.log` 的「轮换」行) | 轮换 |
| 日志 | 三个流可切:listener / dispatcher / claude-stream;SSE 实时滚动 | 暂停滚动 · 关键字过滤 |
| 配置 | 表单:app_id、app_secret(打码,点「显示」)、project_dir、skill、alert_open_id、p2p_allow(可增删列表)、group_allow(同)、quiet_seconds、lock_minutes、max_redispatch、merge_gap_minutes、idle/hard timeout、permission_args(单选:skip / dontAsk)、rotate_at_tokens、reply_in_thread、claude_env(键值对)。每个字段旁一句说明,白名单字段附「怎么拿 open_id / chat_id」 | 保存 → 校验 → 写文件 → toast「listener 需重启才生效」+ 重启按钮 |

### 3.3 新建向导(`GET /new`)

1. **实例名 + 项目目录** → `POST /api/new/init`:后台调 `cli.cmd_init` 同一份逻辑,生成工作台与配置模板。目录不存在报错。
2. **凭证 + 白名单 + 代理** → `POST /api/new/verify`:调 `FeishuClient.token()` 与 `bot_open_id()`,再跑 `claude_runner.probe`(带 `claude_env`)。三项各自显示 ✅/❌;bot open_id 为空时列出飞书后台四步(加机器人能力、开权限、事件选长连接、发版本审核)。通过才能保存。
3. **启用** → `POST /api/new/enable`:`systemctl --user enable --now` 两个单元,成功跳详情页。

### 3.4 登录(`GET /login`)

一个口令框。错 5 次该 IP 锁 10 分钟。

## 4. 接口契约

页面路由返回 HTML,`/api/…` 返回 JSON。写操作全部 POST,带 CSRF token(表单隐藏字段或 `X-CSRF` 头)。未登录一律 302 到 `/login`。

| 接口 | 返回 / 作用 |
|---|---|
| `GET /` `GET /i/{inst}` `GET /new` `GET /login` | 页面 |
| `POST /login` `POST /logout` | 口令 → cookie |
| `GET /api/instances` | `[{inst, health, health_reason, heartbeat_ago_s, today_msgs, today_cards, today_replies, pending, context_tokens, last_alert:{kind,at,text}, paused, units:{listener, timer}}]` |
| `GET /api/i/{inst}/overview` | 数字块 + `hourly:[{hour, msgs, cards}]` + `alerts[]` + `units` + `timer_next` |
| `GET /api/i/{inst}/cards?state=&page=` | `{total, items:[{id, state, sender, sender_name, n_msgs, created_at, first_reply_s, redispatch, note}]}` |
| `GET /api/i/{inst}/cards/{id}` | `{card, messages:[{mid, at, sender_name, text, images[]}], replies:[{at, via, type, evidence, summary}]}` |
| `GET /api/i/{inst}/session` | `{session_id, created_at, fresh, context_tokens, rounds:[{at, context_tokens, output_tokens, card_id}], rotations:[{at, reason, prev}]}` |
| `GET /api/i/{inst}/logs/{stream}?q=&since=` (SSE) | stream ∈ listener / dispatcher / claude;每行一个 `data:` 事件 `{at, level, text}`;`since` 为断线续传的时间戳 |
| `GET /api/i/{inst}/config` | 字段值,`app_secret` 返回 `"••••" + 末 4 位` |
| `POST /api/i/{inst}/config` | 字段级校验 → 合成 toml → `config.load` 再校验 → 写 `.tmp` 后 rename。密钥字段若提交的是打码值则保留原值。返回 `{ok, errors:{field:msg}, needs_restart:["listener"]}` |
| `POST /api/i/{inst}/action/{name}` | name ∈ pause / resume / restart-listener / rotate / dispatch-now / reopen-card(body 带 card_id)。同步执行 ≤ 20 秒,返回 `{ok, message}` |
| `POST /api/new/init` `POST /api/new/verify` `POST /api/new/enable` | 向导三步,见 3.3 |

## 5. 错误处理

| 情况 | 处理 |
|---|---|
| 某实例 `state.db` 不存在 / 损坏 | 该卡灰色「未初始化」或红色「库损坏」,其他实例正常 |
| `systemctl` 失败或超时 | `ok=false`,`message` 带 stderr 前 300 字,页面红色 toast;不改任何状态 |
| 配置校验不过 | 字段级红字,文件不动 |
| 两个浏览器同时保存配置 | 进程内文件锁串行;dispatcher / listener 只读 toml,web 是唯一写入方 |
| SSE 断开 | htmx `sse` 扩展自动重连,带 `since` 续传 |
| 口令错 5 次 | 该 IP 锁 10 分钟,返回 429 |
| journalctl 不可用 | 日志页签显示「读不到 journal,看 `logs/dispatcher.log`」并退回读文件 |
| 窄屏 | 卡片墙单列,页签横向滚动;只读能用,不追求手机操作 |

## 6. 测试

- **后端 pytest**(复用现有 `conftest`):
  - `data.py`:假库灌事件/卡/回复,断言健康判定四色、今日统计、按小时序列、首回耗时
  - `control.py`:`subprocess.run` 打桩,断言每个 action 的 systemctl 参数;配置写入的校验、打码保留、原子 rename;`reopen-card` 状态变化
  - `auth.py`:口令对错、cookie 签发与过期、5 次锁定
  - `logs.py`:journalctl JSON 行 → 事件格式;关键字过滤;`since` 续传
  - 路由:未登录 302;CSRF 缺失 403;`/api/instances` 在无实例、单实例、库损坏三种情况下的返回
- **页面验收**:agy 交付后在真实例上起服务,深浅两主题逐页走 `ui-unify` 的验收清单;首页、详情五页签、向导、登录各截图给用户。

## 7. 与 agy 的交接

Claude 先完成第 4 节全部接口并测过,再向 agy 派单。派单包含:
- 本文第 3、4 节(页面清单与接口契约)
- `ui-unify` 的 `tokens.css`、`base.css`、`demo.html`、`style-guide.md` 路径,约束「颜色只能来自 token,组件优先用 base.css 的 class」
- `gateway/web/templates/` 与 `static/` 目录写权限(`add_dirs`)
- 模式 `accept-edits`,Pro 档,`thinking_depth: high`
- 交付物:所有 Jinja 模板、`app.css`、`app.js`,以及一份「每页用了哪些接口」的对照说明

## 8. 落地

- `systemd/yizhan-web.service`(simple,`Restart=always`),`yizhan install-units` 顺带装
- 依赖新增:`fastapi`、`uvicorn[standard]`、`jinja2`、`python-multipart`、`sse-starlette`、`itsdangerous`
- 前端库放 `static/` 本地文件:`htmx.min.js`、`htmx-ext-sse.js`、`uplot.min.js`(约 40KB),不走 CDN
- 启动:`systemctl --user enable --now yizhan-web`;访问 `http://127.0.0.1:8760`

## 9. 不做

多用户与角色;在页面里编辑 skill 文件;在页面里跟机器人对话;历史数据归档与图表之外的报表;手机端操作体验;HTTPS(局域网内,后续要对外再加反代)。
