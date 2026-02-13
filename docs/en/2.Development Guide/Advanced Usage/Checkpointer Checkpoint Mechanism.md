# Checkpointer Checkpoint Mechanism

Checkpointer (checkpoint) is the core mechanism in the openJiuwen framework for managing state persistence and recovery for Agents and workflows. It supports saving state at key execution points and restoring state when needed, enabling features such as interruption recovery and exception recovery.

## Core Concepts

### Purpose of Checkpointer

Checkpointer is primarily responsible for the following functions:

1. **State Persistence**: Save state at key execution points of Agents and workflows
2. **State Recovery**: Restore previously saved state when re-executing
3. **Interruption Recovery**: Support interruption-resume mechanism for workflows and Agents
4. **Exception Recovery**: Save state when exceptions occur for subsequent recovery

### Namespace Structure

Checkpointer uses namespaces to organize different types of state:

- **`SESSION_NAMESPACE_AGENT`** (`"agent"`): Namespace for Agent state under session
- **`SESSION_NAMESPACE_WORKFLOW`** (`"workflow"`): Namespace for workflow state under session (workflow's own state)
- **`WORKFLOW_NAMESPACE_GRAPH`** (`"workflow-graph"`): Namespace for graph state under workflow (separated from workflow's own state)

Key format: `session_id:namespace:entity_id:suffix`

## Checkpoint Types

openJiuwen provides multiple checkpoint implementations:

### 1. InMemoryCheckpointer (In-Memory Checkpoint)

In-memory checkpoint implementation where all state is saved in memory and lost after process restart. Suitable for development and testing scenarios.

**Features**:

- No additional configuration required
- High performance, suitable for rapid development
- Data not persisted, lost after process restart

**Usage Example**:

```python
from openjiuwen.core.session.checkpointer import InMemoryCheckpointer

# Create in-memory checkpoint instance
checkpointer = InMemoryCheckpointer()

# Use checkpointer for state management
# checkpointer will automatically save and restore state during Agent and workflow execution
```

### 2. PersistenceCheckpointer (Persistent Checkpoint)

Persistence-based checkpoint implementation using the `BaseKVStore` interface for state persistence, supporting any storage backend that implements `BaseKVStore`.

**Supported Storage Backends**:

- **SQLite**: Storage based on SQLite database
- **Shelve**: File storage based on Python shelve module

**Configuration Example**:

```python
from openjiuwen.core.session.checkpointer import (
    CheckpointerFactory,
    CheckpointerConfig,
)

# Using SQLite storage (basic configuration)
config = CheckpointerConfig(
    type="persistence",
    conf={
        "db_type": "sqlite",
        "db_path": "checkpointer.db"
    }
)
checkpointer = await CheckpointerFactory.create(config)

# Using SQLite storage (full configuration, recommended for high concurrency)
config = CheckpointerConfig(
    type="persistence",
    conf={
        "db_type": "sqlite",
        "db_path": "checkpointer.db",
        "db_timeout": 30,        # SQLite lock wait timeout in seconds, default 30
        "db_enable_wal": True    # Enable WAL mode to improve write performance, default True
    }
)
checkpointer = await CheckpointerFactory.create(config)
```

**SQLite Configuration Parameters**:

- `db_type`: Storage backend type, options: `"sqlite"` or `"shelve"`, default: `"sqlite"`
- `db_path`: Database file path, default: `"checkpointer"`
- `db_timeout`: SQLite database lock wait timeout in seconds, default: `30`
- `db_enable_wal`: Whether to enable SQLite WAL (Write-Ahead Logging) mode, default: `True`. WAL mode improves write
  performance

# Using Shelve storage
config = CheckpointerConfig(
    type="persistence",
    conf={
        "db_type": "shelve",
        "db_path": "checkpoint"
    }
)
checkpointer = await CheckpointerFactory.create(config)
```

### 3. RedisCheckpointer (Redis Checkpoint)

Redis-based checkpoint implementation supporting both standalone Redis and Redis Cluster modes. Suitable for production environments, supporting distributed deployment.

**Features**:

- Supports standalone Redis and Redis Cluster
- Supports TTL (Time To Live) configuration
- Supports refreshing TTL on read
- Suitable for distributed scenarios

**Configuration Example**:

```python
from openjiuwen.core.session.checkpointer import (
    CheckpointerFactory,
    CheckpointerConfig,
)

# Standalone Redis
config = CheckpointerConfig(
    type="redis",
    conf={
        "connection": {
            "url": "redis://localhost:6379"
        }
    }
)
checkpointer = await CheckpointerFactory.create(config)

# Redis Cluster mode
config = CheckpointerConfig(
    type="redis",
    conf={
        "connection": {
            "url": "redis://localhost:7000",
            "cluster_mode": True
        }
    }
)
checkpointer = await CheckpointerFactory.create(config)

# With TTL configuration
config = CheckpointerConfig(
    type="redis",
    conf={
        "connection": {
            "url": "redis://localhost:6379"
        },
        "ttl": {
            "default_ttl": 5,  # TTL of 5 minutes
            "refresh_on_read": True  # Refresh TTL on read
        }
    }
)
checkpointer = await CheckpointerFactory.create(config)
```

## Checkpoint Lifecycle

### Agent Checkpoint Lifecycle

Agent checkpoints manage state at the following points:

1. **`pre_agent_execute`**: Before Agent execution, restore Agent state
2. **`interrupt_agent_execute`**: When Agent needs to interrupt and wait for user interaction, save Agent state
3. **`post_agent_execute`**: After Agent execution completes, save Agent state

**Execution Flow**:

```text
Start Agent execution
    ↓
pre_agent_execute (restore state)
    ↓
Execute Agent logic
    ↓
If interruption needed → interrupt_agent_execute (save state)
    ↓
Execution complete → post_agent_execute (save state)
```

### Workflow Checkpoint Lifecycle

Workflow checkpoints manage state at the following points:

1. **`pre_workflow_execute`**: Before workflow execution, restore or clear workflow state
2. **`post_workflow_execute`**: After workflow execution, save or clear workflow state

**Execution Flow**:

```text
Start workflow execution
    ↓
pre_workflow_execute
    ├─ If InteractiveInput → restore workflow state
    └─ If not InteractiveInput → check state
        ├─ State exists and forced deletion not enabled → raise exception
        └─ State exists and forced deletion enabled → clear state
    ↓
Execute workflow logic
    ↓
post_workflow_execute
    ├─ Exception occurred → save state and raise exception
    ├─ Normal completion → clear state
    └─ Interruption needed → save state
```

## Using Checkpoints

### Configuring Checkpoint in Runner

Runner is the core executor of the openJiuwen framework. When Runner starts, it automatically initializes the configured checkpoint. This is the recommended approach because Runner manages checkpoint instances uniformly, ensuring all Agents and workflows use the same checkpoint configuration.

#### Configuration Method

Configure checkpoint through the `checkpointer_config` field of `RunnerConfig`:

```python
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.runner_config import RunnerConfig
from openjiuwen.core.session.checkpointer import CheckpointerConfig

# Create Runner configuration
runner_config = RunnerConfig()

# Configure checkpoint
runner_config.checkpointer_config = CheckpointerConfig(
    type="in_memory",  # or "persistence", "redis"
    conf={}
)

# Set Runner configuration
Runner.set_config(runner_config)

# Start Runner (will automatically initialize checkpoint)
await Runner.start()
```

#### Using In-Memory Checkpoint

Suitable for development and testing environments:

```python
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.runner_config import RunnerConfig
from openjiuwen.core.session.checkpointer import CheckpointerConfig

runner_config = RunnerConfig()
runner_config.checkpointer_config = CheckpointerConfig(
    type="in_memory",
    conf={}
)
Runner.set_config(runner_config)
await Runner.start()
```

#### Using Persistent Checkpoint (SQLite)

Suitable for single-machine production environments, using SQLite as storage backend:

```python
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.runner_config import RunnerConfig
from openjiuwen.core.session.checkpointer import CheckpointerConfig

runner_config = RunnerConfig()
runner_config.checkpointer_config = CheckpointerConfig(
    type="persistence",
    conf={
        "db_type": "sqlite",
        "db_path": "checkpointer.db",  # SQLite database file path
        "db_timeout": 30,               # Lock wait timeout in seconds, default 30
        "db_enable_wal": True           # Enable WAL mode to improve write performance, default True, recommended
    }
)
Runner.set_config(runner_config)
await Runner.start()
```

**Note**: In high concurrency scenarios (e.g., multiple tasks running simultaneously), it is recommended to:

- Keep `db_enable_wal: True` (enabled by default) to enable WAL mode and improve write performance
- Adjust `db_timeout` based on actual needs. You can increase this value appropriately if needed (e.g., 60 seconds)

#### Using Persistent Checkpoint (Shelve)

Using Python shelve module as storage backend:

```python
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.runner_config import RunnerConfig
from openjiuwen.core.session.checkpointer import CheckpointerConfig

runner_config = RunnerConfig()
runner_config.checkpointer_config = CheckpointerConfig(
    type="persistence",
    conf={
        "db_type": "shelve",
        "db_path": "checkpoint"  # Shelve file path (without extension)
    }
)
Runner.set_config(runner_config)
await Runner.start()
```

#### Using Redis Checkpoint

Suitable for distributed production environments, supporting both standalone Redis and Redis Cluster:

```python
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.runner_config import RunnerConfig
from openjiuwen.core.session.checkpointer import CheckpointerConfig

# Standalone Redis
runner_config = RunnerConfig()
runner_config.checkpointer_config = CheckpointerConfig(
    type="redis",
    conf={
        "connection": {
            "url": "redis://localhost:6379"
        }
    }
)
Runner.set_config(runner_config)
await Runner.start()

# Redis Cluster mode
runner_config = RunnerConfig()
runner_config.checkpointer_config = CheckpointerConfig(
    type="redis",
    conf={
        "connection": {
            "url": "redis://localhost:7000",
            "cluster_mode": True
        }
    }
)
Runner.set_config(runner_config)
await Runner.start()

# Redis with TTL configuration
runner_config = RunnerConfig()
runner_config.checkpointer_config = CheckpointerConfig(
    type="redis",
    conf={
        "connection": {
            "url": "redis://localhost:6379"
        },
        "ttl": {
            "default_ttl": 60,  # 60 minutes expiration
            "refresh_on_read": True  # Refresh TTL on read
        }
    }
)
Runner.set_config(runner_config)
await Runner.start()
```

#### Runner Initialization Process

Runner performs the following steps when starting:

1. **Check Configuration**: Check if `RunnerConfig.checkpointer_config` is configured
2. **Lazy Load Provider**: For `redis` type, lazily import Redis checkpointer provider to ensure registration
3. **Create Instance**: Create checkpoint instance through `CheckpointerFactory.create()`
4. **Set as Default**: Set the created checkpoint as the default checkpoint for all Agents and workflows to use
5. **Logging**: Record checkpoint initialization success or failure information

**Notes**:

- If `redis` type is configured but Redis dependencies are not installed, Runner startup will fail and prompt to install dependencies
- Checkpoint initialization failure will cause Runner startup to fail
- Once Runner starts successfully, all Agents and workflows executed through Runner will automatically use the configured checkpoint
- **Provider Registration Mechanism**: Runner automatically handles provider registration, no manual import needed

### Using in Agent

Agents automatically use the checkpoint configured in Runner for state management. If Runner has configured a checkpoint, Agents require no additional configuration:

```python
from openjiuwen.core.application import LLMAgent
from openjiuwen.core.runner import Runner

# Runner has configured checkpoint and started
# Create Agent (will automatically use Runner's configured checkpoint)
agent = LLMAgent(...)
# Agent execution will automatically use checkpoint for state management
```

If you need to use checkpoint separately outside Runner:

```python
from openjiuwen.core.application import LLMAgent
from openjiuwen.core.session.checkpointer import (
    CheckpointerFactory,
    CheckpointerConfig,
)

# If using Redis checkpointer, need to import first to register provider
# from openjiuwen.extensions.checkpointer.redis import checkpointer  # noqa: F401

# Configure checkpoint
checkpointer_config = CheckpointerConfig(
    type="in_memory",  # or "persistence", "redis"
    conf={}
)
checkpointer = await CheckpointerFactory.create(checkpointer_config)
CheckpointerFactory.set_default_checkpointer(checkpointer)

# Create Agent (checkpoint will be automatically integrated)
agent = LLMAgent(...)
# Agent execution will automatically use checkpoint for state management
```

### Using in Workflow

Workflows also automatically use the checkpoint configured in Runner for state management. If Runner has configured a checkpoint, workflows require no additional configuration:

```python
from openjiuwen.core.workflow import Workflow
from openjiuwen.core.runner import Runner

# Runner has configured checkpoint and started
# Create workflow (will automatically use Runner's configured checkpoint)
workflow = Workflow()
# Workflow execution will automatically use checkpoint for state management
```

If you need to use checkpoint separately outside Runner:

```python
from openjiuwen.core.workflow import Workflow
from openjiuwen.core.session.checkpointer import (
    CheckpointerFactory,
    CheckpointerConfig,
)

# If using Redis checkpointer, need to import first to register provider
# from openjiuwen.extensions.checkpointer.redis import checkpointer  # noqa: F401

# Configure checkpoint
checkpointer_config = CheckpointerConfig(
    type="persistence",
    conf={
        "db_type": "sqlite",
        "db_path": "workflow_checkpoint.db"
    }
)
checkpointer = await CheckpointerFactory.create(checkpointer_config)
CheckpointerFactory.set_default_checkpointer(checkpointer)

# Create workflow
workflow = Workflow()
# Workflow execution will automatically use checkpoint for state management
```

### Manual Checkpoint Management

You can also manually manage checkpoints. **Important**: If using extended checkpointer (such as Redis), you need to import the corresponding module first to ensure provider registration.

#### Using Built-in Checkpointer (Auto-registered)

Providers of type `in_memory` and `persistence` are automatically registered when importing `openjiuwen.core.session.checkpointer`:

```python
from openjiuwen.core.session.checkpointer import (
    CheckpointerFactory,
    CheckpointerConfig,
    InMemoryCheckpointer,
)

# Using in-memory checkpoint (auto-registered)
checkpointer = InMemoryCheckpointer()
CheckpointerFactory.set_default_checkpointer(checkpointer)

# Or create through factory
config = CheckpointerConfig(type="in_memory", conf={})
checkpointer = await CheckpointerFactory.create(config)
CheckpointerFactory.set_default_checkpointer(checkpointer)

# Using persistent checkpoint (auto-registered)
config = CheckpointerConfig(
    type="persistence",
    conf={"db_type": "sqlite", "db_path": "checkpoint.db"}
)
checkpointer = await CheckpointerFactory.create(config)
CheckpointerFactory.set_default_checkpointer(checkpointer)
```

#### Using Extended Checkpointer (Requires Pre-import)

For extended checkpointer (such as Redis), you need to import the corresponding module first to ensure provider registration:

```python
from openjiuwen.core.session.checkpointer import (
    CheckpointerFactory,
    CheckpointerConfig,
)

# Important: Must import Redis checkpointer module first to register provider
from openjiuwen.extensions.checkpointer.redis import checkpointer  # noqa: F401

# Now can use Redis checkpointer
config = CheckpointerConfig(
    type="redis",
    conf={"connection": {"url": "redis://localhost:6379"}}
)
checkpointer = await CheckpointerFactory.create(config)
CheckpointerFactory.set_default_checkpointer(checkpointer)
```

**Provider Registration Mechanism**:

- **Built-in Providers** (`in_memory`, `persistence`): Automatically registered when importing `openjiuwen.core.session.checkpointer`, no additional action needed
- **Extended Providers** (`redis`): Need to explicitly import the corresponding module to register
  - Import method: `from openjiuwen.extensions.checkpointer.redis import checkpointer`
  - When importing, the decorator `@CheckpointerFactory.register("redis")` executes, completing registration
- **Using in Runner**: Runner automatically handles provider registration, no manual import needed

#### Manual Checkpoint Instance Management

```python
from openjiuwen.core.session.checkpointer import CheckpointerFactory

# Get checkpoint
checkpointer = CheckpointerFactory.get_checkpointer()

# Check if session exists
exists = await checkpointer.session_exists("session_id")

# Release session resources
await checkpointer.release("session_id")

# Release resources for specific Agent
await checkpointer.release("session_id", agent_id="agent_id")
```

## Interruption Recovery Mechanism

Checkpointer supports interruption recovery mechanism for workflows and Agents.

### Workflow Interruption Recovery

When a workflow needs user interaction, it triggers an interruption and saves state:

```python
from openjiuwen.core.workflow import WorkflowComponent
from openjiuwen.core.session import InteractiveInput

class InteractiveNode(WorkflowComponent):
    async def invoke(self, inputs, session, context):
        # Trigger interruption, wait for user input
        user_input = await session.interact("Please enter your choice:")
        return {"result": user_input}

# First execution, triggers interruption
output = await workflow.invoke({"input": "test"}, session)

# Resume execution, provide user input
user_input = InteractiveInput(raw_inputs="User choice")
output = await workflow.invoke(user_input, session)
```

### Agent Interruption Recovery

Agents also support interruption recovery mechanism:

```python
# Interruption triggered during Agent execution
# Checkpoint will automatically save Agent state

# When resuming execution, checkpoint will automatically restore Agent state
```

## Best Practices

### 1. Choose Appropriate Checkpoint Type

- **Development/Testing Environment**: Use `InMemoryCheckpointer`, simple and fast
- **Single-Machine Production Environment**: Use `PersistenceCheckpointer` with SQLite or Shelve
- **Distributed Production Environment**: Use `RedisCheckpointer`, supports cluster mode

### 2. Configure TTL (Redis Only)

For Redis checkpoints, it's recommended to configure TTL to avoid unlimited data growth:

```python
config = CheckpointerConfig(
    type="redis",
    conf={
        "connection": {"url": "redis://localhost:6379"},
        "ttl": {
            "default_ttl": 60,  # 60 minutes expiration
            "refresh_on_read": True  # Refresh on read, keep active sessions
        }
    }
)
```

### 3. Exception Handling

Checkpoints automatically save state when exceptions occur, but you need to ensure:

- State can be correctly restored after exceptions
- Regularly clean up expired or invalid state
- Monitor checkpoint storage usage

### 4. State Cleanup

Regularly clean up state that is no longer needed:

```python
# Release resources for specific session
await checkpointer.release("session_id")

# Release resources for specific Agent
await checkpointer.release("session_id", agent_id="agent_id")
```

## Troubleshooting

### Common Issues

1. **Provider Not Registered Error**
   - **Problem**: When using `CheckpointerFactory.create()`, provider not found error
   - **Cause**: Extended checkpointer (such as Redis) needs to import module first to register provider
   - **Solution**:

     ```python
     # For Redis checkpointer, need to import first
     from openjiuwen.extensions.checkpointer.redis import checkpointer  # noqa: F401
     
     # Then create
     config = CheckpointerConfig(type="redis", conf={...})
     checkpointer = await CheckpointerFactory.create(config)
     ```

   - **Note**: When using in Runner, Runner automatically handles import, no manual operation needed

2. **State Recovery Failure**
   - Check if checkpoint configuration is correct
   - Check if storage backend is running normally
   - Check if session ID is correct
   - Check if provider is correctly registered

3. **State Not Saved**
   - Check if checkpoint is correctly configured
   - Check if save method is called at correct execution point
   - Check if storage backend has write permissions
   - Check if provider is correctly registered

4. **State Conflict**
   - Ensure same session ID is not executed concurrently
   - Check if multiple checkpoint instances are operating on the same session

### Debugging Tips

```python
# Check if session exists
exists = await checkpointer.session_exists("session_id")
print(f"Session exists: {exists}")

# Get graph state store
graph_store = checkpointer.graph_store()
# Can further check graph state
```

## References

- [Checkpointer API Documentation](../API%20Docs/openjiuwen.core/session/checkpointer.md)
- [Redis Checkpointer API Documentation](../API%20Docs/openjiuwen.extensions/checkpointer/checkpointer.md)
- [Session State Management](./Session/State%20Management.md)
- [Session Interruption Recovery](./Session/Interruption%20Resumption.md)
