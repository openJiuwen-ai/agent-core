# openjiuwen.core.retrieval.indexing.indexer.base

## class openjiuwen.core.retrieval.indexing.indexer.base.Indexer

Index manager abstract base class, providing a unified interface for index building, updating, and deletion.

### abstractmethod async build_index

```python
build_index(chunks: List[TextChunk], config: IndexConfig, embed_model: Optional[Embedding] = None, **kwargs: Any) -> bool
```

Build index.

**Parameters**:

* **chunks**(List[TextChunk]): List of text chunks (e.g., list).
* **config**(IndexConfig): Index configuration.
* **embed_model**(Embedding, optional): Embedding model instance (required for vector index). Default: None.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**bool**, returns True if building is successful, otherwise returns False.

### abstractmethod async update_index

```python
update_index(chunks: List[TextChunk], doc_id: str, config: IndexConfig, embed_model: Optional[Embedding] = None, **kwargs: Any) -> bool
```

Update index.

**Parameters**:

* **chunks**(List[TextChunk]): List of text chunks (e.g., list).
* **doc_id**(str): Document ID.
* **config**(IndexConfig): Index configuration.
* **embed_model**(Embedding, optional): Embedding model instance (required for vector index). Default: None.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**bool**, returns True if update is successful, otherwise returns False.

### abstractmethod async delete_index

```python
delete_index(doc_id: str, index_name: str, **kwargs: Any) -> bool
```

Delete index.

**Parameters**:

* **doc_id**(str): Document ID.
* **index_name**(str): Index name.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**bool**, returns True if deletion is successful, otherwise returns False.

### abstractmethod async index_exists

```python
index_exists(index_name: str) -> bool
```

Check if index exists.

**Parameters**:

* **index_name**(str): Index name.

**Returns**:

**bool**, returns True if index exists, otherwise returns False.

### abstractmethod async get_index_info

```python
get_index_info(index_name: str) -> Dict[str, Any]
```

Get index information.

**Parameters**:

* **index_name**(str): Index name.

**Returns**:

**Dict[str, Any]**, returns index information dictionary.

