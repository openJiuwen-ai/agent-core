# HITT Human-in-the-Team Mode

HITT (Human-In-The-Team) allows a real user to participate in an AgentTeams team as a member. A human member has a stable `member_name`, can receive messages from the Leader or Teammates, and can send direct messages to other members with `@member_name`.

HITT is useful when a task requires human judgment, approval, on-site information, or access to an external system unavailable to the model. Unlike ordinary follow-up input, a human is represented in the roster with the `human_agent` role, and their messages carry an explicit member identity over the team message bus.

## How It Works

HITT uses a human-member-and-avatar model:

- The external user supplies real decisions and input.
- The Human Agent Avatar represents that user in the team runtime and handles team messages and input without an `@mention`.
- `HumanAgentInbox` is the application-facing input endpoint. It routes user input either to the team bus or to the Avatar.
- `register_human_agent_inbound()` registers the team-to-user callback. Applications can use it to publish WebSocket, queue, or UI notifications.

Routing follows these rules:

| Input | Route |
|-------|-------|
| `@team_leader approve the proposal` | Sends directly to `team_leader` as the human member |
| `@reviewer check the result` | Sends directly to `reviewer` as the human member |
| Text without an `@mention` | Passes to the human member's Avatar |
| A team member sends to the human member | Invokes the registered `on_inbound` callback |

## Configure a HITT Team

`TeamAgentSpec.enable_hitt` is the capability ceiling. The Spec must set it to `True` before a team instance can enable HITT. The framework does not create a default human member automatically. Declare the human roster explicitly or let the Leader call `spawn_human_agent` at runtime.

```python
from openjiuwen.agent_teams import (
    DeepAgentSpec,
    StorageSpec,
    TeamAgentSpec,
    TeamMemberSpec,
    TeamRole,
)

spec = TeamAgentSpec(
    agents={"leader": DeepAgentSpec()},
    team_name="review_team",
    spawn_mode="inprocess",
    enable_hitt=True,
    predefined_members=[
        TeamMemberSpec(
            member_name="operator",
            display_name="Human Reviewer",
            role_type=TeamRole.HUMAN_AGENT,
            persona="Approves high-risk operations and validates final results",
        ),
    ],
    storage=StorageSpec(type="memory"),
)
```

Configuration constraints:

- Declaring a predefined `HUMAN_AGENT` while `enable_hitt=False` raises an error during `build()`.
- Setting `enable_hitt=True` without predefined human members is valid; members can be spawned dynamically.
- `build_team(enable_hitt=False)` can disable an open capability for one instance, but cannot enable a capability closed by the Spec.
- A `member_name` must start with a lowercase ASCII letter and contain only lowercase letters, digits, and hyphens.

By default, ordinary Teammates do not see the concrete human roster in their system prompts. Enable role transparency only when required:

```python
spec = TeamAgentSpec(
    # Other configuration omitted.
    enable_hitt=True,
    expose_human_agents_to_teammates=True,
)
```

This setting does not affect the Leader or Human Agents. The Leader always sees the complete roster, and a Human Agent sees the roster including itself.

## Connect Bidirectional Messaging

The following example shows the application-facing integration. In a complete runtime, `leader` comes from `spec.build()`, and Runner or the `build_team` flow initializes the team.

```python
from openjiuwen.agent_teams import HumanAgentInbox
from openjiuwen.agent_teams.interaction import HumanAgentInboundEvent

leader = spec.build()
backend = leader.team_backend
human_avatar = get_operator_avatar()

async def push_to_ui(event: HumanAgentInboundEvent) -> None:
    await websocket.send_json({
        "member": event.member_name,
        "sender": event.sender,
        "content": event.body,
        "broadcast": event.broadcast,
        "message_id": event.message_id,
    })

backend.register_human_agent_inbound("operator", push_to_ui)

inbox = HumanAgentInbox(
    backend,
    backend.message_manager,
    agent_lookup=lambda name: human_avatar if name == "operator" else None,
)

# Send directly to the Leader as the human member.
await inbox.send("@team_leader I approve the release")

# Pass input without an @mention to the operator Avatar.
await inbox.send("Summarize the current task status")
```

`agent_lookup` must return the running Avatar for the target human member. Messages containing an `@mention` go directly to the team bus.

## Spawn Human Members Dynamically

When `enable_hitt=True`, the Leader receives the `spawn_human_agent` tool and can add a human member after team construction:

```json
{
  "member_name": "domain-expert",
  "display_name": "Domain Expert",
  "desc": "Answers questions about equipment specifications and site constraints"
}
```

After spawning the member, the application must still register an inbound callback and route that user's input through the corresponding `HumanAgentInbox`.

## Error Handling

Applications can handle these public exceptions:

| Exception | Meaning |
|-----------|---------|
| `HumanAgentNotEnabledError` | HITT is not enabled for the current team |
| `UnknownHumanAgentError` | The requested human member does not exist or is not registered |

Production integrations should also handle offline users, duplicate messages, and delayed responses. Use `message_id` as an idempotency key and place callback events in a reliable queue before pushing them to the UI.

## Best Practices

1. Use a stable, business-oriented `member_name`; do not use a temporary display name as a routing identifier.
2. Keep `expose_human_agents_to_teammates=False` unless role transparency is required.
3. Humans may respond slowly. Avoid blocking the whole task with polling; let the Leader manage waiting, timeouts, and reassignment.
4. For approvals, record the `message_id`, sender, decision, and timestamp for auditing.
5. HITT determines who participates in the team. Use [Team Permission Guardrails](./Team Permission Guardrails.md) to control whether tools may execute.

See `tests/system_tests/agent_swarm/agent_team_hitt_phase2_e2e.py` for a complete integration example that does not require a live model.
