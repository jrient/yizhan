# 派单四:统计页签 + 一个安全修复

只改 `gateway/web/static/app.js`、`app.css`,以及 `templates/instance.html`(加页签按钮)。
不改 `.py`、接口、`tokens.css` / `base.css` / `chart-tokens.css`,不加依赖。
服务 `http://127.0.0.1:8760`,口令 `-9dvzp7feBusyoSO`,改完刷新即可。

**保留不要动**:`css()` 探针解析函数、会话柱状图 y 轴 `[0, max*1.15]`、`stateBadge()`、
`.cards-table` 的不换行样式、概览图的整数刻度 `incrs`。

## 〇、安全修复(必须做,优先级最高)

卡摘要、消息原文、回复正文、证据、发起人名字、告警正文、卡备注、日志文本 —— **全部来自飞书群,是不可信输入**。
现在它们被直接拼进 `innerHTML`。群里任何人发一条 `<img src=x onerror=...>`,打开控制台的人就会执行它,
而控制台能暂停服务、改配置、看密钥尾号。

做法:在 `app.js` 顶部加

```js
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
```

并把**每一处**把接口返回的字符串插进 HTML 的地方都包上 `esc()`,包括 `title="…"` 属性里的。
至少这些字段:`preview` `text` `summary` `evidence` `sender_name` `name` `label` `note` `detail`
日志的 `text`、`session_id`、`reason`、`inst`、`project_dir`。
数字和我们自己写的常量不用包。图表的图例、提示框里出现的名字同样要包(或用 `textContent`)。

我会用一张内容为 `<img src=x onerror="window.__xss=1">` 的卡去验收,`window.__xss` 必须保持未定义。

## 一、新页签「统计」

详情页加第六个页签「统计」(放在「会话」之后)。数据来自 `GET /api/i/{inst}/stats?days=7`(或 `30`)。

### 布局

- **顶部一行筛选**:「近 7 天 / 近 30 天」分段按钮,只有这一处筛选,切换后整页所有图一起重拉。不要每张图各放筛选。
- **KPI 行**(统计块,不是图表):消息、卡、回复、答复率、首回耗时(中位 · p90)、用量(折算)、错误、证据门拦下。
  - 用量要带「API 折算」字样和小字「订阅账号不按此计费」。
  - 首回耗时格式化成「1分34秒」,显示成「1分34秒 · p90 3分3秒」。
  - 答复率为 null 时显示「—」。
- **下面五张图**,两列网格,窄屏单列。每张图是一张卡片:标题、图、右上角一个「表格」切换(见第三节)。

### 五张图

| # | 标题 | 形式 | 数据 | 颜色 |
|---|---|---|---|---|
| 1 | 每日工作量 | 三条折线 | `daily[].msgs / cards / replies` | `--series-1` 消息、`--series-2` 卡、`--series-3` 回复 |
| 2 | 每日用量(API 折算) | 柱状图 | `daily[].cost_usd` | 单色 `--accent` |
| 3 | 首回耗时 | 两条折线 | `daily[].first_reply_p50_s` / `p90_s`(可能为 null,断开不连) | 中位 `--accent` 实线,p90 `--text-3` 细线(强调式:中位是重点) |
| 4 | 错误与异常 | 按天堆叠柱 | `errors_by_kind[]`,每项有 `label` 和 `daily[]` | 按 `errors_by_kind` 在 **ERROR_ORDER 里的固定位置**取 `--series-N`(见下) |
| 5 | 回复都在答什么 | 横向条形 | `reply_types[]`(`label`, `n`),再加 `senders[]` 做第二个小横条「谁在提问」 | 单色 `--accent` |

图 4 的颜色必须**跟着错误种类走,不跟排名走**:筛选后某类消失,剩下的不能换色。用这张固定映射:

```js
const ERROR_SLOT = { probe_failed:1, resume_failed:2, idle_killed:3, hard_killed:4,
                     rc_nonzero:5, stuck:6, evidence_rejected:7, feishu_error:8 };
// color = css(`--series-${ERROR_SLOT[kind] || 8}`)
```

**不要用 `--danger` / `--warn` / `--ok` 当系列色**:它们是状态色,专门表示好坏,拿来区分系列会让人误读。

### 画法规范(来自 dataviz skill,照做)

- 颜色一律 `css('--series-N')` / `css('--accent')` 从 token 取。`chart-tokens.css` 已挂在页面上,
  值已对本站卡片底色做过色盲与对比度校验。
- **一个图只有一个纵轴**。绝不做双纵轴。
- 线宽 2px;数据点 ≥ 8px(只在 hover 或数据点很少时显示);柱子顶端 4px 圆角,**基线是 0**;
  堆叠柱各段之间留 2px 底色缝隙(用 `--surface` 当描边)。
- 计数类纵轴只要**整数刻度**(用 `incrs: [1,2,5,10,20,50,100,200,500,1000]` 并过滤非整数)。金额纵轴显示 `$1.50`。
- 网格线用 `--border-subtle`,坐标轴文字用 `--text-2`。**不要虚线**。
- 横轴是日期 `MM-DD`;30 天时标签隔几个显示一个,别挤在一起。
- **图例**:≥ 2 个系列时必须有图例,放图上方,用短线段(折线)或小方块(柱)做色标,文字用正文色不用系列色。
  ≤ 3 个系列的折线在最后一个点旁加直接标签(系列名)。
- **不要在每个点上标数字**。条形图(图 5)可以在条尾标数值,因为只有一个系列且条数少。
- **悬停**:折线图用竖直十字线吸附到最近的日期,提示框同时列出该日所有系列的数值(数值加粗在前、系列名在后,
  前面是一小段该系列颜色的短线);柱状图悬停在柱子上显示当天数值,被悬停的柱子稍微提亮。
- **空状态**:某张图在区间内全是 0(比如错误为 0),不要画空坐标轴,显示一句「近 7 天没有错误」这类话。

### 数据覆盖说明

接口返回 `coverage.cost_since` 和 `coverage.errors_since`(毫秒时间戳,可能为 null)。
用量图和错误图的标题下方用小字说明「用量从 09-21 16:36 起记录」「错误从 09-21 13:16 起记录」,
免得把「那天没记录」读成「那天是 0」。

## 二、第三节:每张图的「表格」视图

浅色模式下有三种系列色对比度不到 3:1,按规则必须提供数据表视图作补偿;
它同时也是只看数字、复制数字的人要的东西。

每张图卡片右上角一个小切换「图 / 表」,切到表格就把同一份数据渲染成 `.ui-table`(第一列日期或类别,后面每列一个系列),
数字右对齐。切换状态不用记。

## 三、自检(我会跑)

- `node --check app.js` 通过
- `pytest tests/test_web_static.py`:token 都存在、没有硬编码颜色、没有 `\${` / `` \` ``
- 浏览器里六个页签来回切无 JS 报错,深浅主题切换后图表颜色跟着变
- XSS 验收卡不触发

## 接口返回示例(真实数据,近 7 天)

```json
{
 "range": {"days": 7, "start": "09-17", "end": "09-23"},
 "kpis": {"msgs": 33, "cards": 16, "replies": 40, "cost_usd": 19.38, "errors": 92,
          "evidence_rejected": 0, "first_reply_p50_s": 93.6, "first_reply_p90_s": 183.1, "answered_rate": 1.0},
 "daily": [{"day": "09-17", "msgs": 0, "cards": 0, "replies": 0, "cost_usd": 0.0, "errors": 0,
            "first_reply_p50_s": null, "first_reply_p90_s": null},
           …,
           {"day": "09-21", "msgs": 30, "cards": 13, "replies": 34, "cost_usd": 8.79, "errors": 12,
            "first_reply_p50_s": 89.3, "first_reply_p90_s": 190.9},
           {"day": "09-22", "msgs": 0, "cards": 0, "replies": 0, "cost_usd": 0.0, "errors": 80, …},
           {"day": "09-23", "msgs": 3, "cards": 3, "replies": 6, "cost_usd": 10.6, "errors": 0,
            "first_reply_p50_s": 147.8, "first_reply_p90_s": 153.2}],
 "errors_by_kind": [{"kind": "probe_failed", "label": "探活失败", "daily": [0,0,0,0,12,80,0], "total": 92}],
 "reply_types": [{"type": "config", "label": "配置", "n": 23}, {"type": "other", "label": "答疑", "n": 12},
                 {"type": "frame", "label": "画面", "n": 3}, {"type": "error", "label": "报错", "n": 2}],
 "senders": [{"name": "张三(张三)", "n": 15}, {"name": "小白(李四)", "n": 1}],
 "context": [{"at": 1790..., "issue_id": 7, "context_tokens": 341518, "cost_usd": 6.31, "duration_s": 132}, …],
 "coverage": {"cost_since": 1789981017000, "errors_since": 1789967803000}
}
```
