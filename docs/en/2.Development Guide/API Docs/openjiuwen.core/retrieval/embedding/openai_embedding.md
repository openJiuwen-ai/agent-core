# openjiuwen.core.retrieval.embedding.openai_embedding

## class openjiuwen.core.retrieval.embedding.openai_embedding.OpenAIEmbedding

OpenAI embedding model implementation, supports services following OpenAI standard, supports `encoding_format="base64"` format.

```python
OpenAIEmbedding(config: EmbeddingConfig, timeout: int = 60, max_retries: int = 3, extra_headers: Optional[dict] = None, max_batch_size: int = 8, max_concurrent: int = 50, dimension: Optional[int] = None, verify: bool | str | ssl.SSLContext = True, **kwargs)
```

Initialize OpenAI embedding model.

**Parameters**:

* **config**(EmbeddingConfig): Embedding model configuration.
* **timeout**(int): Request timeout in seconds. Default: 60.
* **max_retries**(int): Maximum retry count. Default: 3.
* **extra_headers**(dict, optional): Additional request headers. Default: None.
* **max_batch_size**(int): Maximum batch size. Default: 8.
* **max_concurrent**(int): Maximum number of concurrent requests. Default: 50.
* **dimension**(int, optional): Embedding dimension (for Matryoshka models). Default: None.
* **verify**(bool | str | ssl.SSLContext): SSL verification settings, bool indicates whether to use default CA certificate, str indicates custom CA certificate path, ssl.SSLContext indicates custom SSL context. Default: True.
* **kwargs**: Variable arguments for passing additional configuration parameters.

### property dimension

```python
dimension -> int
```

Returns the embedding vector dimension.

**Returns**:

**int**, returns the embedding vector dimension.

### async embed_query

```python
embed_query(text: str, **kwargs: Any) -> List[float]
```

Get embedding vector for text (async).

**Parameters**:

* **text**(str): Query text.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[float]**, returns the embedding vector for the query text.

### embed_query_sync

```python
embed_query_sync(text: str, **kwargs: Any) -> List[float]
```

Get embedding vector for text (sync).

**Parameters**:

* **text**(str): Query text.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[float]**, returns the embedding vector for the query text.

### async embed_documents

```python
embed_documents(texts: List[str], batch_size: Optional[int] = None, **kwargs: Any) -> List[List[float]]
```

Get embedding vectors for document list (async).

**Parameters**:

* **texts**(List[str]): List of document texts.
* **batch_size**(int, optional): Batch size. Default: None (uses max_batch_size).
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[List[float]]**, returns a list of embedding vectors for each document text.

### embed_documents_sync

```python
embed_documents_sync(texts: List[str], batch_size: Optional[int] = None, **kwargs: Any) -> List[List[float]]
```

Get embedding vectors for document list (sync).

**Parameters**:

* **texts**(List[str]): List of document texts.
* **batch_size**(int, optional): Batch size. Default: None (uses max_batch_size).
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[List[float]]**, returns a list of embedding vectors for each document text.

> **Reference Examples**: For more usage examples, please refer to the example code in the [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) repository under the `examples/retrieval/` directory, including:
> - `showcase_text_embedding.py` - Text embedding examples
> - `showcase_multimodal_embedding.py` - Multimodal embedding examples
