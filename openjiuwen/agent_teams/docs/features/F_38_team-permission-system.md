# Team Permission System — leader-mediated ASK resolution

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-06-17 |
| 范围 | `agent_teams/rails/team_permission_rail.py`（新增）、`harness/rails/security/tool_security_rail.py`、`harness/rails/interrupt/confirm_rail.py`、`harness/security/models.py`、`harness/security/narrowing.py`（新增）、`agent_teams/tools/member_options.py`（新增）、`agent_teams/schema/blueprint.py`、`agent_teams/schema/team.py`、`agent_teams/agent/agent_configurator.py`、`agent_teams/agent/spawn_manager.py`、`agent_teams/agent/coordination/handlers/message.py`、`agent_teams/rails/team_context.py`、`agent_teams/rails/elements.py`、`agent_teams/rails/team_tool_rail.py`、`agent_teams/tools/tool_factory.py`、`agent_teams/tools/tool_member.py`、`agent_teams/tools/team.py`、`agent_teams/tools/locales/{cn,en}.py`、`agent_teams/tools/database/{engine,member_dao,message_dao}.py`、`agent_teams/tools/memory_database.py`、`agent_teams/tools/message_manager.py`、`agent_teams/tools/models.py`、`agent_teams/monitor/models.py`、`core/single_agent/interrupt/{handler,response}.py` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/ tests/unit_tests/harness/security/` → 251 passed, 16 skipped |
| Refs | #751 |

## 背景

`TeamToolApprovalRail` 仅覆盖 `approval_required_tools` 列出的工具——leader 为指定工具名
配置"需要审批"后 teammate 才触发中断。对于不在该列表中的工具，如果权限引擎判定 ASK，
teammate 路径没有对应的审批机制：要么走用户前端 HITL（单 agent 路径），要么静默 deny。

团队场景需要更完整的权限覆盖：teammate 的所有工具调用统一经过 `PermissionEngine` 三级
判定（ALLOW / DENY / ASK），ASK 结果经内部消息通道路由给 leader 审批，leader 调
`approve_tool` 后 teammate 自动恢复——无需用户前端交互。

## 决策

1. **`TeamPermissionRail` 继承 `PermissionInterruptRail`**：复用完整的 `PermissionEngine`
   三级判定 + auto_confirm + 持久化钩子。新增 `should_persist_to_disk() → False`
   （leader 审批 session-scoped，不写 teammate 本地 YAML）和
   `should_emit_interrupt_output() → False`（审批不产生用户可见输出）。

2. **`TeamApprovalOrchestrator` 作为 hosted 确认路径**：实现
   `RequestPermissionConfirmationHook`，注入到 `ToolPermissionHost`。收到 ASK 结果后
   发消息给 leader，返回 `"interrupt"` 让 rail 挂起 teammate。

3. **`InterruptRequest.silent=True`**：team permission 的 ASK interrupt 不生成
   `__interaction__` 输出。`ToolInterruptHandler` 对 `silent=True` 的请求跳过 payload
   收集，agent 挂起但不向前端输出交互提示。

4. **`protocol="json"` 消息通道 + DB fallback**：leader `approve_tool` 写
   `protocol="json"` DB message（`type=tool_approval_result`）。teammate 的
   `MessageHandler._try_parse_approval_payload` 识别该类消息并 `resume_interrupt`，
   解决 interrupt-resolving 的 fallback delivery。

5. **`enable_permissions=True` 替代 `TeamToolApprovalRail`**：两者互斥。
   `AgentConfigurator` 在 `enable_permissions=True` 时挂 `TeamPermissionRail`，
   不挂 `TeamToolApprovalRail`。leader 在 `build_mode` 下也保留 `approve_tool`。

6. **`permissions_override` per-member narrowing**：`spawn_teammate.permissions`
   接收 `{tool_name: level_string}` dict，经 `narrow_permissions` 收紧基础配置
   （只收紧、不放宽），存入 `TeamMember.options.permissions_override`。

7. **`parse_tool_args` 等受保护方法提升为公共方法**：`PermissionInterruptRail` 的
   `_parse_tool_args` / `_format_args_preview` / `_parse_confirm_payload` 去掉
   `_` 前缀，因为 `TeamApprovalOrchestrator`（非子类）需要调用它们——符合 G.CLS.11
   规则。

## 拒绝的方案

- **在 `TeamToolApprovalRail` 上加 PermissionEngine 继承**：破坏双 rail 分层，
  `TeamToolApprovalRail` 是简单中断 rail，加引擎后职责模糊。
- **用 event bus 做 interrupt resume**：pub/sub event 可能丢失（进程重启 /
  zmq 断连），DB message 是可靠 fallback。
- **`_parse_tool_args_from_str` 方法**：`TeamPermissionRail._parse_confirm_payload`
   的 str 分支最初调用不存在的方法，实际应与父类一致用 `json.loads` + 递归调用。

## 验证基线

- `pytest tests/unit_tests/agent_teams/ tests/unit_tests/harness/security/`
- Import 测试：`from openjiuwen.agent_teams.rails.team_permission_rail import TeamPermissionRail, TeamApprovalOrchestrator`
- `narrow_permissions` 单测覆盖收紧规则（allow→ask→deny 单调、deny→allow 自动修正）

## 已知遗留

- `TeamPermissionRail` 暂无专项单测文件（行为由 `PermissionInterruptRail` 继承测试 +
  `test_member_options.py` + import 测试覆盖）
- `format_base_permissions_for_desc` 的 prompt 注入路径尚未接入 `TeamToolRail`
  的工具描述
