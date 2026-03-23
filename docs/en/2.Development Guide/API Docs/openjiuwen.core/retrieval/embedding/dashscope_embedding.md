# openjiuwen.core.retrieval.embedding.dashscope_embedding

## class openjiuwen.core.retrieval.embedding.dashscope_embedding.DashscopeEmbedding

Multimodal embedding client for Alibaba Cloud DashScope via the `dashscope` SDK. Supports text, image, video, and other inputs against the Model Studio multimodal embedding API. See Alibaba Cloud docs: [Multimodal embedding API reference](https://www.alibabacloud.com/help/en/model-studio/multimodal-embedding-api-reference) (Chinese: [多模态向量 API 参考](https://help.aliyun.com/zh/model-studio/multimodal-embedding-api-reference)).

```python
DashscopeEmbedding(config: EmbeddingConfig, timeout: int = 60, max_retries: int = 3, extra_headers: Optional[dict] = None, max_batch_size: int = 8, max_concurrent: int = 50, dimension: Optional[int] = None, verify: bool | str | ssl.SSLContext = True, **kwargs)
```

Initialize the DashScope multimodal embedding model.

**Parameters**:

* **config**(EmbeddingConfig): Embedding configuration; set `model_name`, `base_url` (e.g. `https://dashscope.aliyuncs.com/api/v1/`), and `api_key`.
* **timeout**(int): Request timeout in seconds. Default: 60.
* **max_retries**(int): Maximum retry count. Default: 3.
* **extra_headers**(dict, optional): Additional HTTP headers. Default: None.
* **max_batch_size**(int): Maximum batch size per request (keep within DashScope multimodal API limits). Default: 8.
* **max_concurrent**(int): Maximum concurrent requests. Default: 50.
* **dimension**(int, optional): Output vector dimension for Matryoshka-style models. Default: None (dimension inferred from the first response).
* **verify**(bool | str | ssl.SSLContext): HTTPS verification; `bool` enables default CA bundle; `str` is a path to a CA bundle; `ssl.SSLContext` is a custom context. Default: True.
* **kwargs**: Extra keyword arguments forwarded to the underlying HTTP clients.

**Notes**:

* Inherits from `APIEmbedding` and calls `dashscope.MultiModalEmbedding` (sync) and `dashscope.AioMultiModalEmbedding` (async).
* `embed_documents` / `embed_query` accept either plain `str` or [MultimodalDocument](../common/document.md); multimodal entries use `MultimodalDocument.dashscope_input` as elements of the request `input` list.
* Effective batch size is the minimum of the caller’s `batch_size` and the instance `max_batch_size`.
* If `callback_cls` is passed in `kwargs` to `embed_documents` / `embed_documents_sync`, it is used for per-batch completion callbacks (same pattern as the base class).

### property dimension

```python
dimension -> int
```

Returns the embedding vector dimension.

**Returns**:

**int**, the embedding vector dimension.

### async embed_query

```python
embed_query(text: str | MultimodalDocument, **kwargs: Any) -> List[float]
```

Embed a single query (async).

**Parameters**:

* **text**(str | MultimodalDocument): Query text or a multimodal document (see [MultimodalDocument](../common/document.md)).
* **kwargs**(Any): Variable arguments for additional parameters.

**Returns**:

**List[float]**, the embedding vector for the query.

### embed_query_sync

```python
embed_query_sync(text: str | MultimodalDocument, **kwargs: Any) -> List[float]
```

Embed a single query (sync).

**Parameters**:

* **text**(str | MultimodalDocument): Query text or a multimodal document.
* **kwargs**(Any): Variable arguments for additional parameters.

**Returns**:

**List[float]**, the embedding vector for the query.

### async embed_documents

```python
embed_documents(texts: List[str | MultimodalDocument], batch_size: Optional[int] = None, **kwargs: Any) -> List[List[float]]
```

Embed a list of texts or multimodal documents (async).

**Parameters**:

* **texts**(List[str | MultimodalDocument]): List of strings or multimodal documents.
* **batch_size**(int, optional): Batch size. Default: None (uses `max_batch_size`).
* **kwargs**(Any): Variable arguments; may include `callback_cls`, etc.

**Returns**:

**List[List[float]]**, embeddings in the same order as `texts`.

### embed_documents_sync

```python
embed_documents_sync(texts: List[str | MultimodalDocument], batch_size: Optional[int] = None, **kwargs: Any) -> List[List[float]]
```

Embed a list of texts or multimodal documents (sync).

**Parameters**:

* **texts**(List[str | MultimodalDocument]): List of strings or multimodal documents.
* **batch_size**(int, optional): Batch size. Default: None (uses `max_batch_size`).
* **kwargs**(Any): Variable arguments; may include `callback_cls`, etc.

**Returns**:

**List[List[float]]**, embeddings in the same order as `texts`.

### async embed_multimodal

```python
embed_multimodal(doc: MultimodalDocument, **kwargs) -> List[float]
```

Embed a multimodal document only (async). Passing a non-`MultimodalDocument` value results in an error.

**Parameters**:

* **doc**(MultimodalDocument): Multimodal document; see [MultimodalDocument](../common/document.md).
* **kwargs**: Variable arguments for additional parameters.

**Returns**:

**List[float]**, the embedding vector for the document.

### embed_multimodal_sync

```python
embed_multimodal_sync(doc: MultimodalDocument, **kwargs) -> List[float]
```

Embed a multimodal document only (sync).

**Parameters**:

* **doc**(MultimodalDocument): Multimodal document.
* **kwargs**: Variable arguments for additional parameters.

**Returns**:

**List[float]**, the embedding vector for the document.

> **Reference Examples**: For more usage examples, refer to the [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) repository under `examples/retrieval/`, including:
> - `showcase_dashscope_multimodal_embedding.py` — DashScope multimodal embeddings and similarity comparison
> - `showcase_text_embedding.py` — Text embedding examples
> - `showcase_multimodal_embedding.py` — Multimodal embedding examples
