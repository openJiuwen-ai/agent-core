# openjiuwen.core.workflow.components.resource.memory_retrieval_comp

## class MemoryRetrievalCompConfig

Configuration dataclass for the `MemoryRetrievalComponent`. Extends `ComponentConfig`. Used to configure long-term memory retrieval parameters.

**Parameters**:

* **memory**(LongTermMemory): Long-term memory instance used to perform memory retrieval.
* **scope_id**(str): Scope ID for isolating memory data across different scenarios. Default: `LongTermMemory.DEFAULT_VALUE`.
* **user_id**(str): User ID for isolating memory data per user. Default: `LongTermMemory.DEFAULT_VALUE`.
* **threshold**(float): Similarity threshold; results below this value are filtered out. Default: `0.3`.

## class MemoryRetrievalComponent

Composable workflow component for long-term memory retrieval. Wraps `MemoryRetrievalExecutable` for use in workflow graphs. Retrieves fragment memories and history summaries from long-term memory based on a query string.

```python
MemoryRetrievalComponent(component_config: Optional[MemoryRetrievalCompConfig] = None)
```

**Parameters**:

* **component_config**(MemoryRetrievalCompConfig, optional): Component configuration.

### Methods

#### add_component

```python
add_component(graph: Graph, node_id: str, wait_for_all: bool = False) -> None
```

Add this component as a node to the workflow graph.

#### to_executable

```python
to_executable() -> MemoryRetrievalExecutable
```

Convert the composable component into its executable counterpart.

## Input / Output

**Input** (`MemoryRetrievalInput`):

| Field | Type | Description |
|-------|------|-------------|
| `query` | str | The query string to retrieve memories for. Must not be empty. |
| `top_k` | int | Maximum number of results to return. Default: `5`. |

> **Note**: `query` must not be an empty string or contain only whitespace, otherwise a parameter validation error will be raised.

**Output** (`MemoryRetrievalOutput`):

| Field | Type | Description |
|-------|------|-------------|
| `fragment_memory_results` | List[MemResult] | List of retrieved fragment memory results. |
| `summary_results` | List[MemResult] | List of retrieved history summary results. |
