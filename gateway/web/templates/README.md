# 驿站 Web 控制台前端视图层

## 文件清单
- `templates/base.html`: 定义外壳结构（侧边栏、顶栏、主题切换等），包含静态资源的引入。
- `templates/index.html`: 首页，定义实例列表的初始数据及自动刷新逻辑。
- `templates/instance.html`: 实例详情页壳结构，具体五大 Tab 内容由 JavaScript 渲染并切换。
- `templates/new.html`: 新建向导页面，利用原生 JS 控制分步展示及状态。
- `templates/login.html`: 登录页面。
- `static/app.css`: 补充排版网格 (`.grid-2`, `.grid-3`)，日志滚动条，图标调整等样式。
- `static/app.js`: 核心前端逻辑（请求 API，拉取 JSON 后绘制 HTML，EventSource 渲染图表/日志）。

## 页面结构与接口调用
- **/login**: 使用原生表单 POST 到 `/login`。
- **/**: 页面初始数据由模板直接注入至 `window.INITIAL_INSTANCES`。前端通过 `renderInstances` 显示卡片。并设立 30 秒轮询调用 `GET /api/instances` 进行数据刷新。调用 `/api/i/{inst}/action/{name}` 执行暂停/恢复。
- **/i/{inst}**: 页面为单页应用（SPA）结构（不含路由，利用 tab 切换与 JS 渲染内容）。
  - **概览**: 结合 `GET /api/i/{inst}/overview` 和 `uPlot` 渲染今日图表。
  - **卡**: 结合 `GET /api/i/{inst}/cards` 进行分页及状态过滤。行点击通过 `GET /api/i/{inst}/cards/{id}` 拉取详情并在抽屉层显示。
  - **会话**: 结合 `GET /api/i/{inst}/session`，渲染会话明细与图表。
  - **日志**: 使用 `EventSource` 连接 `GET /api/i/{inst}/logs/{stream}`，接收实时 SSE，支持暂停滚动及文本过滤。
  - **配置**: 结合 `GET /api/i/{inst}/config` 动态生成表单。提交时构造 FormData POST 到 `/api/i/{inst}/config`。
- **/new**: 3 步新建向导。分别对应 `POST /api/new/init`、`POST /api/new/verify` 和 `POST /api/new/enable` 接口。

## HTMX 属性使用说明
本项目的接口以返回 JSON 数据为主（以便结合前端灵活排版与图表工具渲染），因此采用了基于 Vanilla JS (`fetch`) 的局部刷新与 `EventSource` 处理。
在 `base.html` 中保留了 `hx-ext="sse"` 及 `<script>` 的包含，以确保日后如需接入原生 HTML 片段流时具备基础的 HTMX 底座能力，但本项目绝大多数动态操作已由原生 JS 高效完成。

## 主题切换工作原理
遵循硬性约束，禁止硬编码颜色。主题系统依托于 `/static/tokens.css` 内建的 `light-dark()` 机制：
- 用户点击顶栏的主题按钮时，调用 `setTheme(t)`。
- 该函数通过更新 `document.documentElement.dataset.theme`（设为 `light`、`dark` 或清除）来触发 `color-scheme` 更改。
- 选择结果被同步到 `localStorage` 中并在页面初始化时立即读取恢复。所有的颜色都受到 `:root[data-theme="..."]` 的管辖，不含任何强制性的硬编码 `rgb()` 或 `#fff`。
