# 驿站 yizhan

把飞书群聊/私聊变成 Claude Code 的工单入口:一个飞书应用绑定一个项目,消息合并成问题卡,
按卡唤起 `claude -p --resume <固定会话>` 处理并回到原会话。**常驻会话,非常驻进程。**

- 一个实例 = 一个飞书机器人 + 一个项目 + 一条长期会话,多个实例互不干扰
- 原项目只以 `--add-dir` 挂入,**零污染**:机器人的 CLAUDE.md、skill、归档、记忆都在自己的工作台
- 回复必须带证据(`fgctl` 证据门),做不到的操作会如实告诉提问人并私聊管理员
- 自带 Web 控制台、工作时段、会话轮换、每小时上下文压缩

## 工作流程

```
飞书消息 ──长连接──▶ listener ──写库──▶ state.db ◀── 卡片 / 会话 / 统计 ──▶ web 控制台
                        │ @机器人时立即唤起
                        ▼
                   dispatcher(timer 每分钟兜底)
                        │ 归卡 → 取卡 → claude -p --resume <会话>
                        ▼
                   claude 会话 ──fgctl──▶ 读卡 / 回群 / 销账
                        ▲
                   compact timer(每小时,上下文超过门槛才压缩)
```

| 组件 | 是什么 | 怎么跑 |
|---|---|---|
| `listener` | 常驻长连接,只写库;@ 机器人时立即拉起 dispatcher | `yizhan-listener@<inst>.service` |
| `dispatcher` | oneshot,归卡 → 取卡 → 唤起 claude → 收尾,循环到没卡 | `yizhan-dispatcher@<inst>.timer` 每分钟兜底 + listener 触发 |
| `compact` | 每小时给常驻会话发 `/compact` | `yizhan-compact@<inst>.timer` |
| `web` | 控制台:实例管理与监控 | `yizhan-web.service`,默认 8760 端口 |
| `fgctl` | claude 会话内唯一的读卡/回群/销账入口(带证据门) | 会话里直接调 |

## 快速开始

需要:Linux + systemd(user 单元)、Python 3.12+、[uv](https://github.com/astral-sh/uv)、已登录的 `claude` CLI。

```bash
git clone git@github.com:jrient/yizhan.git && cd yizhan
uv venv .venv && uv pip install -e '.[dev]'
.venv/bin/yizhan install-units                       # 装 systemd user 单元(一次就够)
.venv/bin/yizhan init my-bot --project /path/to/your/project
vi config/my-bot.toml                                # 填 app_id / app_secret / alert_open_id / group_allow
systemctl --user enable --now yizhan-listener@my-bot yizhan-dispatcher@my-bot.timer yizhan-compact@my-bot.timer
loginctl enable-linger $USER                         # 退出登录后服务继续跑
```

`init` 可加 `--from-dir <已有 skill 目录>`,把现成的处理流程拷进工作台。

### 飞书应用配置

- 企业自建应用 + 机器人能力
- 权限:`im:message`、`im:message.group_msg`、`im:message.p2p_msg`、`im:chat:readonly`、`im:resource`、
  `im:message.reactions:write`、`contact:user.base:readonly`
- 事件与回调选**长连接**,订阅 `im.message.receive_v1` 和机器人进群事件
- 坑:长连接须先有客户端连上才能保存 —— 先起 listener 再点保存

## 文件布局

配置跟着仓库走,状态跟着用户走:

```
config/<inst>.toml                        凭证、项目目录、白名单(0600,已 gitignore)
~/.local/state/yizhan/<inst>/
  ├── state.db                            事件、卡、回复、会话(WAL)
  ├── attachments/  logs/
  └── workspace/                          claude 的 cwd
      ├── CLAUDE.md                       机器人纪律(证据分级、处置边界)
      ├── shared-rules.md                 公共规矩(自动同步,见下)
      ├── .claude/settings.json           允许清单
      ├── .claude/skills/<skill>/         处理流程、fgctl 用法、project.toml
      └── notes/                          问题归档
```

## 配置要点

完整字段见 [`gateway/templates/config.toml`](gateway/templates/config.toml),Web 控制台的配置页也能改。

| 字段 | 默认 | 说明 |
|---|---|---|
| `permission_args` | 跳过权限 | 正式运行切到受限模式,见下文「权限」 |
| `work_hours` | `06:00-24:00` | 北京时间派单时段,可跨午夜,`""` = 全天 |
| `compact_at_tokens` | `300000` | 上下文超过多少才压缩,`0` = 每次都压 |
| `rotate_at_tokens` | `0`(关) | 上下文超过多少就换新会话 |
| `reply_in_thread` | `true` | 回复开话题;关掉则按(会话,发起人)+10 分钟窗口归并 |
| `extra_dirs` | `[]` | 除项目外还要让 claude 读到的目录 |
| `[claude_env]` | 带本机代理 | 给 claude 子进程的环境变量,`""` = 删掉该变量 |

### 权限

每张卡都唤起一次 `claude -p`,能做什么由权限模式和工作台允许清单决定。新实例默认跳过权限方便调通,
正式运行要切到受限模式:

```toml
permission_args = ["--permission-mode", "dontAsk", "--permission-prompts", "none"]
```

受限模式下没预批的操作立即被拒、不会卡住;机器人在群里告诉提问人哪一步做不了,dispatcher 私聊管理员原命令。
两种模式的区别、允许清单增补方法、预演验证见 [`docs/权限.md`](docs/权限.md)。

### 公共规矩

所有机器人共用 [`shared/shared-rules.md`](shared/shared-rules.md),只改这一个文件。
dispatcher 每次唤起前把它同步进工作台,并保证工作台 `CLAUDE.md` 里有 `@shared-rules.md` 引用;
机器人自己改了也会被覆盖回去。文件名必须是英文 —— 中文文件名的 `@` 引用在 `claude -p` 下不生效。

### 工作时段

时段外 listener 照常收消息建卡,dispatcher 只是不开新卡,开工后按顺序处理;
到点不打断正在跑的卡,看门狗照常运行。

### 每小时 compact

`yizhan-compact@<inst>.timer` 每个整点给常驻会话发一次 `claude -p --resume <会话> /compact`。

- 和 dispatcher 抢同一把锁:正在处理卡就等,最多 30 分钟,拿不到就跳过这一小时
- 暂停中、还没有会话、上下文没到 `compact_at_tokens` 都不动;大小取自上一轮结束时记下的值
- `/compact` 不管压没压成都报 success,所以结果看流里的 `compact_result`:
  「Not enough messages」算 noop,其它失败记进错误统计 `compact_failed`

## Web 控制台

卡片墙一眼扫全部实例;点进去有概览、卡、会话、日志、配置五个页签;
还能在页面上新建实例(填凭证 → 当场验证 → 一键启用)。

```bash
yizhan web-init                          # 生成 config/web.toml 与随机口令(只打印一次)
systemctl --user enable --now yizhan-web
```

访问 `http://<本机地址>:8760`,输口令进。密钥字段在页面上只显示末四位。

## 日常运维

```bash
yizhan status <inst>        # 会话 id、心跳、各状态卡数、各服务是否在跑
yizhan pause  <inst>        # 停派单,之后可在终端 claude --resume <sid> 人工接管
yizhan resume <inst>
yizhan rotate <inst>        # 换新主会话(会话脏了/转录损坏时)
journalctl --user -u yizhan-listener@<inst> -f
```

**不 pause 的时候不要人工 resume 主会话**,两边同时写转录会打架。

## 踩过的坑

- **systemd 下 claude 报 403**:systemd 服务没有 shell 里的代理变量,探活直接 `403 Request not allowed`。
  代码默认给 claude 带上 `http://127.0.0.1:7890`(`gateway/config.py` 的 `DEFAULT_CLAUDE_ENV`),
  在 `[claude_env]` 里写同名键覆盖,写 `""` 删除。飞书接口不走代理(客户端关了 `trust_env`)。
- **跨进程 `--resume` 能续上下文**;工作台 CLAUDE.md 的改动对已 resume 的会话**立即生效**,不用 rotate。
- **小会话别压缩**:压完摘要可能比原文还大(实测 5k → 10k),还丢细节,所以 compact 有门槛。

## 开发

```bash
.venv/bin/pytest -q         # 全部单测,不碰网络也不碰真 claude(用 tests/fakes/fake_claude.py)
```

设计与计划:

- 总体设计 [`docs/superpowers/specs/2026-09-21-yizhan-design.md`](docs/superpowers/specs/2026-09-21-yizhan-design.md)
- 控制台设计 [`docs/superpowers/specs/2026-09-21-web-console-design.md`](docs/superpowers/specs/2026-09-21-web-console-design.md)
- 实施计划 [`docs/superpowers/plans/`](docs/superpowers/plans/)
