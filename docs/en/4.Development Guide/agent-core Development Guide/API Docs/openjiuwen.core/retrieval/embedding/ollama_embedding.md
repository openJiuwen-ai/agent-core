# openjiuwen.core.retrieval.embedding.ollama_embedding

## class openjiuwen.core.retrieval.embedding.ollama_embedding.OllamaEmbedding

Ollama embedding model implementation.


```python
__init__(config: EmbeddingConfig, hf_tokenizer_name: Optional[str] = None, timeout: int = 60, max_retries: int = 3, extra_headers: Optional[dict] = None,max_batch_size: int = 8, dimension: Optional[int] = None)
```

Initialize Ollama embedding model.

**Parameters**:

* **config**(EmbeddingConfig): Embedding model configuration.
* **hf_tokenizer_name**(str, optional): HuggingFace tokenizer name (optional). Default: None.
* **timeout**(int): Request timeout (seconds). Default: 60.
* **max_retries**(int): Maximum number of retries. Default: 3.
* **extra_headers**(dict, optional): Additional request headers. Default: None.
* **max_batch_size**(int): Maximum batch size. Default: 8.
* **dimension**(int, optional): Embedding output dimension. Default: None.

**Exceptions**:

* **ValueError**: If Ollama is not running or model is unavailable.

### property dimension

```python
dimension() -> int
```

Return the dimension of embedding vectors.

**Returns**:

**int**, returns the dimension of embedding vectors.

### property tokenizer

```python
tokenizer() -> Optional[AutoTokenizer]
```

Get tokenizer instance (if configured).

**Returns**:

**Optional[AutoTokenizer]**, returns tokenizer instance, or None if not configured.

### async embed_query

```python
embed_query(text: str, **kwargs: Any) -> List[float]
```

Get the embedding vector of text.

**Parameters**:

* **text**(str): Query text.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[float]**, returns the embedding vector of the query text.

### async embed_documents

```python
embed_documents(texts: List[str], batch_size: Optional[int] = None, **kwargs: Any) -> List[List[float]]
```

Get embedding vectors for a list of documents.

**Parameters**:

* **texts**(List[str]): List of document texts.
* **batch_size**(int, optional): Batch size. Default: None.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[List[float]]**, returns a list of embedding vectors for each document text.

