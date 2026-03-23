# openjiuwen.core.retrieval.embedding.dashscope_embedding

## class openjiuwen.core.retrieval.embedding.dashscope_embedding.DashscopeEmbedding

基于阿里云 DashScope（`dashscope` SDK）的多模态嵌入客户端，支持文本、图片、视频等输入，调用百炼多模态向量 API。接口说明可参考阿里云文档：[多模态向量 API 参考](https://help.aliyun.com/zh/model-studio/multimodal-embedding-api-reference)（国际站：[Multimodal embedding API reference](https://www.alibabacloud.com/help/en/model-studio/multimodal-embedding-api-reference)）。

```python
DashscopeEmbedding(config: EmbeddingConfig, timeout: int = 60, max_retries: int = 3, extra_headers: Optional[dict] = None, max_batch_size: int = 8, max_concurrent: int = 50, dimension: Optional[int] = None, verify: bool | str | ssl.SSLContext = True, **kwargs)
```

初始化 DashScope 多模态嵌入模型。

**参数**：

* **config**(EmbeddingConfig)：嵌入模型配置，需设置 `model_name`、`base_url`（例如 `https://dashscope.aliyuncs.com/api/v1/`）、`api_key`。
* **timeout**(int)：请求超时时间（秒）。默认值：60。
* **max_retries**(int)：最大重试次数。默认值：3。
* **extra_headers**(dict, 可选)：额外的请求头。默认值：None。
* **max_batch_size**(int)：单次请求的最大批大小（与 DashScope 多模态接口限制一致，不宜过大）。默认值：8。
* **max_concurrent**(int)：最大并发请求数。默认值：50。
* **dimension**(int, 可选)：输出向量维度，用于 Matryoshka 等可裁剪维度的模型。默认值：None（由首次响应推断维度）。
* **verify**(bool | str | ssl.SSLContext)：HTTPS 校验；`bool` 表示是否使用默认 CA；`str` 为自定义 CA 证书路径；`ssl.SSLContext` 为自定义 SSL 上下文。默认值：True。
* **kwargs**：透传给底层 HTTP 客户端的额外关键字参数。

**说明**：

* 继承自 `APIEmbedding`，通过 `dashscope.MultiModalEmbedding`（同步）与 `dashscope.AioMultiModalEmbedding`（异步）调用服务。
* `embed_documents` / `embed_query` 既可传入纯文本 `str`，也可传入 [MultimodalDocument](../common/document.md)；多模态条目会使用 `MultimodalDocument.dashscope_input` 作为请求体中的 `input` 元素。
* 实际批大小取调用方传入的 `batch_size` 与构造时的 `max_batch_size` 的较小值。
* 若在 `embed_documents` / `embed_documents_sync` 的 `kwargs` 中传入 `callback_cls`，将用于各批完成时的回调（默认行为与基类一致）。

### property dimension

```python
dimension -> int
```

返回嵌入向量的维度。

**返回**：

**int**，返回嵌入向量的维度。

### async embed_query

```python
embed_query(text: str | MultimodalDocument, **kwargs: Any) -> List[float]
```

获取单条查询的嵌入向量（异步）。

**参数**：

* **text**(str | MultimodalDocument)：查询文本，或多模态文档（见 [MultimodalDocument](../common/document.md)）。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[float]**，返回查询的嵌入向量。

### embed_query_sync

```python
embed_query_sync(text: str | MultimodalDocument, **kwargs: Any) -> List[float]
```

获取单条查询的嵌入向量（同步）。

**参数**：

* **text**(str | MultimodalDocument)：查询文本，或多模态文档。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[float]**，返回查询的嵌入向量。

### async embed_documents

```python
embed_documents(texts: List[str | MultimodalDocument], batch_size: Optional[int] = None, **kwargs: Any) -> List[List[float]]
```

批量获取嵌入向量（异步）。

**参数**：

* **texts**(List[str | MultimodalDocument])：文本或多模态文档列表。
* **batch_size**(int, 可选)：批处理大小。默认值：None（使用 `max_batch_size`）。
* **kwargs**(Any)：可变参数；可包含 `callback_cls` 等。

**返回**：

**List[List[float]]**，与 `texts` 顺序一致的嵌入向量列表。

### embed_documents_sync

```python
embed_documents_sync(texts: List[str | MultimodalDocument], batch_size: Optional[int] = None, **kwargs: Any) -> List[List[float]]
```

批量获取嵌入向量（同步）。

**参数**：

* **texts**(List[str | MultimodalDocument])：文本或多模态文档列表。
* **batch_size**(int, 可选)：批处理大小。默认值：None（使用 `max_batch_size`）。
* **kwargs**(Any)：可变参数；可包含 `callback_cls` 等。

**返回**：

**List[List[float]]**，与 `texts` 顺序一致的嵌入向量列表。

### async embed_multimodal

```python
embed_multimodal(doc: MultimodalDocument, **kwargs) -> List[float]
```

仅接受多模态文档的嵌入接口（异步）；若传入非 `MultimodalDocument` 将报错。

**参数**：

* **doc**(MultimodalDocument)：多模态文档，详见 [MultimodalDocument](../common/document.md)。
* **kwargs**：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[float]**，返回多模态文档的嵌入向量。

### embed_multimodal_sync

```python
embed_multimodal_sync(doc: MultimodalDocument, **kwargs) -> List[float]
```

仅接受多模态文档的嵌入接口（同步）。

**参数**：

* **doc**(MultimodalDocument)：多模态文档。
* **kwargs**：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[float]**，返回多模态文档的嵌入向量。

> **参考示例**：更多使用示例请参考 [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) 仓库中 `examples/retrieval/` 目录下的示例代码，包括：
> - `showcase_dashscope_multimodal_embedding.py` — 阿里云 DashScope 多模态嵌入与向量相似度对比示例
> - `showcase_text_embedding.py` — 文本嵌入示例
> - `showcase_multimodal_embedding.py` — 多模态嵌入示例
