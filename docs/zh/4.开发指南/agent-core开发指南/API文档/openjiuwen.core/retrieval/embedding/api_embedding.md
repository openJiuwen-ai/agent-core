# openjiuwen.core.retrieval.embedding.api_embedding

## class openjiuwen.core.retrieval.embedding.api_embedding.APIEmbedding

API嵌入模型实现。，支持多种API格式。


```python
APIEmbedding(config: EmbeddingConfig, timeout: int = 60, max_retries: int = 3, extra_headers: Optional[dict] = None, max_batch_size: int = 8)
```

初始化API嵌入模型。

**参数**：

* **config**(EmbeddingConfig)：嵌入模型配置。
* **timeout**(int)：请求超时时间（秒）。默认值：60。
* **max_retries**(int)：最大重试次数。默认值：3。
* **extra_headers**(dict, 可选)：额外的请求头。默认值：None。
* **max_batch_size**(int)：最大批处理大小。默认值：8。

**说明**：

支持的API响应格式：
* `{"embedding": [...]}`
* `{"embeddings": [...]}`
* `{"data": [{"embedding": [...]}, ...]}`

### property dimension

```python
dimension() -> int
```

返回嵌入向量的维度。

**返回**：

**int**，返回嵌入向量的维度。

### async embed_query

```python
embed_query(text: str, **kwargs: Any) -> List[float]
```

获取文本的嵌入向量（异步）。

**参数**：

* **text**(str)：查询文本。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[float]**，返回查询文本的嵌入向量。

**异常**：

* **ValueError**：如果文本为空。

### embed_query_sync

```python
embed_query_sync(text: str, **kwargs: Any) -> List[float]
```

获取文本的嵌入向量（同步）。

**参数**：

* **text**(str)：查询文本。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[float]**，返回查询文本的嵌入向量。

**异常**：

* **ValueError**：如果文本为空。

### async embed_documents

```python
embed_documents(texts: List[str], batch_size: Optional[int] = None, **kwargs: Any) -> List[List[float]]
```

获取文档列表的嵌入向量（异步）。

**参数**：

* **texts**(List[str])：文档文本列表。
* **batch_size**(int, 可选)：批处理大小。默认值：None（使用max_batch_size）。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[List[float]]**，返回每个文档文本的嵌入向量列表。

### embed_documents_sync

```python
embed_documents_sync(texts: List[str], batch_size: Optional[int] = None, **kwargs: Any) -> List[List[float]]
```

获取文档列表的嵌入向量（同步）。

**参数**：

* **texts**(List[str])：文档文本列表。
* **batch_size**(int, 可选)：批处理大小。默认值：None（使用max_batch_size）。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[List[float]]**，返回每个文档文本的嵌入向量列表。

