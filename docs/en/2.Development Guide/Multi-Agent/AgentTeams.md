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

- **Transport**: Handles inter-agent message delivery (supports `pyzmq` and `team_runtime`)
- **Storage**: Persists team state, task lists, and messages (supports `sqlite`)
- **CoordinationLoop**: Manages agent execution flow and event handling

## Quick Start

```python
import asyncio
from openjiuwen.agent_teams import (
    create_agent_team,
    DeepAgentSpec,
    TransportSpec,
    StorageSpec,
    WorkspaceSpec,
    TeamModelConfig,
    MessagerTransportConfig
)
from openjiuwen.core.foundation.llm.schema.config import (
    ModelClientConfig,
    ModelRequestConfig,
)
from openjiuwen.core.runner import Runner

async def main():
    # Initialize the Runner
    await Runner.start()

    # Build model configuration
    model_config = TeamModelConfig(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI",
            api_key="your-api-key",
            api_base="your-api-base-url",
            timeout=120,
        ),
        model_request_config=ModelRequestConfig(
            model="your-model-name",
            temperature=0.2,
            top_p=0.9,
        ),
    )

    # Build transport configuration (used by the Leader)
    transport_config = MessagerTransportConfig(
        backend="pyzmq",
        team_id="demo_team",
        node_id="team_leader",
        direct_addr="tcp://127.0.0.1:{leader_port}",
        pubsub_publish_addr="tcp://127.0.0.1:{pub_port}",
        pubsub_subscribe_addr="tcp://127.0.0.1:{sub_port}",
        metadata={"pubsub_bind": True},
    )

    # Create the team
    leader = create_agent_team(
        agents={
            "leader": DeepAgentSpec(
                model=model_config,
                workspace=WorkspaceSpec(root_path="./workspace"),
                max_iterations=200,
                completion_timeout=600.0,
            ),
            "teammate": DeepAgentSpec(
                model=model_config,
                workspace=WorkspaceSpec(root_path="./workspace"),
                max_iterations=200,
                completion_timeout=600.0,
            ),
        },
        team_name="demo_team",
        teammate_mode="build_mode",
        transport=TransportSpec(type="pyzmq", params=transport_config.model_dump()),
        storage=StorageSpec(type="sqlite", params={"connection_string": "./team.db"}),
    )

    # Stream execution
    async for chunk in Runner.run_agent_streaming(
        agent=leader,
        inputs={"query": "Create a team of 3 people to discuss the future of AI"},
        session="demo_session",
    ):
        print(chunk, end="", flush=True)

    await Runner.stop()

asyncio.run(main())
```

## Configuration Details

### DeepAgentSpec

```python
DeepAgentSpec(
    model=TeamModelConfig(...),      # Model configuration
    workspace=WorkspaceSpec(...),    # Workspace
    max_iterations=200,              # Maximum number of iterations
    completion_timeout=600.0,        # Completion timeout
    system_prompt="Custom system prompt",  # Optional: custom prompt
    tools=[...],                     # Optional: tool list
    rails=[...],                     # Optional: rail list
)
```

### TransportSpec

```python
# PyZMQ backend (recommended)
transport_config = MessagerTransportConfig(
    backend="pyzmq",
    team_id="team_id",
    node_id="team_leader",
    direct_addr="tcp://{host}:{direct_port}",
    pubsub_publish_addr="tcp://{host}:{pub_port}",
    pubsub_subscribe_addr="tcp://{host}:{sub_port}",
    metadata={"pubsub_bind": True},
)

transport = TransportSpec(type="pyzmq", params=transport_config.model_dump())
```

**Port allocation notes**:

- The Leader requires 3 ports: `direct`, `pubsub_publish`, and `pubsub_subscribe`
- Each Teammate also requires 3 ports
- It is recommended to reserve a contiguous port range for each member

### StorageSpec

```python
# SQLite storage
storage = StorageSpec(
    type="sqlite",
    params={"connection_string": "./team.db"},
)
```

## Execution Modes

### Streaming Execution (Recommended)

```python
async for chunk in Runner.run_agent_streaming(
    agent=leader,
    inputs={"query": "Task description"},
    session="session_id",
):
    print(chunk, end="", flush=True)
```

### Interactive Mode

```python
# Start a background streaming task
stream_task = asyncio.create_task(
    Runner.run_agent_streaming(
        agent=leader,
        inputs={"query": "Initial task"},
        session="session_id",
    )
)

# Send follow-up input
await leader.interact("Additional instruction")

# Send a direct message to a specific teammate via @mention
await leader.interact("@teammate_member_id Please focus on the data analysis part")
```

The `@member_id message` syntax routes the message directly to the target teammate, bypassing the leader's agent logic. The sender is recorded as `"user"` in the message table.

## Team Lifecycle Modes

### Temporary

- The team is automatically dissolved after the task is complete
- Suitable for one-off tasks
- Default mode

```python
leader = create_agent_team(
    ...,
    lifecycle="temporary",
)
```

### Persistent

- Team state and members persist across sessions
- After `invoke()` / `stream()` completes, the team enters standby instead of shutting down
- Teammate processes stay alive with polls paused via `TEAM_STANDBY` event
- Subsequent `invoke()` calls automatically resume the coordination loop
- Supports cross-session resume via `resume_persistent_team()`
- Suitable for long-running teams

```python
leader = create_agent_team(
    ...,
    lifecycle="persistent",
)
```

## Recovery and Persistence

### Resume a Persistent Team

For persistent teams that are still in standby (same process), use `resume_persistent_team()` to start a new round:

```python
from openjiuwen.agent_teams import resume_persistent_team

# Resume in a new session (team processes are still alive)
leader = await resume_persistent_team(leader, new_session_id="round_2")

# Run the next round
async for chunk in leader.stream(inputs={"query": "Next task"}):
    print(chunk)
```

### Recover from a Session

For crash recovery or cross-process restore, use `recover_agent_team()`:

```python
from openjiuwen.agent_teams.factory import recover_agent_team

# Recover the team Leader
leader = await recover_agent_team(session_id="previous_session_id")

# Recover all teammates
await leader.recover_team()

# Continue execution
async for chunk in leader.stream(inputs={"query": "Continue the task"}):
    print(chunk)
```

## Teammate Execution Modes

### Plan Mode

- A teammate must get approval from the Leader before completing a task
- Suitable for scenarios that require strict control

```python
leader = create_agent_team(
    ...,
    teammate_mode="plan_mode",
)
```

### Build Mode

- A teammate completes tasks directly without approval
- Suitable when teammates are trusted
- Default mode

```python
leader = create_agent_team(
    ...,
    teammate_mode="build_mode",
)
```

## Predefined Team Members

You can pre-configure team members to skip the dynamic `spawn_member` step. When `predefined_members` is provided, all members are automatically registered in the database and the leader uses a simplified workflow without the `spawn_member` tool.

```python
from openjiuwen.agent_teams.schema.team import TeamMemberSpec, TeamRole

leader = create_agent_team(
    agents={...},
    team_name="my_team",
    predefined_members=[
        TeamMemberSpec(
            member_id="analyst",
            name="DataAnalyst",
            role_type=TeamRole.TEAMMATE,
            persona="Expert data analyst",
        ),
        TeamMemberSpec(
            member_id="writer",
            name="ReportWriter",
            role_type=TeamRole.TEAMMATE,
            persona="Technical writer",
        ),
    ],
    transport=TransportSpec(...),
    storage=StorageSpec(...),
)
```

## Health Check and Auto Recovery

AgentTeams includes a built-in health check mechanism:

1. The Leader periodically checks the status of teammate processes.
2. When an unhealthy teammate is detected, it is restarted automatically.
3. Up to 3 retries are attempted, using an exponential backoff strategy.
4. If the restart succeeds, a `MemberRestartedEvent` is published.
5. If the restart fails, the teammate is marked with `ERROR` status.

## Environment Variables

| Variable | Description | Default |
|------|------|--------|
| `API_BASE` | Base URL for the LLM API | - |
| `API_KEY` | LLM API key | - |
| `MODEL_NAME` | Model name | - |
| `MODEL_PROVIDER` | Model provider | OpenAI |
| `MODEL_TIMEOUT` | Model request timeout (seconds) | 120 |
| `LLM_SSL_VERIFY` | SSL certificate verification | true |
| `IS_SENSITIVE` | Sensitive information mode | false |

## Notes

1. **Runner lifecycle**: All `TeamAgent` instances must run between `Runner.start()` and `Runner.stop()`.
2. **Environment initialization**: Initialize the environment before executing Python-related commands:

   ```bash
   source .venv/bin/activate
   export PYTHONPATH=.:$PYTHONPATH
   ```

3. **Port allocation**: When using the `pyzmq` backend, ensure the Leader and teammates use different port combinations.
4. **Logging**: Team logs are automatically separated by member to simplify debugging.
