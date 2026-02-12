# openjiuwen.core.retrieval.common.config

> **参考示例**：更多使用示例请参考 [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) 仓库中 `examples/retrieval/` 目录下的示例代码，包括：
> - `chroma_query_expr.py` - ChromaDB 查询表达式示例（使用 VectorStoreConfig）
> - `milvus_query_expr.py` - Milvus 查询表达式示例（使用 VectorStoreConfig）
> - `configs.py` - 配置类使用示例（使用 EmbeddingConfig、RerankerConfig）

## class openjiuwen.core.retrieval.common.config.KnowledgeBaseConfig

知识库配置类，定义知识库的基本配置参数。

**参数**：

* **kb_id**(str)：知识库标识符。
* **index_type**(Literal["hybrid", "bm25", "vector"])：索引类型，hybrid=混合索引，bm25=BM25索引，vector=向量索引。默认值："hybrid"。
* **use_graph**(bool)：是否使用图索引。默认值：False。
* **chunk_size**(int)：分块大小。默认值：512。
* **chunk_overlap**(int)：分块重叠大小。默认值：50。

## class openjiuwen.core.retrieval.common.config.RetrievalConfig

检索配置类，定义检索相关的配置参数。

**参数**：

* **top_k**(int)：返回结果数量。默认值：5。
* **score_threshold**(float, 可选)：得分阈值，低于此阈值的结果将被过滤。默认值：None。
* **use_graph**(bool, 可选)：是否使用图检索（None时使用默认配置）。默认值：None。
* **agentic**(bool)：是否使用智能检索。默认值：False。
* **graph_expansion**(bool)：是否启用图扩展。默认值：False。
* **filters**(Dict[str, Any], 可选)：元数据过滤条件（比如 `{"category": "tech", "year": 2023}`）。默认值：None。

## class openjiuwen.core.retrieval.common.config.IndexConfig

索引配置类，定义索引相关的配置参数。

**参数**：

* **index_name**(str)：索引名称。
* **index_type**(Literal["hybrid", "bm25", "vector"])：索引类型。默认值："hybrid"。

## class openjiuwen.core.retrieval.common.config.VectorStoreConfig

向量存储配置类，定义向量存储相关的配置参数。

**参数**：

* **database_name**(str)：数据库名称。默认值：""。
* **collection_name**(str)：集合名称。
* **distance_metric**(Literal["cosine", "euclidean", "dot"])：距离度量方式，cosine=余弦距离，euclidean=欧氏距离，dot=点积。默认值："cosine"。

## class openjiuwen.core.retrieval.common.config.EmbeddingConfig

嵌入模型配置类，定义嵌入模型相关的配置参数。

**参数**：

* **model_name**(str)：模型名称。
* **base_url**(str)：API基础URL。
* **api_key**(str, 可选)：API密钥。默认值：None。

## class openjiuwen.core.retrieval.common.config.RerankerConfig

重排序器配置类，定义重排序器相关的配置参数。

**参数**：

* **api_key**(str)：API密钥。默认值：""。
* **api_base**(str)：API基础URL。
* **model_name**(str)：模型名称（可通过alias "model"访问）。默认值：""。
* **timeout**(float)：请求超时时间（秒）。默认值：10。
* **temperature**(float)：温度参数。默认值：0.95。
* **top_p**(float)：Top-p采样参数。默认值：0.1。
* **yes_no_ids**(tuple[int, int], 可选)："yes"和"no"的token ID（比如 `(123, 456)`）。默认值：None。
* **extra_body**(dict)：特殊关键字参数（比如 `{"custom_param": "value"}`）。默认值：{}。
