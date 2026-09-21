# 驿站 yizhan

飞书 → Claude Code 网关:一个飞书应用绑定一个项目,长连接收消息,合并成问题卡,按卡唤起 `claude -p --resume <固定会话>` 处理并回群。常驻会话、非常驻进程。

- 设计:`docs/superpowers/specs/2026-09-21-yizhan-design.md`
- 代码:`gateway/`(listener / dispatcher / fgctl)
- 每个实例:`~/.config/yizhan/<inst>.toml` + `~/.local/state/yizhan/<inst>/`(state.db 与机器人工作台 workspace/)
- 原项目只以 `--add-dir` 挂入,零污染。
