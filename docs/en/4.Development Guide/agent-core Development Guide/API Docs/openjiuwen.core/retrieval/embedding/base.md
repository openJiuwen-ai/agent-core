# openjiuwen.core.retrieval.embedding.base

## class openjiuwen.core.retrieval.embedding.base.Embedding

Embedding model abstract base class, providing a unified interface for text embedding.

### abstractmethod async embed_query

```python
embed_query(text: str, **kwargs: Any) -> List[float]
```

Get the embedding vector of text.

**Parameters**:

* **text**(str): Query text.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[float]**, returns the embedding vector of the query text.

### abstractmethod async embed_documents

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

### property dimension

```python
dimension() -> int
```

Return the dimension of embedding vectors.

**Returns**:

**int**, returns the dimension of embedding vectors.

