AgentTeams is a Leader-Teammate collaboration framework that completes complex tasks through coordinated work between a Leader and Teammates. Compared to directly using TeamRuntime, AgentTeams provides a higher-level abstraction with support for cross-process execution, persistent state, and automatic recovery, making it suitable for scenarios requiring process isolation, long-running sessions, or crash recovery.

# Core Concepts

## Leader

- Responsible for task decomposition, assignment, and coordination
- Manages the lifecycle of team members
- Processes user input and routes it to the appropriate teammate

## Teammate

- Executes tasks in a specific domain
- Communicates with the Leader through the messaging system
- Can run independently in a separate process

## Architecture Components

- **Transport**: Handles inter-agent message delivery (supports `inprocess` and `pyzmq`)
- **Storage**: Persists team state, task lists, and messages (supports `sqlite` and `memory`)

# Team Lifecycle

| Mode | Description |
|------|-------------|
| Temporary | Automatically dissolves the team after the task is completed; suitable for one-off tasks |
| Persistent | Preserves team state and members across sessions, supporting resume and crash recovery |

# Teammate Execution Modes

| Mode | Description |
|------|-------------|
| Plan Mode | A teammate must get approval from the Leader before completing a task; suitable for scenarios requiring strict control |
| Build Mode | A teammate completes tasks directly without approval; suitable when teammates are trusted (default) |

# Teammate Spawn Modes

| Mode | Description |
|------|-------------|
| Inprocess | Teammates run as asyncio coroutines in the same process; suitable for single-process scenarios |
| Process | Teammates run as subprocesses; suitable for scenarios requiring process isolation (default) |

# Advanced Features

- **Health Check and Auto Recovery**: The Leader periodically checks teammate process status and automatically restarts unhealthy members
- **Persistent Standby**: Persistent teams enter standby after each round; teammate processes stay alive and resume automatically on the next `invoke()`
- **Resume Support**: Supports recovering team state via `resume_persistent_team()` (same process) or `recover_agent_team()` (crash recovery)
- **User @mention**: Users can send direct messages to specific teammates via `@member_name message` syntax, bypassing the leader
- **Predefined Members**: Pre-configure team members to skip dynamic spawning, with automatic DB registration
- **Cross-Process Communication**: Uses `pyzmq` to support collaboration across processes and hosts

# Suitable Scenarios

- Complex tasks that require cross-process collaboration
- Long-running team projects
- Scenarios that require persistence and recovery
- Production environments that require health checks and automatic recovery

# Related Documentation

- [AgentTeams Guide](./AgentTeams.md)