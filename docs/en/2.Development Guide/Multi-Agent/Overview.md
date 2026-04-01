Multi-Agent refers to a system architecture where multiple independent Agents are organized to collaborate through inter-agent communication to accomplish complex tasks. Compared to a single Agent, a multi-agent system enables parallel workloads, role specialization, and significantly outperforms in tasks that are large-scale, logically complex, or require multi-role coordination.

The openJiuwen multi-agent framework is designed around two core concepts: **TeamRuntime** (the communication runtime) and **BaseTeam** (the team abstraction). It supports both lightweight direct-runtime usage and full team encapsulation exposed as a unified interface. The **Agent as Tool** capability further allows any Agent to be invoked as a tool by another Agent.

# Core Concepts

## TeamRuntime

`TeamRuntime` is the core runtime for multi-agent communication, responsible for:

- **Agent Registration and Management**: Lazily creates Agent instances using the Card + Provider pattern, maintaining Agent metadata in a unified registry.
- **Message Routing**: Built-in `MessageBus` supports both point-to-point (P2P) and publish-subscribe (Pub-Sub) communication patterns.
- **Lifecycle Management**: Provides `start()` / `stop()` and async context manager support for safe resource cleanup.

`TeamRuntime` can be used standalone or as the communication backbone of `BaseTeam`.

## CommunicableAgent

`CommunicableAgent` is a mixin class that equips Agents with communication capabilities. Agents that inherit it can call `send()`, `publish()`, and `subscribe()` directly inside their `invoke()` logic to communicate with other Agents in the team. The runtime binds automatically when the Agent is first instantiated—no manual setup required.

## BaseTeam

`BaseTeam` is the abstract base class for teams, exposing a unified interface using the **Card + Config** pattern:

- `TeamCard`: Immutable team identity (id, name, description, member list, etc.).
- `TeamConfig`: Mutable runtime parameters (max agents, message timeout, etc.).
- All Agent management is delegated to an internal `TeamRuntime`.
- Subclasses implement `invoke()` / `stream()` to serve as a complete team.

## Agent as Tool

openJiuwen's `AbilityManager` supports registering an `AgentCard` as an Ability. When the host Agent's LLM decides to call a sub-Agent, the framework automatically retrieves the instance from `ResourceManager` and executes its `invoke()`. The result is returned as a `ToolMessage`, making the entire process transparent to the caller.

# Communication Patterns

| Pattern | Method | Characteristics |
|---------|--------|-----------------|
| Point-to-Point (P2P) | `runtime.send()` / `agent.send()` | One-to-one, synchronously waits for response |
| Publish-Subscribe (Pub-Sub) | `runtime.publish()` / `agent.publish()` | One-to-many broadcast, fire-and-forget |
| Hybrid | P2P + Pub-Sub combination | Flexibly orchestrates complex collaboration flows |

Subscriptions support both exact matching and wildcard patterns (`*`, `?`), e.g., `task_events`, `task_*`.

# Feature Overview

| Module | Description |
|--------|-------------|
| TeamRuntime + CommunicableAgent | Lightweight runtime for directly orchestrating multi-agent collaboration |
| BaseTeam | Team encapsulation exposing a unified invoke/stream interface |
| Agent as Tool | Allows an Agent to be invoked as a tool by another Agent |
| AgentTeams | Leader-Teammate collaboration framework for cross-process, persistent, and recoverable teamwork |

## AgentTeams Architecture (Leader-Teammate Collaboration)

AgentTeams completes complex tasks through coordinated work between a Leader and Teammates, with support for cross-process execution, persistence, and automatic recovery.

### Core Concepts

**Leader**

- Responsible for task decomposition, assignment, and coordination
- Manages the lifecycle of team members
- Processes user input and routes it to the appropriate teammate

**Teammate**

- Executes tasks in a specific domain
- Communicates with the Leader through the messaging system
- Can run independently in a separate process

**Architecture Components**

- **Transport**: Handles inter-agent message delivery (supports `pyzmq` and `team_runtime`)
- **Storage**: Persists team state, task lists, and messages (supports `sqlite`)
- **CoordinationLoop**: Manages agent execution flow and event handling

### Team Lifecycle

| Mode | Description |
|------|------|
| Temporary | Automatically dissolves the team after the task is completed; suitable for one-off tasks |
| Persistent | Preserves team state and members across sessions, supporting resume and crash recovery |

### Teammate Execution Modes

| Mode | Description |
|------|------|
| Plan Mode | A teammate must get approval from the Leader before completing a task; suitable for scenarios requiring strict control |
| Build Mode | A teammate completes tasks directly without approval; suitable when teammates are trusted |

### Advanced Features

- **Health Check and Auto Recovery**: The Leader periodically checks teammate process status and automatically restarts unhealthy members
- **Resume Support**: Supports recovering team state and members from previous sessions
- **Cross-Process Communication**: Uses `pyzmq` to support collaboration across processes and hosts

### Suitable Scenarios

- Complex tasks that require cross-process collaboration
- Long-running team projects
- Scenarios that require persistence and recovery
- Production environments that require health checks and automatic recovery

# Related Documentation

- [AgentTeams Guide](./AgentTeams.md)
- [TeamRuntime and CommunicableAgent](./TeamRuntime-and-CommunicableAgent.md)
- [BaseTeam](./BaseTeam.md)
- [Agent as Tool](./AgentAsTool.md)
