# 项目空间 · 块 B 实现记录

块 B 已落到 `feat/project-space-agent-core`。规格仍以
`project-space-agent-core-requirements.md` 为准。这里只记接线方式和有意没搬的部分。

公开讨论区没有改。没有团队行时群聊仍是 `invalid_group_chat`，不会调用 `build_team`。

## 接线

| 点 | 做法 |
|---|---|
| 规格 | `TeamAgentSpec` 增加 `ensure_team_on_start`、`team_desc`、`prompt_overrides`。`TeamMemberSpec.agent_spec` 可选。未知提示词键在 `build()` 抛 `AGENT_TEAM_CONFIG_INVALID`，合法名来自 `TeamSectionName.ALL` |
| 提示词 | `_apply_prompt_overrides` 放在 `build_team_static_sections` 返回前。空字符串删段，非空整段替换并保留原 `priority`。这个角色本来没有的段不会被补上。`TeamPolicyRail` 和外部 CLI 的 `build_team_member_system_prompt` 都走这里。覆盖只进 `TEAM_POLICY` 参数，不进工具排除列表 |
| 声明即成团 | `Kernel.start` 在现有探测之后，旗标为真且没有团行时调用 `TeamAgent.ensure_team_built`。成功后把 `team_row_present` 置真，这次启动后半段的调度器激活仍会走到。缺 spec 或缺 `team_backend` 抛 `RuntimeError`。全员 `SHUTDOWN` 后的 `clean_team` 仍先执行，清掉团行后这次启动会重建 |
| 已有团行 | `build_team` 在能力上限校验通过后、改写开关和 `create_team` 之前返回。名册、描述和本次传入的开关都不改。`BuildTeamTool` 的 `display_name` 读库里的原值 |
| 加减成员 | `Runner.spawn_team_member` / `remove_team_member`。会话用新的 `_resolve_team_session_id`。池里没有该团，或给出的会话和 `current_session_id` 不一致，返回 `team_not_active`。不支持的角色在写入 `agents` 之前返回 `unsupported_role_type` |
| 成员规格 | 接受的角色在拉起前把 `agent_spec` 写入当前 leader 的 `spec.agents[member_name]`，并调用 `persist_session_manifest`。`SpawnPayloadBuilder` 持有同一份 spec，拉起时 `model_dump` 能带上这个键 |
| 被动真人 | `spawn_passive_human` 之后直接返回，不调用 `auto_start_member`。HITT 关闭时原样返回后端原因 |
| 停机 | `remove_team_member` 先看名册。不存在或已在 `MEMBER_DEPARTED_STATUSES` 则 `ok=True`。否则把 `shutdown_member` 的结果转成返回值。真人持有任务的拒绝仍在 `shutdown_member` 里，被动真人沿用 `is_live_human_agent` |
| 汇报 | `progress_report/service.py`。`Runner.get_progress_report` 先拒绝非法 `scope`，再从池或 session bucket 取 spec。没有 spec 是 `ValueError("team_not_found")`。没有任务也没有本场 `history.jsonl` 时返回「暂无进展」，不调用模型。有材料但没有模型是 `ValueError("report_model_unavailable")`。有材料时一次 `model.invoke` |

## 检视时改过的地方

不支持的角色原先会先把 `agent_spec` 写进 `agents` 再返回失败。现在先判定角色，拒绝的请求不改 spec，也不持久化。

## 没搬的东西

caozhenhua 工作区里这几处没有照搬：

- `ensure_team_built` 在 `team_backend is None` 时直接返回。
- spec 缺失时用默认团名继续 `build_team`。
- 进展汇报把缺 spec、缺模型、没有 leader 私有历史都收成「暂无进展」。
- 从每个成员的 checkpoint 恢复私有对话，并用 map-reduce 多次调用模型。
- 不支持角色的原因写成 `unsupported role_type: {role}`。本分支用稳定值 `unsupported_role_type`。

名册上的描述不算「成员产出」。没有任务、也没有本场公开讨论时，直接返回「暂无进展」。
