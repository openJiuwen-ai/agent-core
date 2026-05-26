# Agent Teams Interaction

外部交互入口子系统。SDK / 用户 / leader 通过这里把消息送进团队的三种视角（GodView / Operator / HumanAgent），由 `runtime/manager.py:_dispatch_payload` 路由到 `UserInbox` / `HumanAgentInbox`。HITT（Human in the Team）的全部 runtime 表面都落在这一层。

## 模块构成

| 文件 | 作用 |
|---|---|
| `payload.py` | `GodViewMessage` / `OperatorMessage` / `HumanAgentMessage` 三种交互视角的 dataclass + `InteractPayload` Union + `DeliverResult(ok, message_id, reason)` 统一返回类型 |
| `router.py` | `parse_interact_str(body)` 纯语法解析(`# / $ / @member` 前缀 → typed payloads，不查 roster)；`parse_mention(raw) -> (target, body) \| None` 纯函数；`resolve_targets(payloads, *, member_exists)` async 后处理：严格匹配 `@member` recipient，未知 mention 折回为"无 @ 消息"投给 leader/avatar(保留原文，见 F_23)；`is_reserved_name(name)` 校验保留名 |
| `user_inbox.py` | `UserInbox`：user 侧显式 API。`broadcast` / `direct` / `deliver_to_leader`，全部返回 `DeliverResult` |
| `human_agent_inbox.py` | `HumanAgentInbox`：human_agent 对外发声，仅在 HITT 启用时可用。`send` 成功返 `DeliverResult`；HITT 关闭抛 `HumanAgentNotEnabledError`，未知 sender 抛 `UnknownHumanAgentError`（manager 层捕获转 `DeliverResult.failure(reason)`） |

## 调用链

- `Runner.interact_agent_team(payload, *, team_name, session_id)` 接收 `InteractPayload`，bare `str` 作为 `GodViewMessage` 的便捷形式。
- 三视角到 inbox 的 dispatch 统一在 `runtime/manager.py:_dispatch_payload`：GodView → `deliver_to_leader`，Operator(target=None/x) → `UserInbox.broadcast/direct`，HumanAgent → `HumanAgentInbox.send`。
- 旧的 `@xxx body` 解析从 `agent/dispatcher.py` 移到这里——dispatcher 只保留调用点。`TeamAgent.broadcast()` 和 `TeamAgent.human_agent_say()` 也走这一层。

## HITT（Human in the Team）

### `enable_hitt` 是分层开关

- `TeamAgentSpec.enable_hitt`（spec 层）= 能力天花板（capability ceiling）。True 才允许 HITT；False 时所有 human-agent 创建路径全部拒绝。
- `build_team(enable_hitt=...)`（工具参数，`Optional[bool]`）= 本次实例的运行时开关。`None` 继承 spec；`True` 显式启用（要求 spec=True，否则报错）；`False` 显式禁用（即使 spec=True 也覆盖，跳过预配的 HUMAN_AGENT 并 warning）。

### 人类成员的来源

- **静态**：在 `TeamAgentSpec.predefined_members` 显式声明 `role_type=HUMAN_AGENT` 成员（自定 `member_name`，可多人）。框架不再隐式注入默认 `human_agent`。
- **动态**：leader 在已建团后通过 `spawn_member(role_type='human_agent', member_name=..., display_name=..., desc=...)` 拉新人类成员加入。`role_type='human_agent'` 时禁止传 `model_name` / `prompt`（由框架内置模板托管）。

### 一致性约束（`TeamAgentSpec.build()` 时 fail-fast）

- `enable_hitt=False` 且 `predefined_members` 含 HUMAN_AGENT → `AGENT_TEAM_CONFIG_INVALID`（特性禁了但预配了人）。
- `enable_hitt=True` 且 `predefined_members` 无 HUMAN_AGENT → 允许（动态 spawn 路径）。

`_resolve_team_mode`（`agent_configurator.py:57`）只把**非 HUMAN_AGENT** 的 predefined member 计入 `hybrid` 派生 —— 所以纯 HITT 团队（仅声明人类成员）仍然是 `default` 模式，leader 保留 `spawn_member` 工具。

### 运行约束（代码层 + Prompt 层双重保证）

1. `human_agent` 是保留成员名（`constants.RESERVED_MEMBER_NAMES`），用作动态 spawn 的默认人类成员名；自定 HUMAN_AGENT 成员名可避开此保留名。普通 teammate 的 predefined 成员仍然不允许撞保留名（`_validate_reserved_names`）。
2. human-agent 走标准 UNSTARTED → spawn 流程（与 teammate 一致），但工具集仅保留 `view_task` + `member_complete_task`（`HUMAN_AGENT_TOOLS`）；rail 装配会剥离 `FirstIterationGate` / `TeamToolApprovalRail`。
3. 一旦 `task.assignee` 指向某个 human-agent 且状态 CLAIMED，`UpdateTaskTool` 拒绝 reassign 和 cancel；批量 cancel 链路也跳过。
4. 发送给 human-agent 的点对点消息与广播 **保持 `is_read=False`**——human-agent 与 teammate 共用 `MessageHandler._process_unread_messages` poll 路径，由该路径在 deliver 完成后调 `mark_message_read`。在写入侧自动标已读会绕过 poll 路径，让 avatar 的 DeepAgent 永远收不到消息。见 `docs/features/F_20_human-agent-mailbox-unread-flip.md`。
5. TeamPolicyRail 注入 `team_hitt` section（priority=12），按 role 给 leader/teammate/human_agent 下达角色特定的行为约束。section 注入条件来自 `backend.hitt_enabled()` —— 反映运行时 effective flag，不依赖 roster 是否已 spawn。
6. 团队事件（task 指派 / message / broadcast）流向 human-agent harness 时**直接**走 coordination 的 `deliver_input`（与 teammate 路径同），但渲染文本走 `hitt.*` i18n 模板：`hitt.task_assigned_to_self_human`（前缀 `[任务指派给控制者]`）/ `hitt.msg_received_for_human`（前缀 `[转发给控制者的{msg_type}]`），文案里指代 avatar 背后真人时用「控制者 / controller」，区别 leader 侧的「用户 / user」。avatar 见到这些前缀时不应自主调 send_message / member_complete_task / claim_task —— 行为约束由 `prompts/sections.py::_hitt_section_human_agent_cn/en` 同步保证。SDK 的 `on_inbound` 回调通道（`MessageHandler._notify_human_agent_inbound`）保留作为可选的 out-of-band 通知，不在 task 指派路径上复制。
