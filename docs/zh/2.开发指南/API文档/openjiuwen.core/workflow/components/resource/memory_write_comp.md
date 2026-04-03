# openjiuwen.core.workflow.components.resource.memory_write_comp

## class MemoryWriteCompConfig

`MemoryWriteComponent` 的配置数据类，继承自 `ComponentConfig`。用于配置长期记忆写入的相关参数。

**参数**：

* **memory**(LongTermMemory)：长期记忆实例，用于执行记忆写入操作。
* **scope_id**(str)：记忆的作用域 ID，用于隔离不同场景下的记忆数据。默认值：`LongTermMemory.DEFAULT_VALUE`。
* **user_id**(str)：用户 ID，用于按用户隔离记忆数据。默认值：`LongTermMemory.DEFAULT_VALUE`。
* **session_id**(str)：会话 ID，用于关联当前会话。默认值：`LongTermMemory.DEFAULT_VALUE`。
* **agent_config**(AgentMemoryConfig)：智能体记忆配置，控制记忆生成的行为。默认值：`AgentMemoryConfig()`。
* **gen_mem**(bool)：是否自动生成记忆片段。默认值：`True`。
* **gen_mem_with_history_msg_num**(int)：生成记忆时参考的历史消息数量。默认值：`2`。

## class MemoryWriteComponent

用于长期记忆写入的可组合工作流组件。封装 `MemoryWriteExecutable` 以在工作流图中使用。将对话消息写入长期记忆中。

```python
MemoryWriteComponent(component_config: Optional[MemoryWriteCompConfig] = None)
```

**参数**：

* **component_config**(MemoryWriteCompConfig, 可选)：组件配置。

### 方法

#### add_component

```python
add_component(graph: Graph, node_id: str, wait_for_all: bool = False) -> None
```

将该组件作为节点添加到工作流图中。

#### to_executable

```python
to_executable() -> MemoryWriteExecutable
```

将可组合组件转换为其可执行的对应实例。

## 输入 / 输出

**输入** (`MemoryWriteInput`)：

| 字段 | 类型 | 说明 |
|------|------|------|
| `messages` | List[BaseMessage] | 待写入长期记忆的消息列表，不能为空。 |
| `timestamp` | datetime, 可选 | 消息的时间戳。默认值：`None`（使用当前时间）。 |

> **注意**：`messages` 列表不能为空，否则将抛出参数校验错误。

**输出** (`MemoryWriteOutput`)：

| 字段 | 类型 | 说明 |
|------|------|------|
| `success` | bool | 写入是否成功。默认值：`True`。 |
