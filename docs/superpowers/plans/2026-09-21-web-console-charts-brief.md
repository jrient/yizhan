# 派单二:深色为默认 + 图表重做 + 一个数据绑定 bug

只改 `/data/projects/yizhan/gateway/web/static/app.js`(必要时 `app.css`)。
不要改任何 `.py`,不要改接口,不要改 `tokens.css` / `base.css`,不要加依赖。
改完刷新浏览器即可生效,服务在 `http://127.0.0.1:8760` 跑着(口令在 `config/web.toml`)。

## 一、深色为默认

现在没存过偏好时跟随系统。改成**默认深色**:

- 首次打开(localStorage 里没有 `theme`)→ 深色。
- 顶栏那组切换按钮仍然保留三个选项,但默认高亮的应当是「深色」而不是「跟随系统」。
- 用户显式选过就记住,刷新后沿用。

## 二、图表重做(这是重点)

### 为什么现在是坏的

uPlot 画在 **canvas** 上,`stroke: "var(--accent)"` 这种写法 canvas 根本不认,
所以线和柱子都没有颜色,只剩几个默认白点。这是当前两个图表都像坏掉的根本原因。

正确做法:运行时把 token 的真实色值读出来再传给 uPlot,并且在主题切换时重建图表。

```js
const css = (name, el = document.documentElement) =>
  getComputedStyle(el).getPropertyValue(name).trim();
// 例:css('--accent') → '#3ecf8e'
```

主题一换,这些值就变了,所以 `setTheme()` 之后要重新 draw 当前图表(记下当前 tab,重新渲染即可)。

### 两个图表

**① 概览页「今日趋势」**(`/api/i/{inst}/overview` 的 `hourly`,形如 `[{hour:"13", msgs:5, cards:4}]`)

- 两条序列:消息数、卡数。x 轴是小时,标签直接用 `13:00` 这种,不要小数。
- 强调色给「消息」,次要色给「卡」;两条线要能在深色底上分清,别都用同一个绿。
- 数据大多是 0,只有几个小时有值 —— 现在的图几乎是一条贴底的直线。用面积填充或加数据点,让少量非零值看得见。
- 鼠标悬停显示「14:00 · 消息 5 · 卡 4」,中文,不要 uPlot 默认的 `Time:` / `Value:`。

**② 会话页「每轮开销」**(`/api/i/{inst}/session` 的 `rounds`)

每条 round 形如:
```json
{"round_id":3,"issue_id":13,"session_id":"a6f2…","started_at":1789974724562,
 "ended_at":1789974765000,"rc":0,"context_tokens":54285,"output_tokens":700,
 "cost_usd":0.63,"outcome":"answered"}
```

现在的问题:x 轴出现 `1, 1.05, 1.1 … 2` 这种小数刻度(把 round_id 当连续量了),
只有两个点时几乎什么都看不见,图例是英文 `Value:`。

要的效果:

- x 轴是**离散的轮次**,标签用序号或 `HH:MM`(取 `started_at`),绝不出现小数。
- 柱子高度 = `context_tokens`。柱子按 `outcome` 上色:`answered` 用强调色,
  `stuck` 用危险色,`waiting` 用警告色,其余用中性色。深色底上要有足够对比。
- 悬停显示:`卡 #13 · 14:32 · 上下文 54.3k · 输出 700 · $0.63 · answered`。
- 点柱子跳到那张卡(现在已经有这个行为,保留)。
- 只有 1~2 条数据时也要好看:柱子别拉伸到占满整个画布宽度,给固定最大宽度。
- 数据为空时显示空状态,不要画一个空坐标系。

### 通用要求

- 坐标轴、网格线、刻度文字全部用 token 色值(`--text-2`、`--border-subtle` 这类),
  深浅两主题都要读得清。现在网格线在深色下几乎看不见。
- y 轴数值大的用 `54.3k` 这种缩写,不要 `55,000` 占一大片。
- 图表容器随窗口宽度自适应(`ResizeObserver` 或 resize 事件重建)。
- 别引入新库,uPlot 已经够用。

## 三、顺带修一个数据绑定 bug

会话页顶部「当前 Session ID」显示成 `--`,「新鲜度」也是错的。
接口返回的结构是**嵌套**的:

```json
{"session": {"session_id":"a6f200f1-…","created_at":1789968033149,"reason":"init",
             "prev_id":null,"fresh":0},
 "rounds":[…], "rotations":[…], "total_cost_usd":63.2459, "context_tokens":54285}
```

现在代码读的是 `data.current_session_id` 和 `data.fresh`,都不存在。
应当读 `data.session.session_id`、`data.session.fresh`、`data.session.created_at`,
并且 `data.session` 可能为 `null`(还没建过会话),要处理。
Session ID 很长,显示成前 8 位 + 复制按钮比较好。

## 交付

说明你改了什么、颜色是怎么从 token 取到的、主题切换时图表怎么重建。
自检:`grep -nE '#[0-9a-fA-F]{3,6}|rgb\(' app.js app.css` 应当没有命中
(从 `getComputedStyle` 读出来的色值是运行时的,不算硬编码)。
