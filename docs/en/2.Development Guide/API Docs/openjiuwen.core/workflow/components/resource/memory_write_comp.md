# openjiuwen.core.workflow.components.resource.memory_write_comp

## class MemoryWriteCompConfig

Configuration dataclass for the `MemoryWriteComponent`. Extends `ComponentConfig`. Used to configure long-term memory write parameters.

**Parameters**:

* **memory**(LongTermMemory): Long-term memory instance used to perform memory write operations.
* **scope_id**(str): Scope ID for isolating memory data across different scenarios. Default: `LongTermMemory.DEFAULT_VALUE`.
* **user_id**(str): User ID for isolating memory data per user. Default: `LongTermMemory.DEFAULT_VALUE`.
* **session_id**(str): Session ID for associating with the current session. Default: `LongTermMemory.DEFAULT_VALUE`.
* **agent_config**(AgentMemoryConfig): Agent memory configuration that controls memory generation behavior. Default: `AgentMemoryConfig()`.
* **gen_mem**(bool): Whether to automatically generate memory fragments. Default: `True`.
* **gen_mem_with_history_msg_num**(int): Number of historical messages to reference when generating memories. Default: `2`.

## class MemoryWriteComponent

Composable workflow component for long-term memory write. Wraps `MemoryWriteExecutable` for use in workflow graphs. Writes conversation messages to long-term memory.

```python
MemoryWriteComponent(component_config: Optional[MemoryWriteCompConfig] = None)
```

**Parameters**:

* **component_config**(MemoryWriteCompConfig, optional): Component configuration.

### Methods

#### add_component

```python
add_component(graph: Graph, node_id: str, wait_for_all: bool = False) -> None
```

Add this component as a node to the workflow graph.

#### to_executable

```python
to_executable() -> MemoryWriteExecutable
```

Convert the composable component into its executable counterpart.

## Input / Output

**Input** (`MemoryWriteInput`):

| Field | Type | Description |
|-------|------|-------------|
| `messages` | List[BaseMessage] | List of messages to write to long-term memory. Must not be empty. |
| `timestamp` | datetime, optional | Timestamp for the messages. Default: `None` (uses current time). |

> **Note**: The `messages` list must not be empty, otherwise a parameter validation error will be raised.

**Output** (`MemoryWriteOutput`):

| Field | Type | Description |
|-------|------|-------------|
| `success` | bool | Whether the write operation succeeded. Default: `True`. |
