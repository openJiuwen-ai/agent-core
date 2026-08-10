# Team 权限护栏

Team 权限护栏用于限制团队成员可以调用哪些工具，并在高风险调用发生时把审批请求交给 Leader。它由两层机制组成：

- `tool_permissions.py` 定义 Leader、Teammate、Human Agent 在不同团队模式下可见的内置协作工具集合。
- `TeamPermissionRail` 对 Teammate 的实际工具调用执行 `allow`、`ask`、`deny` 决策，并把 `ask` 请求路由给 Leader。

这两层解决的问题不同：前者控制团队协作工具是否装配，后者控制已经装配的工具在本次调用中能否执行。

> `openjiuwen.agent_teams.tools.tool_permissions` 是框架内部的角色工具集合，不是 YAML 中名为 `tool_permissions` 的配置段。应用侧权限策略使用 `permissions`，团队开关使用 `TeamAgentSpec.enable_permissions`。

## 权限决策

| 级别 | 行为 |
|------|------|
| `allow` | 直接执行工具 |
| `ask` | 中断 Teammate 当前调用，向 Leader 发送审批请求 |
| `deny` | 拒绝执行工具 |

启用团队权限后，`TeamPermissionRail` 替代旧的 `TeamToolApprovalRail`。当规则返回 `ask` 时，`TeamApprovalOrchestrator` 会向 Leader 发送工具名、调用 ID、命中的规则和参数摘要；Leader 使用 `approve_tool` 给出决定后，Teammate 从中断点恢复。

Leader 的审批只在当前 session 内有效，不会写入共享权限配置。审批响应会记录 `decided_by="leader"`，便于审计决定来源。

## 启用团队权限

在团队规格上打开总开关：

```python
from openjiuwen.agent_teams import DeepAgentSpec, TeamAgentSpec

spec = TeamAgentSpec(
    agents={
        "leader": DeepAgentSpec(),
        "teammate": DeepAgentSpec(),
    },
    team_name="secured_team",
    spawn_mode="inprocess",
    enable_permissions=True,
)
```

`enable_permissions=True` 负责切换团队审批链路，并确保 Leader 保留 `approve_tool`。具体的基础权限策略由部署使用的 Harness 权限配置提供，常见结构如下：

```yaml
permissions:
  enabled: true
  schema: tiered_policy
  defaults:
    "*": ask
  tools:
    read_file: allow
    write_file: ask
    bash: deny
  file_guard:
    enabled: true
    defaults:
      read: ask
      write: ask
      exec: deny
```

`permissions` 支持 `tools`、`defaults`、`rules`、`approval_overrides` 和 `file_guard` 等字段。`enable_permissions` 与 `permissions.enabled` 应同时开启：前者启用 Team 的 Leader 审批编排，后者启用 Harness 的权限策略评估。

## 成员级权限收窄

Leader 动态创建 Teammate 时，可以通过 `spawn_teammate.permissions` 为该成员设置更严格的工具权限：

```json
{
  "member_name": "auditor",
  "display_name": "只读审计员",
  "desc": "检查实现和测试，不修改文件",
  "permissions": {
    "write_file": "deny",
    "bash": "ask"
  }
}
```

成员覆盖遵循“只能收紧，不能放宽”：

| 基础权限 | 成员覆盖 | 生效权限 |
|----------|----------|----------|
| `allow` | `ask` | `ask` |
| `allow` | `deny` | `deny` |
| `ask` | `deny` | `deny` |
| `deny` | `allow` | `deny` |
| `ask` | `allow` | `ask` |

未在基础 `tools` 中显式配置的工具，先按 `defaults.<tool>`、`defaults["*"]`、最终回退 `ask` 的顺序求出基础级别，再与成员覆盖取更严格值。覆盖值只接受 `allow`、`ask`、`deny`。

成员覆盖会随成员记录持久化，成员重启时重新应用；它不会改写团队共享的基础权限策略。

## Leader 审批流程

一次 `ask` 调用的生命周期如下：

1. Teammate 请求调用工具。
2. 权限引擎匹配规则并返回 `ask`。
3. `TeamApprovalOrchestrator` 向 Leader 发送审批消息。
4. Teammate 的工具调用进入中断状态。
5. Leader 检查工具、参数和命中规则，调用 `approve_tool`。
6. 审批结果写入团队消息存储，Teammate 恢复并执行或拒绝该调用。

如果审批请求消息发送失败，该请求不会被视为批准。应用也不应通过普通消息文本模拟审批，必须走 `approve_tool`，以便正确关联工具调用 ID 和中断状态。

## 内置团队工具权限

框架按角色和调度模式装配协作工具：

- Leader 拥有建队、成员管理、任务管理、消息和审批工具。
- Teammate 只获得当前 `dispatch_mode` 和 `teammate_mode` 所需的任务、消息工具。
- Human Agent 使用专门的 `HUMAN_AGENT_TOOLS` 集合。
- HITT、Swarmflow 等能力关闭时，对应工具不会装配。

这些集合是框架不变量。业务应用不应直接修改 `LEADER_TOOLS` 或 `MEMBER_TOOLS_BY_DISPATCH`；需要限制工具时使用权限策略和成员级收窄。

## 安全建议

1. 默认规则使用 `ask` 或 `deny`，只对明确的只读工具设置 `allow`。
2. 写文件、执行命令、外部网络和凭证访问分别制定规则，不要用一个宽泛规则全部放行。
3. 对只读角色在创建时显式收窄写入工具，即使团队基础策略当前允许写入。
4. Leader 审批前展示完整工具名、规范化参数和命中规则；敏感参数在 UI 和日志中脱敏。
5. 把 `decided_by`、调用 ID、规则、决定和 session ID 写入审计日志。
6. `file_guard` 可独立于工具级权限启用，用于限制文件读、写、执行边界。

## 常见问题

### 开启 `enable_permissions` 后为什么没有弹出用户审批？

Team 模式的 `ask` 默认由 Leader 审批，不直接交给最终用户。需要最终用户参与时，可让 Leader 通过 [HITT](./HITT人机交互团队模式.md) 联系人类成员，但最终仍由 Leader 调用 `approve_tool` 完成协议层审批。

### 为什么成员设置 `allow` 后仍然被拒绝？

成员权限覆盖只能收紧。基础权限为 `deny` 时，成员覆盖为 `allow` 的有效结果仍是 `deny`。

### `approval_required_tools` 和 Team 权限有什么区别？

`approval_required_tools` 是旧的指定工具审批方式。`enable_permissions=True` 时由 `TeamPermissionRail` 和分层权限策略统一处理，不再挂载 `TeamToolApprovalRail`。
