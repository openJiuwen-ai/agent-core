# openjiuwen.core.retrieval.indexing.indexer.milvus_indexer

## class openjiuwen.core.retrieval.indexing.indexer.milvus_indexer.MilvusIndexer

Milvus index manager implementation, responsible for building, updating, and deleting Milvus indexes.


```python
MilvusIndexer(milvus_uri: str, milvus_token: Optional[str] = None, text_field: str = "content", vector_field: str = "embedding", sparse_vector_field: str = "sparse_vector", metadata_field: str = "metadata", doc_id_field: str = "document_id", **kwargs: Any)
```

Initialize Milvus index manager.

**Parameters**:

* **milvus_uri**(str): Milvus URI.
* **milvus_token**(str, optional): Milvus Token. Default: None.
* **text_field**(str): Text field name. Default: "content".
* **vector_field**(str): Vector field name. Default: "embedding".
* **sparse_vector_field**(str): Sparse vector field name. Default: "sparse_vector".
* **metadata_field**(str): Metadata field name. Default: "metadata".
* **doc_id_field**(str): Document ID field name. Default: "document_id".
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

### property client

```python
client() -> MilvusClient
```

Get Milvus client.

**Returns**:

**MilvusClient**, returns Milvus client instance.

### async build_index

```python
build_index(chunks: List[TextChunk], config: IndexConfig, embed_model: Optional[Embedding] = None, **kwargs: Any) -> bool
```

Build index.

**Parameters**:

* **chunks**(List[TextChunk]): List of text chunks.
* **config**(IndexConfig): Index configuration.
* **embed_model**(Embedding, optional): Embedding model instance (required for vector index). Default: None.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**bool**, returns True if building is successful, otherwise returns False.

### async update_index

```python
update_index(chunks: List[TextChunk], doc_id: str, config: IndexConfig, embed_model: Optional[Embedding] = None, **kwargs: Any) -> bool
```

Update index.

**Parameters**:

* **chunks**(List[TextChunk]): List of text chunks.
* **doc_id**(str): Document ID.
* **config**(IndexConfig): Index configuration.
* **embed_model**(Embedding, optional): Embedding model instance (required for vector index). Default: None.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**bool**, returns True if update is successful, otherwise returns False.

### async delete_index

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

### async index_exists

```python
index_exists(index_name: str) -> bool
```

Check if index exists.

**Parameters**:

* **index_name**(str): Index name.

**Returns**:

**bool**, returns True if index exists, otherwise returns False.

### async get_index_info

```python
get_index_info(index_name: str) -> Dict[str, Any]
```

Get index information.

**Parameters**:

* **index_name**(str): Index name.

**Returns**:

**Dict[str, Any]**, returns a dictionary containing index statistics and metadata.

### close

```python
close() -> None
```

Close the index manager and release resources.

