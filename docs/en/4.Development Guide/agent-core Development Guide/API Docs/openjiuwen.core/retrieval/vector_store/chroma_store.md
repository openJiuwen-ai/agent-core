# openjiuwen.core.retrieval.vector_store.chroma_store

## class openjiuwen.core.retrieval.vector_store.chroma_store.ChromaVectorStore

ChromaDB vector store implementation, supporting vector search, sparse search (text matching), and hybrid search.

> **Reference Examples**: For more usage examples, please refer to the example code in the [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) repository under the `examples/retrieval/` directory, including:
> - `chroma_query_expr.py` - ChromaDB QueryExpr usage examples

> **Reference**: Vector database similarity score calculation: [VectorStoreScoring](https://gitcode.com/SushiNinja/VectorStoreScoring).


```python
ChromaVectorStore(config: VectorStoreConfig, chroma_path: str, text_field: str = "content", vector_field: str | ChromaVectorField = "embedding", sparse_vector_field: str = "sparse_vector", metadata_field: str = "metadata", doc_id_field: str = "document_id", **kwargs: Any)
```

Initialize ChromaDB vector store (persistent mode).

**Parameters**:

* **config**(VectorStoreConfig): Vector store configuration.
* **chroma_path**(str): ChromaDB persistence path (required).
* **text_field**(str): Text field name. Default: "content".
* **vector_field**(str | ChromaVectorField, optional): Vector field name (str) or vector field configuration object (ChromaVectorField). If a string is provided, a ChromaVectorField will be created with default configuration. Default: "embedding". For more configuration options, see [ChromaVectorField documentation](../../foundation/store/vector_fields/chroma_fields.md).
* **sparse_vector_field**(str): Sparse vector field name (stored as metadata in ChromaDB). Default: "sparse_vector".
* **metadata_field**(str): Metadata field name. Default: "metadata".
* **doc_id_field**(str): Document ID field name. Default: "document_id".
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

### property client

```python
client -> chromadb.PersistentClient
```

Get ChromaDB client.

**Returns**:

**chromadb.PersistentClient**, returns ChromaDB persistent client instance.

### property collection

```python
collection -> chromadb.Collection
```

Get ChromaDB collection.

**Returns**:

**chromadb.Collection**, returns ChromaDB collection instance.

### property distance_metric

```python
distance_metric -> str
```

Get raw distance metric string.

**Returns**:

**str**, returns distance metric string.

### staticmethod create_client

```python
create_client(database_name: str, path_or_uri: str, token: str = "", **kwargs: Any) -> chromadb.PersistentClient
```

Create Chroma client and ensure database exists.

**Parameters**:

* **database_name**(str): Database name.
* **path_or_uri**(str): Path or URI.
* **token**(str): Access token. Default: "".
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**chromadb.PersistentClient**, returns ChromaDB persistent client instance.

### check_vector_field

```python
check_vector_field() -> None
```

Check if vector field configuration is consistent with actual database.

### async add

```python
add(data: dict | List[dict], batch_size: int | None = 128, **kwargs: Any) -> None
```

Add vector data.

**Parameters**:

* **data**(dict | List[dict]): Vector data, can be a single dictionary or a list of dictionaries.
* **batch_size**(int, optional): Batch size. Default: 128.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

### async search

```python
search(query_vector: List[float], top_k: int = 5, filters: Optional[dict | QueryExpr] = None, **kwargs: Any) -> List[SearchResult]
```

Vector search.

**Parameters**:

* **query_vector**(List[float]): Query vector.
* **top_k**(int): Number of results to return. Default: 5.
* **filters**(dict | QueryExpr, optional): Metadata filter conditions. Default: None. For more configuration options about QueryExpr, see [QueryExpr documentation](../../foundation/store/query/base.md).
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[SearchResult]**, returns a list of search results.

### async sparse_search

```python
sparse_search(query_text: str, top_k: int = 5, filters: Optional[dict | QueryExpr] = None, **kwargs: Any) -> List[SearchResult]
```

Sparse search (text matching).

**Parameters**:

* **query_text**(str): Query text.
* **top_k**(int): Number of results to return. Default: 5.
* **filters**(dict | QueryExpr, optional): Metadata filter conditions. Default: None. For more configuration options about QueryExpr, see [QueryExpr documentation](../../foundation/store/query/base.md).
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[SearchResult]**, returns a list of search results.

### async hybrid_search

```python
hybrid_search(query_text: str, query_vector: Optional[List[float]] = None, top_k: int = 5, alpha: float = 0.5, filters: Optional[dict | QueryExpr] = None, **kwargs: Any) -> List[SearchResult]
```

Hybrid search (sparse retrieval + vector retrieval), using RRF algorithm to fuse results.

**Parameters**:

* **query_text**(str): Query text.
* **query_vector**(List[float], optional): Query vector (will be used directly if provided, otherwise needs to be embedded first). Default: None.
* **top_k**(int): Number of results to return. Default: 5.
* **alpha**(float): Hybrid weight (0=pure sparse retrieval, 1=pure vector retrieval, 0.5=balanced). Default: 0.5.
* **filters**(dict | QueryExpr, optional): Metadata filter conditions. Default: None. For more configuration options about QueryExpr, see [QueryExpr documentation](../../foundation/store/query/base.md).
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[SearchResult]**, returns a list of search results, using RRF algorithm to fuse vector retrieval and sparse retrieval results.

### async delete

```python
delete(ids: Optional[List[str]] = None, filter_expr: str | QueryExpr | None = None, **kwargs: Any) -> bool
```

Delete vector data.

**Parameters**:

* **ids**(List[str], optional): List of IDs to delete. Default: None.
* **filter_expr**(str | QueryExpr, optional): Filter expression. Default: None. For more configuration options about QueryExpr, see [QueryExpr documentation](../../foundation/store/query/base.md).
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**bool**, returns True if deletion is successful, otherwise returns False.

### close

```python
close() -> None
```

Close the vector store and release resources.

### async table_exists

```python
table_exists(table_name: str) -> bool
```

Check if a collection exists in current database.

**Parameters**:

* **table_name**(str): Collection name.

**Returns**:

**bool**, returns True if collection exists, otherwise returns False.

### async delete_table

```python
delete_table(table_name: str) -> None
```

Delete a collection from current database.

**Parameters**:

* **table_name**(str): Collection name.
