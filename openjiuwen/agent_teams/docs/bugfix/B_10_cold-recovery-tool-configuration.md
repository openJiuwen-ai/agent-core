# 冷恢复沿用旧工具后端，忽略当前部署配置

| 项 | 值 |
|---|---|
| 日期 | 2026-10-31 |
| 范围 | agent/team_agent.py 的 recover_from_session |

已存在的 Team checkpoint 没有 sys_operation 或保存 LOCAL 时，宿主为当前请求提供 SANDBOX 工具配置，冷恢复仍使用旧成员 spec，导致命令在 AgentServer 内执行。页面可以返回正常内容，但没有创建预期的工具沙箱。

根因是恢复合并只覆盖 live build_context、embedding 配置与模型池，未覆盖显式工具配置。恢复入口现在对同名成员及 Reviewer 复制当前显式 sys_operation，保留 checkpoint 的其他成员字段；当前值为 None 时保持 checkpoint 行为。复制后再 configure，确保恢复的 Leader 和后续成员使用同一份更新后的 blueprint。不替换整份 agents、不清除会话或重建团队名册。
