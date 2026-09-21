# 驿站 yizhan

飞书 → Claude Code 网关:一个飞书应用绑定一个项目,长连接收消息,合并成问题卡,按卡唤起
`claude -p --resume <固定会话>` 处理并回群。**常驻会话、非常驻进程。**

- 设计:`docs/superpowers/specs/2026-09-21-yizhan-design.md`
- 实施计划:`docs/superpowers/plans/2026-09-21-yizhan-gateway.md`

## 三个组件

| | 是什么 | 怎么跑 |
|---|---|---|
| `listener` | 常驻长连接,只写库;@ 我时立即拉起 dispatcher | `yizhan-listener@<inst>.service` |
| `dispatcher` | oneshot,归卡 → 取卡 → 唤起 claude → 收尾,循环到没卡 | timer 每分钟兜底 + listener 触发 |
| `fgctl` | claude 会话内唯一的读卡/回群/销账入口(带证据门) | 会话里直接调 |

原项目只以 `--add-dir` 挂入,**零污染**:机器人的 CLAUDE.md、skill、归档、记忆全在自己的工作台目录。

## 装一个实例

```bash
cd /data/projects/yizhan
uv venv .venv && uv pip install -e '.[dev]'
.venv/bin/yizhan install-units                       # 装 systemd user 单元(一次就够)
.venv/bin/yizhan init drama-auto --project /data/projects/drama-auto \
    --from-dir /data/projects/drama-auto/.claude/skills/feedback-group-reply
vi config/drama-auto.toml                            # 填 app_id/app_secret/alert_open_id/group_allow
systemctl --user enable --now yizhan-listener@drama-auto yizhan-dispatcher@drama-auto.timer
loginctl enable-linger $USER
```

飞书应用那边:企业自建应用 + 机器人能力;权限 `im:message`、`im:message.group_msg`、
`im:message.p2p_msg`、`im:chat:readonly`、`im:resource`、`im:message.reactions:write`、
`contact:user.base:readonly`;事件与回调选**长连接**,订阅 `im.message.receive_v1` 和机器人进群事件。
坑:长连接项须先有客户端连上才能保存 —— 先起 listener 再点保存。

## 每个实例的东西在哪

配置跟着项目走,状态跟着用户走:

```
/data/projects/yizhan/config/<inst>.toml   凭证、项目目录、白名单(0600,已 gitignore)
~/.local/state/yizhan/<inst>/
  ├── state.db                          事件、卡、回复、会话(WAL)
  ├── attachments/  logs/
  └── workspace/                        claude 的 cwd
      ├── CLAUDE.md                     机器人纪律(证据分级、处置边界)
      ├── .claude/settings.json         允许清单
      ├── .claude/skills/<skill>/       六步流程、fgctl 用法、project.toml
      └── notes/                        问题归档
```

## 坑:systemd 下 claude 报 403

`claude` 在这台机器上要走本地代理才连得上 API,而 systemd 服务没有 shell 里的代理变量,
探活会直接 `403 Request not allowed`,卡全堆着没人处理(2026-09-21 实测)。配置里写清楚:

```toml
[claude_env]
https_proxy = "http://127.0.0.1:7890"
http_proxy  = "http://127.0.0.1:7890"
```

飞书接口不走代理,客户端已经关了 `trust_env`,两边互不影响。

## 权限:第一天之后就换掉

模板里的 `permission_args = ["--dangerously-skip-permissions"]` 只适合关起门来跑通,因为它
**连工作台 settings.json 的 deny 清单一起绕过**,而群消息是不可信输入。跑通后改成:

```toml
permission_args = ["--permission-mode", "dontAsk", "--permission-prompts", "none"]
```

无头模式下未预批的调用直接被拒、不会挂住;拒绝会记日志,按日志补 allow 清单。

## 日常运维

```bash
yizhan status drama-auto        # 会话 id、心跳、各状态的卡数、服务是否在跑
yizhan pause  drama-auto        # 停 timer,然后可以在终端 claude --resume <sid> 人工接管
yizhan resume drama-auto
yizhan rotate drama-auto        # 换新主会话(会话脏了/转录损坏时)
journalctl --user -u yizhan-listener@drama-auto -f
```

**不 pause 的时候不要人工 resume 主会话**,两边同时写转录会打架。

## 开发

```bash
.venv/bin/pytest -q             # 全部单测,不碰网络也不碰真 claude(用 tests/fakes/fake_claude.py)
```

已实测(2026-09-21):跨进程 `--resume` 能续上下文;工作台 CLAUDE.md 的改动对已 resume 的会话
**立即生效**,改完下一张卡就按新规矩走,不用 rotate。
