# openjiuwen.core.workflow.components.resource.knowledge_retrieval_comp

## class ComponentKBConfig

单个知识库在知识检索组件中的配置模型

**参数**：

* **kb_config**(KnowledgeBaseConfig)：知识库配置（知识库 ID、索引类型等）。
* **vector_store_config**(VectorStoreConfig)：向量存储配置。`store_provider` 决定后端（Milvus、ChromaDB、PGVector）。
* **embed_config**(EmbeddingConfig, 可选)：嵌入模型配置。当 `index_type` 为 `"vector"` 或 `"hybrid"` 时必填。默认值：None。
* **embed_additional_config**(Dict[str, Any])：嵌入模型构造时的额外关键字参数。默认值：{}。

## class KnowledgeRetrievalCompConfig

`KnowledgeRetrievalComponent` 的配置数据类，继承自 `ComponentConfig`。支持配置多个知识库，检索时对所有知识库执行检索并合并结果。

**参数**：

* **component_kb_configs**(List[ComponentKBConfig])：各知识库的配置列表。每个元素对应一个知识库（含 kb_config、vector_store_config、embed_config 等）。
* **vector_store_connection_config**(Dict[str, Any])：向量存储连接相关参数，创建向量存储时传入（如 `{"chroma_path": "/tmp/chroma"}` 或 `{"milvus_uri": "http://localhost:19530", "milvus_token": ""}`）。
* **retrieval_config**(RetrievalConfig)：检索配置（top_k、score_threshold、图检索/智能检索选项等）。
* **model_id**(str, 可选)：从 Runner 资源管理器获取 LLM 的模型 ID。默认值：None。
* **model_client_config**(ModelClientConfig, 可选)：用于智能检索场景的 LLM 客户端配置。默认值：None。
* **model_config**(ModelRequestConfig, 可选)：用于智能检索场景的 LLM 请求配置。默认值：None。

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
| `results` | List[str] | 检索到的文本字符串列表（来自所有配置的知识库合并结果）。 |
| `context` | str | 所有检索到的文本使用默认分隔符 `"\n\n"` 连接后的结果。 |