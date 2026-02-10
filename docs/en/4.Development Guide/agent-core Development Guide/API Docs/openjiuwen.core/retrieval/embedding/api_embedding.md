# openjiuwen.core.retrieval.embedding.api_embedding

## class openjiuwen.core.retrieval.embedding.api_embedding.APIEmbedding

API embedding model implementation, supporting multiple API formats.

> **Reference Examples**: For more usage examples, please refer to the example code in the [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) repository under the `examples/retrieval/` directory, including:
> - `showcase_text_embedding.py` - Text embedding examples


```python
APIEmbedding(config: EmbeddingConfig, timeout: int = 60, max_retries: int = 3, extra_headers: Optional[dict] = None, max_batch_size: int = 8, max_concurrent: int = 50)
```

Initialize API embedding model.

**Parameters**:

* **config**(EmbeddingConfig): Embedding model configuration.
* **timeout**(int): Request timeout (seconds). Default: 60.
* **max_retries**(int): Maximum number of retries. Default: 3.
* **extra_headers**(dict, optional): Additional request headers. Default: None.
* **max_batch_size**(int): Maximum batch size. Default: 8.
* **max_concurrent**(int): Maximum number of concurrent requests. Default: 50.

**Description**:

Supported API response formats:
* `{"embedding": [...]}`
* `{"embeddings": [...]}`
* `{"data": [{"embedding": [...]}, ...]}`

### property dimension

```python
dimension -> int
```

Return the dimension of embedding vectors.

**Returns**:

**int**, returns the dimension of embedding vectors.

### async embed_query

```python
embed_query(text: str, **kwargs: Any) -> List[float]
```

Get the embedding vector of text (async).

**Parameters**:

* **text**(str): Query text.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[float]**, returns the embedding vector of the query text.

### embed_query_sync

```python
embed_query_sync(text: str, **kwargs: Any) -> List[float]
```

Get the embedding vector of text (sync).

**Parameters**:

* **text**(str): Query text.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[float]**, returns the embedding vector of the query text.

### async embed_documents

```python
embed_documents(texts: List[str], batch_size: Optional[int] = None, **kwargs: Any) -> List[List[float]]
```

Get embedding vectors for a list of documents (async).

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

Get embedding vectors for a list of documents (sync).

**Parameters**:

* **texts**(List[str]): List of document texts.
* **batch_size**(int, optional): Batch size. Default: None (uses max_batch_size).
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[List[float]]**, returns a list of embedding vectors for each document text.

