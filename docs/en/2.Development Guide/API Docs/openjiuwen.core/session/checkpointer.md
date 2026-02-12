# openjiuwen.core.session.checkpointer

## class openjiuwen.core.session.checkpointer.Checkpointer

```python
class openjiuwen.core.session.checkpointer.Checkpointer
```

Abstract base class for checkpoints (Checkpointer), used to manage state persistence and recovery for Agents and workflows. Checkpointer is responsible for saving and restoring state at key execution points of Agents and workflows, supporting features such as interruption recovery and exception recovery.

### staticmethod get_thread_id

```python
staticmethod get_thread_id(session: BaseSession) -> str
```

Get the thread ID of the session, formatted as `session_id:workflow_id`.

**Parameters**:

- **session**(BaseSession): Session object.

**Returns**:

**str**: Thread ID string.

### abstractmethod pre_workflow_execute

```python
abstractmethod async pre_workflow_execute(session: BaseSession, inputs: InteractiveInput)
```

Called before workflow execution to restore or clear workflow state.

**Parameters**:

- **session**(BaseSession): Workflow session object.
- **inputs**(InteractiveInput): Workflow input. If it is of type `InteractiveInput`, restore workflow state; otherwise, check if state exists, and if it exists and forced deletion is not enabled, raise an exception.

### abstractmethod post_workflow_execute

```python
abstractmethod async post_workflow_execute(session: BaseSession, result, exception)
```

Called after workflow execution to save or clear workflow state.

**Parameters**:

- **session**(BaseSession): Workflow session object.
- **result**: Workflow execution result.
- **exception**: If an exception occurred during workflow execution, pass the exception object; otherwise `None`.

### abstractmethod pre_agent_execute

```python
abstractmethod async pre_agent_execute(session: BaseSession, inputs)
```

Called before Agent execution to restore Agent state.

**Parameters**:

- **session**(BaseSession): Agent session object.
- **inputs**: Agent input. If provided, it will be added to the session state.

### abstractmethod interrupt_agent_execute

```python
abstractmethod async interrupt_agent_execute(session: BaseSession)
```

Called when the Agent needs to interrupt and wait for user interaction to save Agent state.

**Parameters**:

- **session**(BaseSession): Agent session object.

### abstractmethod post_agent_execute

```python
abstractmethod async post_agent_execute(session: BaseSession)
```

Called after Agent execution completes to save Agent state.

**Parameters**:

- **session**(BaseSession): Agent session object.

### abstractmethod session_exists

```python
abstractmethod async session_exists(session_id: str) -> bool
```

Check if the specified session ID exists.

**Parameters**:

- **session_id**(str): Session ID.

**Returns**:

**bool**: Returns `True` if the session exists, otherwise `False`.

### abstractmethod release

```python
abstractmethod async release(session_id: str, agent_id: str = None)
```

Release resources for the specified session. If `agent_id` is provided, only release resources for that Agent; otherwise release all resources for the entire session.

**Parameters**:

- **session_id**(str): Session ID.
- **agent_id**(str, optional): Agent ID. Default: `None`.

### abstractmethod graph_store

```python
abstractmethod graph_store() -> Store
```

Get the graph state store object.

**Returns**:

**Store**: Graph state store object.

## class openjiuwen.core.session.checkpointer.Storage

```python
class openjiuwen.core.session.checkpointer.Storage
```

Abstract base class for storage, used to save and restore session state.

### abstractmethod save

```python
abstractmethod async save(session: BaseSession)
```

Save session state.

**Parameters**:

- **session**(BaseSession): Session object.

### abstractmethod recover

```python
abstractmethod async recover(session: BaseSession, inputs: InteractiveInput = None)
```

Recover session state.

**Parameters**:

- **session**(BaseSession): Session object.
- **inputs**(InteractiveInput, optional): Interactive input. Default: `None`.

### abstractmethod clear

```python
abstractmethod async clear(session_id: str)
```

Clear state for the specified session.

**Parameters**:

- **session_id**(str): Session ID.

### abstractmethod exists

```python
abstractmethod async exists(session: BaseSession) -> bool
```

Check if session state exists.

**Parameters**:

- **session**(BaseSession): Session object.

**Returns**:

**bool**: Returns `True` if state exists, otherwise `False`.

## class openjiuwen.core.session.checkpointer.InMemoryCheckpointer

```python
class openjiuwen.core.session.checkpointer.InMemoryCheckpointer()
```

In-memory checkpoint implementation where all state is saved in memory and lost after process restart. Suitable for development and testing scenarios.

**Example**:

```python
>>> from openjiuwen.core.session.checkpointer import InMemoryCheckpointer
>>> 
>>> checkpointer = InMemoryCheckpointer()
>>> # Use checkpointer for state management
```

## class openjiuwen.core.session.checkpointer.PersistenceCheckpointer

```python
class openjiuwen.core.session.checkpointer.PersistenceCheckpointer(kv_store: BaseKVStore)
```

Persistence-based checkpoint implementation using the `BaseKVStore` interface for state persistence, supporting any storage backend that implements `BaseKVStore` (such as SQLite, Shelve, etc.).

**Parameters**:

- **kv_store**(BaseKVStore): Key-value store object for persisting state.

**Example**:

```python
>>> from openjiuwen.core.session.checkpointer import PersistenceCheckpointer
>>> from openjiuwen.core.foundation.store.kv import ShelveStore
>>> 
>>> kv_store = ShelveStore("checkpoint.db")
>>> checkpointer = PersistenceCheckpointer(kv_store)
>>> # Use checkpointer for state management
```

## class openjiuwen.core.session.checkpointer.CheckpointerFactory

```python
class openjiuwen.core.session.checkpointer.CheckpointerFactory
```

Checkpoint factory class for creating and managing different types of checkpoint instances.

### classmethod register

```python
classmethod register(name: str)
```

Register a checkpoint provider.

**Parameters**:

- **name**(str): Checkpoint type name (e.g., `"in_memory"`, `"persistence"`, `"redis"`).

**Returns**:

Decorator function for decorating `CheckpointerProvider` classes.

**Example**:

```python
>>> from openjiuwen.core.session.checkpointer import CheckpointerFactory, CheckpointerProvider
>>> 
>>> @CheckpointerFactory.register("custom")
>>> class CustomCheckpointerProvider(CheckpointerProvider):
...     async def create(self, conf: dict) -> Checkpointer:
...         # Create custom checkpoint instance
...         return CustomCheckpointer()
```

### classmethod create

```python
classmethod async create(checkpointer_conf: CheckpointerConfig) -> Checkpointer
```

Create a checkpoint instance based on configuration.

**Parameters**:

- **checkpointer_conf**(CheckpointerConfig): Checkpoint configuration object containing `type` and `conf` fields.

**Returns**:

**Checkpointer**: Checkpoint instance.

**Example**:

```python
>>> from openjiuwen.core.session.checkpointer import CheckpointerFactory, CheckpointerConfig
>>> 
>>> config = CheckpointerConfig(type="in_memory", conf={})
>>> checkpointer = await CheckpointerFactory.create(config)
```

### classmethod set_default_checkpointer

```python
classmethod set_default_checkpointer(checkpointer: Checkpointer)
```

Set the default checkpoint instance.

**Parameters**:

- **checkpointer**(Checkpointer): Checkpoint instance.

### classmethod set_checkpointer

```python
classmethod set_checkpointer(store_type: str, checkpointer: Checkpointer)
```

Set a checkpoint instance for a specific type.

**Parameters**:

- **store_type**(str): Storage type (e.g., `"in_memory"`, `"redis"`).
- **checkpointer**(Checkpointer): Checkpoint instance.

### classmethod get_checkpointer

```python
classmethod get_checkpointer(store_type: Optional[str] = None) -> Checkpointer
```

Get checkpoint instance.

**Parameters**:

- **store_type**(Optional[str]): Storage type. If provided:
  - First checks if an instance was set for this type via `set_checkpointer`.
  - If type is `"in_memory"` and no instance was set, returns the default in-memory checkpointer.
  - Otherwise, returns the default checkpointer set via `set_default_checkpointer`.
  If not provided, returns the default checkpointer.

**Returns**:

**Checkpointer**: Checkpoint instance.

## class openjiuwen.core.session.checkpointer.CheckpointerProvider

```python
class openjiuwen.core.session.checkpointer.CheckpointerProvider
```

Abstract base class for checkpoint providers, used to create checkpoint instances of specific types.

### abstractmethod create

```python
abstractmethod async create(conf: dict) -> Checkpointer
```

Create a checkpoint instance based on configuration.

**Parameters**:

- **conf**(dict): Configuration dictionary.

**Returns**:

**Checkpointer**: Checkpoint instance.

## class openjiuwen.core.session.checkpointer.CheckpointerConfig

```python
class openjiuwen.core.session.checkpointer.CheckpointerConfig(type: str = "in_memory", conf: dict = {})
```

Checkpoint configuration class.

**Parameters**:

- **type**(str, optional): Checkpoint type. Default: `"in_memory"`.
- **conf**(dict, optional): Checkpoint configuration dictionary. Default: `{}`.

## func openjiuwen.core.session.checkpointer.build_key

```python
func build_key(*parts: str) -> str
```

Build a key by joining multiple string parts with colon separator.

**Parameters**:

- ***parts**(str): Variable number of string parts.

**Returns**:

**str**: Key string joined with `:`.

**Example**:

```python
>>> from openjiuwen.core.session.checkpointer import build_key
>>> 
>>> key = build_key("session1", "agent", "agent1")
>>> print(key)
session1:agent:agent1
```

## func openjiuwen.core.session.checkpointer.build_key_with_namespace

```python
func build_key_with_namespace(session_id: str, namespace: str, entity_id: str, *suffixes: str) -> str
```

Build a key with namespace structure, formatted as `session:namespace:entity_id:suffixes`.

**Parameters**:

- **session_id**(str): Session identifier.
- **namespace**(str): Namespace (e.g., `"agent"`, `"workflow"`).
- **entity_id**(str): Entity identifier (e.g., agent_id, workflow_id).
- ***suffixes**(str): Additional key suffixes.

**Returns**:

**str**: Key string.

**Example**:

```python
>>> from openjiuwen.core.session.checkpointer import build_key_with_namespace
>>> 
>>> key = build_key_with_namespace("session1", "agent", "agent1", "state")
>>> print(key)
session1:agent:agent1:state
```

## Constants

### SESSION_NAMESPACE_AGENT

```python
SESSION_NAMESPACE_AGENT = "agent"
```

Namespace for Agent state under session.

### SESSION_NAMESPACE_WORKFLOW

```python
SESSION_NAMESPACE_WORKFLOW = "workflow"
```

Namespace for workflow state under session (workflow's own state).

### WORKFLOW_NAMESPACE_GRAPH

```python
WORKFLOW_NAMESPACE_GRAPH = "workflow-graph"
```

Namespace for graph state under workflow (separated from workflow's own state).
