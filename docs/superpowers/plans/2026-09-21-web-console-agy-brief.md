# 派单:驿站 Web 控制台的模板与前端

后端已完成并通过 155 个测试。你只做**视图层**:Jinja 模板 + `app.css` + `app.js`。
不要改 `gateway/web/*.py`,不要改接口,不要新增依赖。

## 0. 你能写的文件

```
/data/projects/yizhan/gateway/web/templates/    base.html index.html instance.html new.html login.html partials/*.html
/data/projects/yizhan/gateway/web/static/       app.css app.js         ← 只有这两个是你的
```

`static/` 下这些是既有资产,**只读不改**:`tokens.css`、`base.css`(设计体系)、`htmx.min.js`、
`htmx-ext-sse.js`、`uplot.min.js`、`uplot.min.css`。

## 1. 视觉:必须套统一设计体系

设计体系在 `/home/hongshu/.claude/skills/ui-unify/`:

| 文件 | 用途 |
|---|---|
| `assets/tokens.css` | CSS 变量(已复制到本项目 static/)。**所有颜色只能来自这里的变量** |
| `assets/base.css` | 现成组件 class:`ui-btn` `ui-card` `ui-table` `ui-badge` `ui-input` 等,优先用 |
| `assets/demo.html` | 完整应用外壳示例:侧栏、顶栏、统计卡、表格、表单、空状态、toast。**照着它的结构写** |
| `references/style-guide.md` | 视觉规范:色板、排版、间距、圆角、反例 |

硬性约束:

- **禁止任何硬编码颜色**。不许出现 `#fff`、`rgb(...)`、`red` 这类字面量,一律 `var(--…)`。
- 深色为主、浅色自动跟随。`tokens.css` 已处理 `data-theme`,页面要提供一个切换按钮(存 localStorage)。
- 不引入任何新的 CSS 框架或 JS 库。交互用 htmx,图表用 uPlot,两者都已在本地。
- 中文界面。字号、间距、圆角按 style-guide,不要自己发明。

页面里这样引:

```html
<link rel="stylesheet" href="/static/tokens.css">
<link rel="stylesheet" href="/static/base.css">
<link rel="stylesheet" href="/static/uplot.min.css">
<link rel="stylesheet" href="/static/app.css">
<script src="/static/htmx.min.js"></script>
<script src="/static/htmx-ext-sse.js"></script>
<script src="/static/uplot.min.js"></script>
<script src="/static/app.js" defer></script>
```

## 2. 这是什么系统(写文案要用得上)

驿站是飞书到 Claude Code 的网关。每个「实例」绑一个飞书应用和一个代码项目,由两个后台服务组成:
`listener`(常驻,收飞书消息写库)和 `dispatcher`(定时醒来,把消息归成「问题卡」,唤起 claude 处理并回群)。
控制台就是管这些实例、看它们健不健康。

术语:**实例** = 一个项目的机器人;**卡** = 一批合并到一起的用户消息,机器人一次处理一张;
**会话** = claude 的常驻上下文,有固定 session id,每轮 `--resume` 续上。

## 3. 页面

### 3.1 登录 `GET /login` → `login.html`

一个口令输入框。`POST /login`,字段名 `password`。模板变量:`error`(字符串或 None,有就红字显示)。
不需要 CSRF。居中的小卡片即可,写明这是「驿站控制台」。

### 3.2 首页 `GET /` → `index.html`

**卡片墙**:每个实例一张卡。模板变量 `instances`(数组)与 `csrf`。
数据结构见第 4 节 `/api/instances` 的真实输出。

每张卡上要有:

| 元素 | 数据来源 | 说明 |
|---|---|---|
| 健康点 + 实例名 | `health`(green/yellow/red/grey)、`inst` | 点用 token 里的语义色;grey = 还没启用 |
| 健康原因 | `health_reason` | 绿色时是「正常」,其余是具体原因,直接显示 |
| 心跳 | `heartbeat_ago_s` | 秒数转成「3 秒前 / 2 分钟前」 |
| 今日消息 / 卡 / 回复 | `today_msgs` `today_cards` `today_replies` | |
| 今日成本 | `today_cost_usd` | 美元,保留四位;为 0 时显示 `$0` |
| 待处理 | `pending` | 大于 0 时高亮 |
| 上次上下文 | `context_tokens` | 转成 `64k` 这种写法 |
| 最近告警 | `last_alert` | 可能为 null |
| 服务状态 | `units.listener` `units.timer` | active / inactive,小徽章 |

按钮:`paused` 为 true 显示「恢复」,否则「暂停」(调 action 接口,见 4.6);「详情 →」跳 `/i/<inst>`。
右上角「＋ 新建实例」跳 `/new`。整页每 30 秒用 htmx 刷新卡片区(`hx-get="/api/instances"` 配合
`hx-trigger="every 30s"`,或者用 JS 拉 JSON 重渲染,你定,但要能局部刷新不整页跳)。

空状态:一个实例都没有时,给一句说明 + 「新建实例」按钮。

### 3.3 详情 `GET /i/{inst}?tab=overview` → `instance.html`

模板变量:`inst`、`tab`、`summary`(同卡片墙单条)、`fields`(配置字段元信息)、`streams`
(`('listener','dispatcher','claude')`)、`csrf`。

顶部:实例名、健康点与原因、项目目录、五个操作按钮(暂停/恢复、重启监听、轮换会话、立即派一轮)。
下面五个页签,切换用 htmx 局部加载(`/i/{inst}?tab=cards` 返回整页也行,但首选前端切换 + 拉 JSON)。

**① 概览**:四个统计块(心跳、今日消息/卡、待处理、上次上下文);今日按小时的消息与卡**折线图**
(uPlot,数据 `overview.hourly`,x 轴是小时字符串);最近告警列表(`overview.alerts`,字段 `kind`、`sent_at`);
卡状态分布(`overview.card_states`,形如 `{"answered": 11, "open": 2}`);服务状态与 `timer_next`。

**② 卡**:表格,列为 编号、状态、发起人、消息数、建于、首回耗时、重派。
状态用徽章:`answered` 绿、`open` 蓝、`answering` 黄(带动画)、`waiting` 灰、`stuck` 红。
顶部按状态筛选 + 分页(`?state=&page=`)。点一行展开(或右侧抽屉)拉 `/api/i/{inst}/cards/{id}`:
显示每条消息原文(`messages[].text`、`sender_name`、时间,`images` 是图片 URL 数组直接 `<img>`),
和机器人每条回复(`replies[].summary` 正文、`type` 类型徽章、`evidence` 证据、`sent_at`)。
证据这一项要显眼——它是这套系统的核心纪律。`stuck` 的卡多一个「重开」按钮。

**③ 会话**:当前 session id(可复制)、建于、`fresh`、累计成本 `total_cost_usd`;
`rounds` 数组画**柱状图**(x 为轮次,y 为 `context_tokens`,鼠标悬停显示 `cost_usd`、`output_tokens`、
`outcome`、`issue_id`,点柱子跳到那张卡);`rotations` 是轮换历史表(`session_id`、`created_at`、
`reason`、`ended_at`)。`rounds` 为空时给空状态,别画空图。

**④ 日志**:三个流切换(listener / dispatcher / claude)。用 htmx SSE 扩展连
`/api/i/{inst}/logs/{stream}`,事件名 `log`,`data` 是 JSON `{at, level, text}`。
等宽字体,按 `level` 上色(error 红、warn 黄、notice 强调色、info 默认、debug 弱化)。
要有:暂停滚动的开关、关键字过滤框(改 `?q=` 重连)、清屏。保留最近 500 行,超了丢头部。

**⑤ 配置**:表单。字段元信息来自 `fields`(见 4.5),每项有 `name`、`type`
(str/int/bool/list/table)、`desc`(一句说明,**直接显示在字段旁**)、`secret`、`restart`。
- `str`/`int` → 输入框;`bool` → 开关;`list` → 可增删的标签列表;`table` → 键值对增删
- `secret` 字段显示打码值(`••••IUZ0`),旁边「显示/修改」按钮;**不提交或提交打码值 = 保留原密钥**
- 提交 `POST /api/i/{inst}/config`,失败返回 `{ok:false, errors:{字段:原因}}`,把红字显示到对应字段
- 成功返回 `needs_restart`(如 `["listener"]`),弹 toast 提示并给一个「立刻重启」按钮

### 3.4 新建向导 `GET /new` → `new.html`

三步,同一页分步显示,不要跳页。模板变量 `fields`、`csrf`。

1. 实例名 + 项目目录 → `POST /api/new/init`。成功后进第 2 步,失败显示 `message`。
2. 凭证与白名单表单(app_id、app_secret、alert_open_id、p2p_allow、group_allow、claude_env)
   → `POST /api/new/verify`(带 `inst`)。返回三项检查结果 `token`、`bot`、`probe`,每项
   `{ok, message}`,逐条显示 ✅/❌ 与说明。`bot` 失败时 `message` 里有飞书后台要做的四步,原样显示。
   三项全绿才允许下一步。
3. 「启用」→ `POST /api/new/enable`,成功跳 `/i/<inst>`。

向导里要有引导文案,比如怎么拿自己的 open_id:把机器人拉进群 @ 一句,日志里就能看到。

## 4. 接口(真实输出,直接照着写)

所有接口需登录 cookie(浏览器自动带)。**写操作必须带 CSRF**:请求头 `X-CSRF: {{ csrf }}`,
或表单里 `<input type="hidden" name="csrf" value="{{ csrf }}">`。缺了返回 403。

### 4.1 `GET /api/instances`

```json
[{"inst":"drama-auto","project_dir":"/data/projects/drama-auto","paused":false,"db_state":"ok",
  "units":{"listener":"active","timer":"active",
           "dispatcher_last":{"rc":0,"at":"Mon 2026-09-21 15:20:58 CST","result":"success"}},
  "today_msgs":30,"today_cards":13,"today_replies":30,"today_cost_usd":0,"pending":2,
  "context_tokens":0,"last_alert":{"kind":"new_chat:oc_da5f…","at":1789971237221},
  "stuck":0,"oldest_open_min":null,"heartbeat_ago_s":571.9,
  "health":"green","health_reason":"正常"}]
```

`db_state` 为 `missing` 时 health 是 grey,统计项全 0;为 `broken` 时 health 是 red。

### 4.2 `GET /api/i/{inst}/overview`

```json
{"inst":"drama-auto",
 "hourly":[{"hour":"00","msgs":0,"cards":0}, …, {"hour":"13","msgs":5,"cards":4}],
 "alerts":[{"kind":"new_chat:oc_da5f…","sent_at":1789971237221}],
 "card_states":{"answered":11,"open":2},
 "summary":{ …同 4.1 的单条… },
 "timer_next":"15:31:58","quiet_seconds":30,"skill":"drama-auto-feedback-group-reply"}
```

`hourly` 只到当前小时为止,不是固定 24 条。

### 4.3 `GET /api/i/{inst}/cards?state=&page=1&size=20`

```json
{"total":13,"page":1,"size":3,
 "items":[{"id":13,"state":"answered","sender":"ou_feab…","sender_name":"ou_feab…",
           "n_msgs":1,"created_at":1789974724562,"chat_id":"oc_da5f…","thread_id":null,
           "redispatch":0,"note":null,"first_reply_s":56}]}
```

`sender_name` 认不出名字时就是 open_id 本身,显示时截断。`first_reply_s` 可能是 null(还没回)。

### 4.4 `GET /api/i/{inst}/cards/{id}`

```json
{"card":{"issue_id":13,"state":"answered","chat_id":"oc_…","thread_id":null,"sender_id":"ou_…",
         "created_at":1789974724562,"redispatch":0,"note":null,"defer_until":null},
 "messages":[{"mid":"om_x1…","at":1789974724562,"text":"消息原文","sender_name":"ou_…",
              "images":["/api/i/drama-auto/attach/img_v3_…"]}],
 "replies":[{"reply_id":1,"issue_id":13,"to_message_id":"om_…","my_message_id":"om_…",
             "via":"reply","type":"other","evidence":"证据出处原文","summary":"回复正文","sent_at":…}],
 "rounds":[{"round_id":1,"issue_id":13,"session_id":"a6f2…","started_at":…,"ended_at":…,
            "rc":0,"context_tokens":54285,"output_tokens":700,"cost_usd":0.63,"outcome":"answered"}]}
```

### 4.5 `GET /api/i/{inst}/config`

```json
{"values":{"app_id":"cli_aa3a…","app_secret":"••••IUZ0","project_dir":"/data/projects/drama-auto",
           "skill":"drama-auto-feedback-group-reply","alert_open_id":"ou_feab…",
           "p2p_allow":["ou_feab…"],"group_allow":["oc_da5f…"],"quiet_seconds":30,
           "reply_in_thread":false,"claude_env":{"https_proxy":"http://127.0.0.1:7890"}, …},
 "fields":[{"name":"group_allow","type":"list",
            "desc":"允许响应的群 chat_id。进了新群会告警提醒,确认后加进来",
            "secret":false,"restart":"listener"}, …]}
```

`fields` 是顺序化的字段清单,**表单按这个顺序渲染**,不要自己排。

### 4.6 写操作

| 接口 | 提交 | 返回 |
|---|---|---|
| `POST /api/i/{inst}/action/{name}` | name ∈ `pause` `resume` `restart-listener` `rotate` `dispatch-now` `reopen-card`(表单带 `card_id`) | `{ok, message}`,失败 HTTP 400 |
| `POST /api/i/{inst}/config` | 表单,只提交改过的字段也可以 | `{ok, errors:{字段:原因}, needs_restart:["listener"], changed:[…]}` |
| `POST /api/new/init` | `inst`、`project_dir`、可选 `skill` | `{ok, message, skill}` |
| `POST /api/new/verify` | `inst` + 配置字段 | `{ok, token:{ok,message}, bot:{ok,open_id,message}, probe:{ok,message}, saved:{…}}` |
| `POST /api/new/enable` | `inst` | `{ok, message}` |
| `POST /logout` | — | 302 到 /login |

`message` 是给人看的中文,直接 toast 出来。

### 4.7 日志 SSE

`GET /api/i/{inst}/logs/{stream}?since=<毫秒>&q=<关键字>`,`stream` ∈ listener / dispatcher / claude。
事件名 `log`,`data` 为 `{"at":1789974724562,"level":"info","text":"…"}`。
level ∈ emerg/alert/crit/error/warn/notice/info/debug。断线重连时带上最后一条的 `at` 作为 `since`。

## 5. 交付

- 所有模板 + `app.css` + `app.js`,能直接跑起来
- 一份 `templates/README.md`:每个模板用了哪些接口、哪些 htmx 属性、主题切换怎么工作
- 自检:页面里 `grep -nE '#[0-9a-fA-F]{3,6}|rgb\(' ` 应当只在 `tokens.css` 命中
- 不要动 Python、不要改接口、不要加依赖、不要外网 CDN

怎么本地验证:服务已经在 `http://127.0.0.1:8760` 跑着(口令在 `config/web.toml`)。
改完模板刷新页面即可,不用重启服务。
