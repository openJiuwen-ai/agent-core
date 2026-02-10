# openjiuwen.core.retrieval.embedding.vllm_embedding

## class openjiuwen.core.retrieval.embedding.vllm_embedding.VLLMEmbedding

vLLM嵌入模型实现，支持类似vLLM服务的多模态嵌入模型（如Qwen3-VL-Embedding）。

```python
VLLMEmbedding(config: EmbeddingConfig, timeout: int = 60, max_retries: int = 3, extra_headers: Optional[dict] = None, max_batch_size: int = 8, max_concurrent: int = 50, dimension: Optional[int] = None, verify: bool | str | ssl.SSLContext = True, **kwargs)
```

初始化vLLM嵌入模型。

**参数**：

* **config**(EmbeddingConfig)：嵌入模型配置。
* **timeout**(int)：请求超时时间（秒）。默认值：60。
* **max_retries**(int)：最大重试次数。默认值：3。
* **extra_headers**(dict, 可选)：额外的请求头。默认值：None。
* **max_batch_size**(int)：最大批处理大小。默认值：8。
* **max_concurrent**(int)：最大并发请求数。默认值：50。
* **dimension**(int, 可选)：嵌入向量维度（用于Matryoshka模型）。默认值：None。
* **verify**(bool | str | ssl.SSLContext)：SSL验证设置。默认值：True。
* **kwargs**：可变参数，用于传递其他额外的配置参数。

**说明**：

VLLMEmbedding继承自OpenAIEmbedding，支持多模态文档嵌入。

### property dimension

```python
dimension -> int
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

### async embed_multimodal

```python
embed_multimodal(doc: MultimodalDocument, **kwargs) -> List[float]
```

嵌入多模态文档（异步）。

**参数**：

* **doc**(MultimodalDocument)：多模态文档。
* **kwargs**：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[float]**，返回多模态文档的嵌入向量。

### embed_multimodal_sync

```python
embed_multimodal_sync(doc: MultimodalDocument, **kwargs) -> List[float]
```

嵌入多模态文档（同步）。

**参数**：

* **doc**(MultimodalDocument)：多模态文档。
* **kwargs**：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[float]**，返回多模态文档的嵌入向量。

> **参考示例**：更多使用示例请参考 [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) 仓库中 `examples/retrieval/` 目录下的示例代码，包括：
> - `showcase_text_embedding.py` - 文本嵌入示例
> - `showcase_multimodal_embedding.py` - 多模态嵌入示例
