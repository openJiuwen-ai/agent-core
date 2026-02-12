# openjiuwen.core.session.checkpointer

## class openjiuwen.core.session.checkpointer.Checkpointer

```python
class openjiuwen.core.session.checkpointer.Checkpointer
```

检查点（Checkpointer）抽象基类，用于管理 Agent 和工作流的状态持久化和恢复。Checkpointer 负责在 Agent 和工作流执行的关键节点保存和恢复状态，支持中断恢复、异常恢复等功能。

### staticmethod get_thread_id

```python
staticmethod get_thread_id(session: BaseSession) -> str
```

获取会话的线程 ID，格式为 `session_id:workflow_id`。

**参数**：

- **session**(BaseSession)：会话对象。

**返回**：

**str**：线程 ID 字符串。

### abstractmethod pre_workflow_execute

```python
abstractmethod async pre_workflow_execute(session: BaseSession, inputs: InteractiveInput)
```

在工作流执行前调用，用于恢复或清理工作流状态。

**参数**：

- **session**(BaseSession)：工作流会话对象。
- **inputs**(InteractiveInput)：工作流输入。如果为 `InteractiveInput` 类型，则恢复工作流状态；否则检查是否存在状态，如果存在且未启用强制删除，则抛出异常。

### abstractmethod post_workflow_execute

```python
abstractmethod async post_workflow_execute(session: BaseSession, result, exception)
```

在工作流执行后调用，用于保存或清理工作流状态。

**参数**：

- **session**(BaseSession)：工作流会话对象。
- **result**：工作流执行结果。
- **exception**：如果工作流执行过程中发生异常，则传入异常对象；否则为 `None`。

### abstractmethod pre_agent_execute

```python
abstractmethod async pre_agent_execute(session: BaseSession, inputs)
```

在 Agent 执行前调用，用于恢复 Agent 状态。

**参数**：

- **session**(BaseSession)：Agent 会话对象。
- **inputs**：Agent 输入。如果提供，会将其添加到会话状态中。

### abstractmethod interrupt_agent_execute

```python
abstractmethod async interrupt_agent_execute(session: BaseSession)
```

当 Agent 需要中断等待用户交互时调用，用于保存 Agent 状态。

**参数**：

- **session**(BaseSession)：Agent 会话对象。

### abstractmethod post_agent_execute

```python
abstractmethod async post_agent_execute(session: BaseSession)
```

在 Agent 执行完成后调用，用于保存 Agent 状态。

**参数**：

- **session**(BaseSession)：Agent 会话对象。

### abstractmethod session_exists

```python
abstractmethod async session_exists(session_id: str) -> bool
```

检查指定会话 ID 是否存在。

**参数**：

- **session_id**(str)：会话 ID。

**返回**：

**bool**：如果会话存在则返回 `True`，否则返回 `False`。

### abstractmethod release

```python
abstractmethod async release(session_id: str, agent_id: str = None)
```

释放指定会话的资源。如果提供了 `agent_id`，则只释放该 Agent 的资源；否则释放整个会话的所有资源。

**参数**：

- **session_id**(str)：会话 ID。
- **agent_id**(str, 可选)：Agent ID。默认值：`None`。

### abstractmethod graph_store

```python
abstractmethod graph_store() -> Store
```

获取图状态存储对象。

**返回**：

**Store**：图状态存储对象。

## class openjiuwen.core.session.checkpointer.Storage

```python
class openjiuwen.core.session.checkpointer.Storage
```

存储抽象基类，用于保存和恢复会话状态。

### abstractmethod save

```python
abstractmethod async save(session: BaseSession)
```

保存会话状态。

**参数**：

- **session**(BaseSession)：会话对象。

### abstractmethod recover

```python
abstractmethod async recover(session: BaseSession, inputs: InteractiveInput = None)
```

恢复会话状态。

**参数**：

- **session**(BaseSession)：会话对象。
- **inputs**(InteractiveInput, 可选)：交互输入。默认值：`None`。

### abstractmethod clear

```python
abstractmethod async clear(session_id: str)
```

清除指定会话的状态。

**参数**：

- **session_id**(str)：会话 ID。

### abstractmethod exists

```python
abstractmethod async exists(session: BaseSession) -> bool
```

检查会话状态是否存在。

**参数**：

- **session**(BaseSession)：会话对象。

**返回**：

**bool**：如果状态存在则返回 `True`，否则返回 `False`。

## class openjiuwen.core.session.checkpointer.InMemoryCheckpointer

```python
class openjiuwen.core.session.checkpointer.InMemoryCheckpointer()
```

基于内存的检查点实现，所有状态保存在内存中，进程重启后状态会丢失。适用于开发和测试场景。

**样例**：

```python
>>> from openjiuwen.core.session.checkpointer import InMemoryCheckpointer
>>> 
>>> checkpointer = InMemoryCheckpointer()
>>> # 使用 checkpointer 进行状态管理
```

## class openjiuwen.core.session.checkpointer.PersistenceCheckpointer

```python
class openjiuwen.core.session.checkpointer.PersistenceCheckpointer(kv_store: BaseKVStore)
```

基于持久化存储的检查点实现，使用 `BaseKVStore` 接口进行状态持久化，支持任何实现了 `BaseKVStore` 的存储后端（如 SQLite、Shelve 等）。

**参数**：

- **kv_store**(BaseKVStore)：键值存储对象，用于持久化状态。

**样例**：

```python
>>> from openjiuwen.core.session.checkpointer import PersistenceCheckpointer
>>> from openjiuwen.core.foundation.store.kv import ShelveStore
>>> 
>>> kv_store = ShelveStore("checkpoint.db")
>>> checkpointer = PersistenceCheckpointer(kv_store)
>>> # 使用 checkpointer 进行状态管理
```

## class openjiuwen.core.session.checkpointer.CheckpointerFactory

```python
class openjiuwen.core.session.checkpointer.CheckpointerFactory
```

检查点工厂类，用于创建和管理不同类型的检查点实例。

### classmethod register

```python
classmethod register(name: str)
```

注册检查点提供者。

**参数**：

- **name**(str)：检查点类型名称（如 `"in_memory"`、`"persistence"`、`"redis"`）。

**返回**：

装饰器函数，用于装饰 `CheckpointerProvider` 类。

**样例**：

```python
>>> from openjiuwen.core.session.checkpointer import CheckpointerFactory, CheckpointerProvider
>>> 
>>> @CheckpointerFactory.register("custom")
>>> class CustomCheckpointerProvider(CheckpointerProvider):
...     async def create(self, conf: dict) -> Checkpointer:
...         # 创建自定义检查点实例
...         return CustomCheckpointer()
```

### classmethod create

```python
classmethod async create(checkpointer_conf: CheckpointerConfig) -> Checkpointer
```

根据配置创建检查点实例。

**参数**：

- **checkpointer_conf**(CheckpointerConfig)：检查点配置对象，包含 `type` 和 `conf` 字段。

**返回**：

**Checkpointer**：检查点实例。

**样例**：

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

设置默认检查点实例。

**参数**：

- **checkpointer**(Checkpointer)：检查点实例。

### classmethod set_checkpointer

```python
classmethod set_checkpointer(store_type: str, checkpointer: Checkpointer)
```

为指定类型设置检查点实例。

**参数**：

- **store_type**(str)：存储类型（如 `"in_memory"`、`"redis"`）。
- **checkpointer**(Checkpointer)：检查点实例。

### classmethod get_checkpointer

```python
classmethod get_checkpointer(store_type: Optional[str] = None) -> Checkpointer
```

获取检查点实例。

**参数**：

- **store_type**(Optional[str])：存储类型。如果提供：
  - 首先检查是否通过 `set_checkpointer` 为该类型设置了实例。
  - 如果类型为 `"in_memory"` 且未设置实例，返回默认的内存检查点。
  - 否则返回通过 `set_default_checkpointer` 设置的默认检查点。
  如果未提供，返回默认检查点。

**返回**：

**Checkpointer**：检查点实例。

## class openjiuwen.core.session.checkpointer.CheckpointerProvider

```python
class openjiuwen.core.session.checkpointer.CheckpointerProvider
```

检查点提供者抽象基类，用于创建特定类型的检查点实例。

### abstractmethod create

```python
abstractmethod async create(conf: dict) -> Checkpointer
```

根据配置创建检查点实例。

**参数**：

- **conf**(dict)：配置字典。

**返回**：

**Checkpointer**：检查点实例。

## class openjiuwen.core.session.checkpointer.CheckpointerConfig

```python
class openjiuwen.core.session.checkpointer.CheckpointerConfig(type: str = "in_memory", conf: dict = {})
```

检查点配置类。

**参数**：

- **type**(str, 可选)：检查点类型。默认值：`"in_memory"`。
- **conf**(dict, 可选)：检查点配置字典。默认值：`{}`。

## func openjiuwen.core.session.checkpointer.build_key

```python
func build_key(*parts: str) -> str
```

使用冒号分隔符连接多个字符串部分构建键。

**参数**：

- ***parts**(str)：可变数量的字符串部分。

**返回**：

**str**：使用 `:` 连接后的键字符串。

**样例**：

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

构建带命名空间的键，格式为 `session:namespace:entity_id:suffixes`。

**参数**：

- **session_id**(str)：会话标识符。
- **namespace**(str)：命名空间（如 `"agent"`、`"workflow"`）。
- **entity_id**(str)：实体标识符（如 agent_id、workflow_id）。
- ***suffixes**(str)：额外的键后缀。

**返回**：

**str**：键字符串。

**样例**：

```python
>>> from openjiuwen.core.session.checkpointer import build_key_with_namespace
>>> 
>>> key = build_key_with_namespace("session1", "agent", "agent1", "state")
>>> print(key)
session1:agent:agent1:state
```

## 常量

### SESSION_NAMESPACE_AGENT

```python
SESSION_NAMESPACE_AGENT = "agent"
```

Agent 状态在会话下的命名空间。

### SESSION_NAMESPACE_WORKFLOW

```python
SESSION_NAMESPACE_WORKFLOW = "workflow"
```

工作流状态在会话下的命名空间（工作流自身状态）。

### WORKFLOW_NAMESPACE_GRAPH

```python
WORKFLOW_NAMESPACE_GRAPH = "workflow-graph"
```

图状态在工作流下的命名空间（与工作流自身状态分离）。
