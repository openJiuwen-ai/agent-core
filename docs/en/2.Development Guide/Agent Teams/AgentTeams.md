# AgentTeams Guide

AgentTeams is a multi-agent collaboration framework that completes complex tasks through coordinated work between a Leader and Teammates.

## Core Concepts

### Leader
- Responsible for task decomposition, assignment, and coordination
- Manages the lifecycle of team members
- Processes user input and routes it to the appropriate teammate

### Teammate
- Executes tasks in a specific domain
- Communicates with the Leader through the messaging system
- Can run independently in a separate process

### Architecture Components
- **Transport**: Handles inter-agent message delivery (supports `inprocess` and `pyzmq`)
- **Storage**: Persists team state, task lists, and messages (supports `sqlite`, `postgresql`, and `memory`)

## Quick Start

```python
import asyncio
import yaml
from openjiuwen.agent_teams import TeamAgentSpec
from openjiuwen.core.runner import Runner

async def main():
    # Initialize Runner
    await Runner.start()

    # Load team spec from YAML config
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    spec = TeamAgentSpec.model_validate(cfg)
    leader = spec.build()

    # Streaming execution
    async for chunk in Runner.run_agent_team_streaming(
        agent_team=leader,
        inputs={"query": "Create a 3-person team to discuss the future of AI"},
        session="demo_session",
    ):
        print(chunk, end="", flush=True)

    await Runner.stop()

asyncio.run(main())
```

### Configuration Example (config.yaml)

```yaml
agents:
  leader:
    model:
      model_client_config:
        client_provider: "${MODEL_PROVIDER}"
        api_key: "${API_KEY}"
        api_base: "${API_BASE}"
        timeout: 120
      model_request_config:
        model: "${MODEL_NAME}"
        temperature: 0.2
        top_p: 0.9
    max_iterations: 200
    completion_timeout: 600.0
  teammate:
    model:
      model_client_config:
        client_provider: "${MODEL_PROVIDER}"
        api_key: "${API_KEY}"
        api_base: "${API_BASE}"
        timeout: 120
      model_request_config:
        model: "${MODEL_NAME}"
        temperature: 0.2
        top_p: 0.9
    max_iterations: 200
    completion_timeout: 600.0
    
transport:
  type: inprocess

team_name: demo_team
lifecycle: temporary
teammate_mode: build_mode
spawn_mode: inprocess  # Use inprocess mode, teammates run in the same process
leader:
  member_name: team_leader
  display_name: Team Leader
  persona: Project management expert
```

### Storage Configuration (SQLite / PostgreSQL)

```yaml
# SQLite (local file)
storage:
  type: sqlite
  params:
    connection_string: ./team_data/team.db

# PostgreSQL (recommended for distributed deployment)
storage:
  type: postgresql
  params:
    connection_string: postgresql+asyncpg://user:password@host:5432/agent_team
```

Notes:
- PostgreSQL uses the same `connection_string` field;
- Ensure the PostgreSQL service is running and reachable before startup;
- If you install optional extras, include the `postgres` extra (`asyncpg`).

## Configuration Details

| Config Item | Description |
|--------|------|
| `agents` | Configure DeepAgent by role; must include `leader`, `teammate` is optional |
| `team_name` | Team name |
| `lifecycle` | `temporary` (one-off) or `persistent` (cross-session retained) |
| `teammate_mode` | `build_mode` (direct completion) or `plan_mode` (requires approval) |
| `spawn_mode` | `inprocess` (same process) or `process` (subprocess) |
| `leader` | Leader identity config (member_name, display_name, persona) |

For other config items (transport, storage, predefined_members, workspace, etc.), see [API Docs](../API%20Docs/openjiuwen.agent_teams/agent_teams.md).

## Execution Modes

### Streaming Execution (Recommended)

```python
async for chunk in Runner.run_agent_team_streaming(
    agent_team=leader,
    inputs={"query": "Task description"},
    session="session_id",
):
    print(chunk, end="", flush=True)
```

### Interactive Mode

```python
# run_agent_team_streaming() returns an async iterator,
# so wrap the consumer in a coroutine before create_task().
async def consume_stream():
    async for chunk in Runner.run_agent_team_streaming(
        agent_team=leader,
        inputs={"query": "Initial task"},
        session="session_id",
    ):
        print(chunk, end="", flush=True)

stream_task = asyncio.create_task(consume_stream())

# Send follow-up input
await leader.interact("Additional instruction")

# Send direct message to a specific teammate via @mention
await leader.interact("@teammate_member_name Please focus on the data analysis part")
```

The `@member_name message` syntax routes the message directly to the target teammate, bypassing the leader's agent logic. The sender is recorded as `"user"` in the message table.

## Recovery and Persistence

Persistent teams (`lifecycle: persistent`) keep their state in the session checkpoint and the team storage. **All recovery paths share a single entry point — pass the spec and the target session to `Runner.run_agent_team_streaming`**. The `runtime` subsystem inspects the in-memory pool and the persisted session to pick the right branch automatically:

| Scenario | Auto-selected branch |
|---|---|
| Same process, switch to a new session | `new_team_in_session_warm` (reuse the in-memory Leader) |
| Return to a previously persisted session | `warm_recover` (rebind the Leader to the session checkpoint) |
| Cold start after a process restart | `cold_recover` (rebuild Leader from the session bucket, respawn teammates) |
| Resume after pause | `resume_from_pause` (same Leader, same session) |

```python
# Single entry point: spec + target session_id, runtime decides cold vs warm.
async for chunk in Runner.run_agent_team_streaming(
    agent_team=spec,
    inputs={"query": "Next task"},
    session="round_2",
):
    print(chunk)
```

If you need to bypass `Runner` and obtain a leader instance directly (e.g. operations scripting), the low-level path is `TeamAgent.recover_from_session(session, team_name, runtime_spec=spec)` followed by `await agent.recover_team()`. Application code should stay on the `Runner` path.

## Health Check and Auto Recovery

AgentTeams includes a built-in health check mechanism:

1. The Leader periodically checks the status of teammate processes
2. When an unhealthy teammate is detected, it is restarted automatically
   - Up to 3 retries, using exponential backoff strategy
3. If restart succeeds, a `MemberRestartedEvent` is published
4. If restart fails, the teammate is marked with `ERROR` status

## Notes

1. **Runner lifecycle**: All `TeamAgent` instances must run between `Runner.start()` and `Runner.stop()`

2. **Environment initialization**: Initialize the environment before executing Python-related commands:
   ```bash
   source .venv/bin/activate
   export PYTHONPATH=.:$PYTHONPATH
   ```

3. **Logging**: Team logs are automatically separated by member to simplify debugging
