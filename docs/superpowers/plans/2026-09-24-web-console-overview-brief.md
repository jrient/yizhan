# 派单五:概览页重做 + 手机端布局 + 统计页两处

只改 `gateway/web/static/app.js`、`app.css`、`gateway/web/templates/*.html`。
不改 `.py`、接口、`tokens.css` / `base.css` / `chart-tokens.css`,不加依赖。
服务 `http://127.0.0.1:8760`,口令 `-9dvzp7feBusyoSO`,改完刷新即可(静态文件不用重启)。
接口真实返回样例:`/tmp/claude-1000/-data-projects-yizhan/777b2bc4-6daa-4630-9f9e-430fe5a2d582/scratchpad/api-samples-v2.json`。

**保留不要动**:`esc()`、`css()` 探针解析、`stateBadge()`、`legendKey()`、`BAR_RADIUS`、`ERROR_SLOT`、
`openCardDrawer()`、主题切换重绘逻辑、`.cards-table` 不换行样式、整数刻度 `incrs`、统计页已有五张图。

**硬规则**(违反即返工):
- 群里来的文本(`preview` `text` `sender_name` `label` `note` 告警正文)一律 `esc()`,包括 `title="…"` 属性里的。
- 颜色只用 token;不写 hex/rgb;不用 emoji,状态用 `.ui-dot` + 文字。
- 状态色 `--ok/--warn/--danger` 只表示好坏,不当系列色;系列色用 `--series-N` / `--accent`。
- 图表:单纵轴;图例文字用正文色(色标在旁边);计数轴整数刻度;不在每个点上标数字。
- JS 模板字符串里不要写 `\${` 和 `` \` ``(测试会查)。不要在 static/ 下留临时脚本。

## 一、概览页(`renderOverview`,整体重写)

概览只回答「此刻 + 今天」,趋势归统计页签,不要重复。自上而下三区:

### 区一 此刻状态(一行,桌面 5 格,窄屏 2 列)

| 格 | 内容 | 数据 |
|---|---|---|
| 健康 | `.ui-dot` + `summary.health_reason`;颜色按 `summary.health`(green/yellow/red/grey) | summary |
| 监听 | 「心跳 N 秒前」;listener 状态不是 active 或心跳 >600s 时标红 | `summary.heartbeat_ago_s`、`summary.units.listener` |
| 派卡 | `dispatcher_active` 为真 →「正在处理」;否则「上一轮 HH:MM 成功/失败」 | `dispatcher_active`、`summary.units.dispatcher_last`(`rc`、`at` 是 systemd 时间字符串) |
| 当前焦点 | `current_card` 有值 →「卡 #N」+ 摘要(esc,单行省略),可点开抽屉;有 `stuck_cards` → 标红「N 张卡死」;都没有 →「空闲,等新消息」 | `current_card`、`stuck_cards` |
| 上下文 | 大字 `422k`,下面 `.ui-progress` 水位条,分母 `summary.context_window`,小字「/ 1M 窗口」;`context_window` 为 null 时只显示数字不画条。占比 ≥70% 条用 `--warn`,≥90% 用 `--danger`(这是好坏,合规) | `summary.context_tokens`、`summary.context_window` |

**不要**再显示「下一轮:待定」和技能名(技能名在配置页签)。

### 区二 需要处理(告警)

- 标题「告警」,右侧小字「24 小时内 N 条」。
- 用 `<details class="ui-accordion">`:`summary` 一行 = 中文 `label`(后端已翻好,如「卡 #18 权限拒绝」)+ 右侧时间;
  展开后 `text` 放 `<pre class="ui-code wrap">`(esc),为 null 就不给展开。
- 分两组:24 小时内(逐条列出,默认折叠正文);更早的收成一个 `details`「更早 N 条」,展开后才列。
- 24 小时内一条都没有 → `.ui-empty`「24 小时内没有告警」,更早组照常。
- 告警里 `label` 以「卡 #N」开头的,标题上的卡号可点开 `openCardDrawer(N)`。

### 区三 今天(`.grid-2`,窄屏单列)

**左:今日数字 + 每小时柱状图**
- 一行紧凑数字:消息 `summary.today_msgs` · 卡 `today_cards` · 回复 `today_replies` · 首回中位 `today_first_reply_p50_s`(格式化成「2分7秒」,null 显示「—」)· 用量 `$today_cost_usd`(小字「API 折算」)。
- 图:`hourly[]` 分组柱状图,两个系列 消息 `--series-1`、卡 `--series-2`,同一小时两根柱并排;
  柱顶 4px 圆角、基线 0、柱间 2px 底色缝;有图例(色块 + 正文色文字);整数刻度;悬停显示该小时两项数字。
  今天全是 0 → `.ui-empty`「今天还没有消息」,不画空坐标。
  替换现在的面积折线图(去掉面积填充、每点圆点、彩色图例文字)。

**右:最近的卡**
- `recent_cards`(5 张),每行:`#id` · `stateBadge` · 发起人 `sender_name` · 摘要 `preview`(单行省略)· 首回 `first_reply_s`(格式化)· 相对时间 `created_at`。
- 整行可点 → `openCardDrawer(id)`。底部链接「全部卡 →」切到「卡」页签。
- 空 →「还没有卡」。
- 两列高度要对齐,不要出现一侧下面一大片空白。

## 二、手机端(整站,≤768px)

现在 390 宽时侧边栏不收起,整页横向溢出(scrollWidth 800)。要求:
- 侧边栏默认隐藏,顶栏左侧出现菜单按钮,点开侧边栏覆盖在内容上,点遮罩或导航项收起。
- 实例头部(名字 + 四个按钮)按钮换行,不溢出;页签条横向可滑,不换行成竖排。
- 所有 `.grid-2/.grid-3` 在窄屏单列;统计页 KPI 两列。
- 验收:`/`、概览、卡、会话、统计、日志、配置七个页面在 390 宽时 `document.documentElement.scrollWidth <= 390`。
  (卡列表表格可以在自己的容器里横滑,但页面本身不能。)

## 三、统计页两处

1. **错误图小数量看不见**:非零的堆叠段至少画 3px 高(真实值不变,提示框和表格仍显示原数)。
   现在 09-24 两条「权限拒绝」在 80 的刻度下是 0 像素。
2. **新增第 6 张图「每轮上下文」**:数据 `stats.context[]`(每轮一条:`at`、`issue_id`、`context_tokens`、
   `context_window`、`session_id`、`cost_usd`)。
   - 折线 + 点(轮次少,点 8px),横轴时间,纵轴 token(显示 `200k` `400k`),颜色 `--accent`。
   - 如果 `context_window` 有值,画一条水平参考线表示窗口上限(`--text-3` 细线,右端标「窗口 1M」)。
   - `session_id` 变化的位置画一条竖直细线(`--border`)标「新会话」。
   - 悬停:时间、卡 #N、上下文 k、本轮用量 $。
   - 有表格视图(时间、卡、上下文、用量)。区间内没有轮次 →「近 N 天没有派卡」。
   - 标题下小字:「越长越慢、单轮越贵;接近窗口会被自动压缩」。

## 四、自检(我会跑)

- `node --check gateway/web/static/app.js`
- `.venv/bin/python -m pytest -q tests/test_web_static.py`
- 深浅主题下概览、统计来回切无 JS 报错;390 宽七个页面无横向溢出
- XSS:摘要为 `<img src=x onerror="window.__xss=1">` 的卡,`window.__xss` 保持未定义
