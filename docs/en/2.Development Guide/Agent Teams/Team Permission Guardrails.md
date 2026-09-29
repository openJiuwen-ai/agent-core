# Team Permission Guardrails

Team permission guardrails restrict which tools team members may call and route high-risk calls to the Leader for approval. The implementation has two layers:

- `tool_permissions.py` defines the built-in coordination-tool sets available to Leaders, Teammates, and Human Agents in each team mode.
- `TeamPermissionRail` evaluates actual Teammate tool calls as `allow`, `ask`, or `deny`, and routes `ask` decisions to the Leader.

The layers solve different problems. The first controls whether a coordination tool is mounted for a role; the second controls whether an already-mounted tool may execute for a specific call.

> `openjiuwen.agent_teams.tools.tool_permissions` is an internal role-to-tool definition, not a YAML section named `tool_permissions`. Applications configure policies through `permissions` and enable team orchestration through `TeamAgentSpec.enable_permissions`.

## Permission Decisions

| Level | Behavior |
|-------|----------|
| `allow` | Execute the tool immediately |
| `ask` | Interrupt the Teammate call and send an approval request to the Leader |
| `deny` | Reject the tool call |

When team permissions are enabled, `TeamPermissionRail` replaces the legacy `TeamToolApprovalRail`. If a policy returns `ask`, `TeamApprovalOrchestrator` sends the Leader the tool name, call ID, matched rule, and an argument preview. The Leader responds through `approve_tool`, after which the Teammate resumes from the interruption point.

Leader approvals are scoped to the current session and are never persisted into the shared permission configuration. Approval responses record `decided_by="leader"` for auditing.

## Enable Team Permissions

Enable the team-level switch on the team specification:

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

`enable_permissions=True` selects the team approval path and ensures that the Leader retains `approve_tool`. The Harness permission configuration supplies the underlying policy. A typical policy has this structure:

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

The `permissions` section supports `tools`, `defaults`, `rules`, `approval_overrides`, and `file_guard`. Enable both `enable_permissions` and `permissions.enabled`: the former activates Leader-mediated Team approval, while the latter activates Harness policy evaluation.

## Per-Member Permission Narrowing

When dynamically spawning a Teammate, the Leader can provide `spawn_teammate.permissions` to make that member more restrictive:

```json
{
  "member_name": "auditor",
  "display_name": "Read-Only Auditor",
  "desc": "Reviews implementation and tests without modifying files",
  "permissions": {
    "write_file": "deny",
    "bash": "ask"
  }
}
```

Member overrides can only narrow permissions:

| Base | Override | Effective |
|------|----------|-----------|
| `allow` | `ask` | `ask` |
| `allow` | `deny` | `deny` |
| `ask` | `deny` | `deny` |
| `deny` | `allow` | `deny` |
| `ask` | `allow` | `ask` |

For a tool absent from the base `tools` map, the framework resolves the base level from `defaults.<tool>`, then `defaults["*"]`, and finally falls back to `ask`. It then chooses the stricter result. Override values must be `allow`, `ask`, or `deny`.

The override is persisted with the member record and reapplied after member restart. It does not modify the team's shared base policy.

## Leader Approval Flow

An `ask` call follows this lifecycle:

1. A Teammate requests a tool call.
2. The permission engine matches a rule and returns `ask`.
3. `TeamApprovalOrchestrator` sends an approval message to the Leader.
4. The Teammate tool call enters the interrupted state.
5. The Leader reviews the tool, arguments, and matched rule, then calls `approve_tool`.
6. The result is written to team message storage, and the Teammate resumes to execute or reject the call.

If the approval request cannot be delivered, the call is not considered approved. Do not emulate approval with an ordinary text message; use `approve_tool` so the decision remains associated with the tool call ID and interruption state.

## Built-In Team Tool Permissions

The framework mounts coordination tools by role and dispatch mode:

- The Leader receives team construction, member management, task management, messaging, and approval tools.
- Teammates receive only the task and messaging tools required by the active `dispatch_mode` and `teammate_mode`.
- Human Agents use the dedicated `HUMAN_AGENT_TOOLS` set.
- Tools associated with HITT, Swarmflow, and other optional capabilities are not mounted when those capabilities are disabled.

These sets are framework invariants. Applications should not mutate `LEADER_TOOLS` or `MEMBER_TOOLS_BY_DISPATCH`; use permission policies and per-member narrowing instead.

## Security Recommendations

1. Use `ask` or `deny` as the default and allow only explicitly identified read-only tools.
2. Define separate rules for file writes, shell execution, external networking, and credential access.
3. Explicitly deny write tools for read-only roles even if the team's base policy currently allows them.
4. Show the complete tool name, normalized arguments, and matched rule before Leader approval; redact sensitive values in the UI and logs.
5. Audit `decided_by`, call ID, matched rule, decision, and session ID.
6. Enable `file_guard` to enforce file read, write, and execution boundaries independently of tool-level policy.

## FAQ

### Why does enabling `enable_permissions` not ask the end user directly?

In Team mode, `ask` decisions are mediated by the Leader. If an end user must participate, the Leader can contact a human member through [HITT](./HITT Human-in-the-Team Mode.md), but the Leader must still call `approve_tool` to complete the protocol-level approval.

### Why is a member still denied after setting `allow`?

Member overrides can only narrow permissions. If the base level is `deny`, an `allow` override remains effectively `deny`.

### How does `approval_required_tools` differ from Team permissions?

`approval_required_tools` is the legacy explicit tool-approval mechanism. When `enable_permissions=True`, `TeamPermissionRail` and the tiered permission policy handle approvals, and `TeamToolApprovalRail` is not mounted.
