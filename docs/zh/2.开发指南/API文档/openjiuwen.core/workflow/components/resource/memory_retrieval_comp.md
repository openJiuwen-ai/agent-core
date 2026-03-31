# openjiuwen.core.workflow.components.resource.memory_retrieval_comp

## class MemoryRetrievalCompConfig

`MemoryRetrievalComponent` 的配置数据类，继承自 `ComponentConfig`。用于配置长期记忆检索的相关参数。

**参数**：

* **memory**(LongTermMemory)：长期记忆实例，用于执行记忆检索操作。
* **scope_id**(str)：记忆的作用域 ID，用于隔离不同场景下的记忆数据。默认值：`LongTermMemory.DEFAULT_VALUE`。
* **user_id**(str)：用户 ID，用于按用户隔离记忆数据。默认值：`LongTermMemory.DEFAULT_VALUE`。
* **threshold**(float)：检索相似度阈值，低于该阈值的结果将被过滤。默认值：`0.3`。

## class MemoryRetrievalComponent

用于长期记忆检索的可组合工作流组件。封装 `MemoryRetrievalExecutable` 以在工作流图中使用。根据查询字符串从长期记忆中检索片段记忆和历史摘要。

```python
MemoryRetrievalComponent(component_config: Optional[MemoryRetrievalCompConfig] = None)
```

**参数**：

* **component_config**(MemoryRetrievalCompConfig, 可选)：组件配置。

### 方法

#### add_component

```python
add_component(graph: Graph, node_id: str, wait_for_all: bool = False) -> None
```

将该组件作为节点添加到工作流图中。

#### to_executable

```python
to_executable() -> MemoryRetrievalExecutable
```

将可组合组件转换为其可执行的对应实例。

## 输入 / 输出

**输入** (`MemoryRetrievalInput`)：

| 字段 | 类型 | 说明 |
|------|------|------|
| `query` | str | 用于检索记忆的查询字符串，不能为空字符串。 |
| `top_k` | int | 返回的最大结果数量。默认值：`5`。 |

> **注意**：`query` 不能为空字符串或仅包含空白字符，否则将抛出参数校验错误。

**输出** (`MemoryRetrievalOutput`)：

| 字段 | 类型 | 说明 |
|------|------|------|
| `fragment_memory_results` | List[MemResult] | 检索到的片段记忆结果列表。 |
| `summary_results` | List[MemResult] | 检索到的历史摘要结果列表。 |
