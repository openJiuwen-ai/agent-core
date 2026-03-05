# openjiuwen.core.workflow.components.resource.knowledge_retrieval_comp

> **参考示例**：更多使用示例请参考 [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) 仓库中 `examples/retrieval/` 目录下的示例代码，包括：
> - `retrieval_workflow_demo_chroma.py` — 使用 ChromaDB 的 RAG 工作流示例
> - `retrieval_workflow_demo_milvus.py` — 使用 Milvus 的 RAG 工作流示例（支持图索引和智能检索）

## class KnowledgeRetrievalCompConfig

`KnowledgeRetrievalComponent` 的配置数据类，继承自 `ComponentConfig`。

**参数**：

* **kb_configs**(List[KnowledgeBaseConfig])：知识库配置列表。每个条目定义一个用于检索的知识库。
* **retrieval_config**(RetrievalConfig)：检索配置（top_k、score_threshold、图检索/智能检索选项等）。
* **vector_store_config**(VectorStoreConfig)：向量存储配置。`store_provider` 字段决定使用哪个后端（Milvus、ChromaDB、PGVector）。
* **vector_store_additional_config**(Dict[str, Any])：传递给向量存储构造函数的额外关键字参数（如 `{"milvus_uri": "http://localhost:19530"}` 或 `{"chroma_path": "/tmp/chroma"}`）。
* **embed_config**(EmbeddingConfig, 可选)：嵌入模型配置。当 `index_type` 为 `"vector"` 或 `"hybrid"` 时必填。默认值：None。
* **model_id**(str, 可选)：从 Runner 资源管理器获取 LLM 的模型 ID。默认值：None。
* **model_client_config**(ModelClientConfig, 可选)：用于智能检索场景的 LLM 客户端配置。默认值：None。
* **model_config**(ModelRequestConfig, 可选)：用于智能检索场景的 LLM 请求配置。默认值：None。
* **result_separator**(str)：将检索到的文本连接成 `context` 输出时使用的分隔符。默认值：`"\n\n"`。
* **include_metadata**(bool)：是否在输出中包含 `results_with_metadata`。默认值：False。

## class KnowledgeRetrievalComponent

用于知识检索的可组合工作流组件。封装 `KnowledgeRetrievalExecutable` 以在工作流图中使用。

```python
KnowledgeRetrievalComponent(component_config: Optional[KnowledgeRetrievalCompConfig] = None)
```

**参数**：

* **component_config**(KnowledgeRetrievalCompConfig, 可选)：组件配置。

### 方法

#### add_component

```python
add_component(graph: Graph, node_id: str, wait_for_all: bool = False) -> None
```

将该组件作为节点添加到工作流图中。

#### to_executable

```python
to_executable() -> KnowledgeRetrievalExecutable
```

将可组合组件转换为其可执行的对应实例。

## 输入 / 输出

**输入** (`KnowledgeRetrievalInput`)：

| 字段 | 类型 | 说明 |
|------|------|------|
| `query` | str | 用于检索文档的查询字符串。 |

> **Query 重写**：未启用智能检索时，本组件不会对 query 进行重写。多轮对话场景下，可先使用 [QueryRewriter](../../../retrieval/query_rewriter/query_rewriter.md) 对用户消息进行重写，将返回的 `standalone_query` 作为 `query` 传入。详见 openJiuwen [知识检索](../../../../../../高阶用法/知识检索.md) 文档。

**输出**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `results` | List[str] | 检索到的文本字符串列表。 |
| `context` | str | 所有检索到的文本通过 `result_separator` 连接后的结果。 |
| `results_with_metadata` | List[dict]（可选） | 仅当 `include_metadata=True` 时存在。每个条目是序列化的 `MultiKBRetrievalResult`。 |