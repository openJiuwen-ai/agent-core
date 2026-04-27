# openjiuwen.extensions.store.vector.es_vector_store

## class openjiuwen.extensions.store.vector.es_vector_store.ElasticsearchVectorStore

Elasticsearch-based vector store implementation using Elasticsearch's `dense_vector` field type with k-NN search to provide vector similarity search capabilities.

Each collection maps to an Elasticsearch index whose mapping is derived from the provided `CollectionSchema`.

### Implementation Details

- Uses `AsyncElasticsearch` for all operations
- Vector fields are stored as `dense_vector` with `index: true` so that native k-NN search is available (ES 8.x+)
- Scalar fields are mapped to their closest ES types
- Collection metadata (schema, distance metric, schema version) is persisted in a dedicated `_meta` document inside the index

> **Reference Example**: See the project example code for more usage details:
> - `examples/es_vector_store_example.py` - Full ES Vector Store usage example

---

## __init__

```python
__init__(es: AsyncElasticsearch, index_prefix: str = "agent_vector")
```

Initialize the Elasticsearch vector store.

**Parameters**:

* **es**(AsyncElasticsearch): Async Elasticsearch client instance.
* **index_prefix**(str, optional): Index prefix. Default: "agent_vector".

---

## async create_collection

```python
async create_collection(collection_name: str, schema: Union[CollectionSchema, Dict[str, Any]], **kwargs: Any) -> None
```

Create a new collection (creates an index in Elasticsearch).

**Parameters**:

* **collection_name**(str): Collection name.
* **schema**(CollectionSchema | Dict[str, Any]): Collection schema, can be a CollectionSchema object or dictionary.
* **kwargs**(Any): Additional configuration:
    * **distance_metric**(str, optional): Distance metric type. Supports "COSINE", "L2", "IP". Default: "COSINE".

**Raises**:

* **BaseError**: When the schema is missing a FLOAT_VECTOR field.

**Example**:

```python
from openjiuwen.core.foundation.store.base_vector_store import CollectionSchema, FieldSchema, VectorDataType
from openjiuwen.extensions.store.vector.es_vector_store import ElasticsearchVectorStore

# Create schema
schema = CollectionSchema(description="test collection")
schema.add_field(FieldSchema(name="id", dtype=VectorDataType.VARCHAR, is_primary=True))
schema.add_field(FieldSchema(name="embedding", dtype=VectorDataType.FLOAT_VECTOR, dim=768))
schema.add_field(FieldSchema(name="content", dtype=VectorDataType.VARCHAR))

# Create collection
await store.create_collection("my_collection", schema, distance_metric="COSINE")
```

---

## async delete_collection

```python
async delete_collection(collection_name: str, **kwargs: Any) -> None
```

Delete a collection (deletes the Elasticsearch index).

**Parameters**:

* **collection_name**(str): Collection name.
* **kwargs**(Any): Reserved for future use.

**Example**:

```python
await store.delete_collection("my_collection")
```

---

## async collection_exists

```python
async collection_exists(collection_name: str, **kwargs: Any) -> bool
```

Check if a collection exists.

**Parameters**:

* **collection_name**(str): Collection name.
* **kwargs**(Any): Reserved for future use.

**Returns**:

**bool**: True if the collection exists, False otherwise.

**Example**:

```python
exists = await store.collection_exists("my_collection")
if exists:
    print("Collection exists")
else:
    print("Collection does not exist")
```

---

## async get_schema

```python
async get_schema(collection_name: str, **kwargs: Any) -> CollectionSchema
```

Get the schema of a collection.

**Parameters**:

* **collection_name**(str): Collection name.
* **kwargs**(Any): Additional configuration:
    * **primary_key_field**(str, optional): Primary key field name. Default: "id".

**Returns**:

**CollectionSchema**: The collection's schema object.

**Raises**:

* **BaseError**: When the collection does not exist.

**Example**:

```python
schema = await store.get_schema("my_collection")
print(f"Collection has {len(schema.fields)} fields")
for field in schema.fields:
    print(f"- {field.name}: {field.dtype.value}")
```

---

## async add_docs

```python
async add_docs(collection_name: str, docs: List[Dict[str, Any]], **kwargs: Any) -> None
```

Batch add documents to a collection.

**Parameters**:

* **collection_name**(str): Collection name.
* **docs**(List[Dict[str, Any]]): Document list, each document is a dictionary.
* **kwargs**(Any): Additional configuration:
    * **batch_size**(int, optional): Batch size. Default: 500.

**Example**:

```python
documents = [
    {
        "id": "doc1",
        "embedding": [0.1, 0.2, 0.3],
        "content": "This is the first document",
    },
    {
        "id": "doc2",
        "embedding": [0.4, 0.5, 0.6],
        "content": "This is the second document",
    },
]

await store.add_docs("my_collection", documents, batch_size=100)
```

---

## async search

```python
async search(collection_name: str, query_vector: List[float], vector_field: str, top_k: int = 5, filters: Optional[Dict[str, Any]] = None, **kwargs: Any) -> List[VectorSearchResult]
```

Vector similarity search.

**Parameters**:

* **collection_name**(str): Collection name.
* **query_vector**(List[float]): Query vector.
* **vector_field**(str): Vector field name.
* **top_k**(int, optional): Number of similar results to return. Default: 5.
* **filters**(Dict[str, Any], optional): Metadata filter conditions. Default: None.
* **kwargs**(Any): Additional configuration:
    * **metric_type**(str, optional): Distance metric type. Default: uses the metric specified at collection creation.
    * **num_candidates**(int, optional): k-NN candidate count. Default: max(top_k * 10, 100).
    * **output_fields**(List[str], optional): Output field list.

**Returns**:

**List[VectorSearchResult]**: Search result list, each result contains score and field information.

**Example**:

```python
# Basic search
query = [0.1, 0.2, 0.3]
results = await store.search("my_collection", query, "embedding", top_k=5)

# Search with filters
results = await store.search(
    "my_collection",
    query,
    "embedding",
    top_k=10,
    filters={"category": "tech"}
)

# Search with list filter
results = await store.search(
    "my_collection",
    query,
    "embedding",
    top_k=10,
    filters={"category": ["tech", "science"]}
)

for result in results:
    print(f"ID: {result.fields['id']}, Score: {result.score:.4f}")
    print(f"Content: {result.fields['content']}")
```

---

## async delete_docs_by_ids

```python
async delete_docs_by_ids(collection_name: str, ids: List[str], **kwargs: Any) -> None
```

Delete documents by document IDs.

**Parameters**:

* **collection_name**(str): Collection name.
* **ids**(List[str]): List of document IDs to delete.
* **kwargs**(Any): Additional configuration:
    * **batch_size**(int, optional): Batch size. Default: 500.

**Example**:

```python
await store.delete_docs_by_ids("my_collection", ["doc1", "doc2"])
```

---

## async delete_docs_by_filters

```python
async delete_docs_by_filters(collection_name: str, filters: Dict[str, Any], **kwargs: Any) -> None
```

Delete documents matching filter conditions.

**Parameters**:

* **collection_name**(str): Collection name.
* **filters**(Dict[str, Any]): Filter conditions.
* **kwargs**(Any): Reserved for future use.

**Example**:

```python
# Delete documents where category is "tech"
await store.delete_docs_by_filters("my_collection", {"category": "tech"})

# Delete documents where category is in ["tech", "science"]
await store.delete_docs_by_filters("my_collection", {"category": ["tech", "science"]})
```

---

## async list_collection_names

```python
async list_collection_names() -> List[str]
```

List all collection names.

**Returns**:

**List[str]**: Collection name list.

**Example**:

```python
collections = await store.list_collection_names()
for coll in collections:
    print(f"Collection: {coll}")
```

---

## async get_collection_metadata

```python
async get_collection_metadata(collection_name: str) -> Dict[str, Any]
```

Get collection metadata.

**Parameters**:

* **collection_name**(str): Collection name.

**Returns**:

**Dict[str, Any]**: Metadata dictionary with the following keys:
* **schema**(Dict): Collection schema
* **distance_metric**(str): Distance metric type
* **vector_field**(str): Vector field name
* **vector_dim**(int): Vector dimension
* **schema_version**(int): Schema version
* **collection_name**(str): Collection name

**Example**:

```python
metadata = await store.get_collection_metadata("my_collection")
print(f"Distance metric: {metadata['distance_metric']}")
print(f"Vector dimension: {metadata['vector_dim']}")
print(f"Schema version: {metadata['schema_version']}")
```

---

## async update_collection_metadata

```python
async update_collection_metadata(collection_name: str, metadata: Dict[str, Any]) -> None
```

Update collection metadata.

**Parameters**:

* **collection_name**(str): Collection name.
* **metadata**(Dict[str, Any]): Metadata to update.

**Raises**:

* **BaseError**: When schema_version is negative or not an integer.

**Example**:

```python
await store.update_collection_metadata("my_collection", {"schema_version": 1})
```

---

## async update_schema

```python
async update_schema(collection_name: str, operations: List[BaseOperation]) -> None
```

Update the schema of a collection.

**Parameters**:

* **collection_name**(str): Collection name.
* **operations**(List[BaseOperation]): Schema operation list.

**Description**:

This method creates a new temporary collection, migrates data, and then replaces the original collection. Operations include adding fields, removing fields, etc.

**Example**:

```python
from openjiuwen.core.memory.migration.operation.add_field_operation import AddFieldOperation

# Add a new field
operation = AddFieldOperation(
    field_name="new_field",
    field_type="VARCHAR",
    default_value=""
)

await store.update_schema("my_collection", [operation])
```

---

## Supported Data Type Mappings

ElasticsearchVectorStore supports the following data type mappings to Elasticsearch types:

| VectorDataType | Elasticsearch Type | Description |
|---------------|-------------------|-------------|
| FLOAT_VECTOR | dense_vector | Vector field, supports k-NN search |
| VARCHAR | keyword | String field |
| INT64 | long | 64-bit integer |
| INT32 | integer | 32-bit integer |
| INT16 | integer | 16-bit integer |
| INT8 | integer | 8-bit integer |
| FLOAT | float | Single-precision floating point |
| DOUBLE | double | Double-precision floating point |
| BOOL | boolean | Boolean value |
| JSON | object | JSON object |
| ARRAY | object | Array (stored as object) |

---

## Supported Distance Metrics

| Metric Type | ES similarity | Description |
|-------------|--------------|-------------|
| COSINE | cosine | Cosine similarity (default) |
| L2 | l2_norm | Euclidean distance |
| IP | dot_product | Inner product |

---

## Complete Example

```python
import asyncio
from elasticsearch import AsyncElasticsearch
from openjiuwen.core.foundation.store.base_vector_store import (
    CollectionSchema,
    FieldSchema,
    VectorDataType,
)
from openjiuwen.extensions.store.vector.es_vector_store import ElasticsearchVectorStore


async def main():
    # Connect to Elasticsearch
    # Note: Supports both local and remote Elasticsearch servers
    # Local example: "http://localhost:9200"
    # Remote example: "https://your-es-server.com:9200"
    # For remote connections, it is recommended to set request_timeout, verify_certs, etc.
    es = AsyncElasticsearch("http://localhost:9200", verify_certs=False)
    store = ElasticsearchVectorStore(es=es, index_prefix="my_app")

    # Create collection
    schema = CollectionSchema(description="document vector store")
    schema.add_field(FieldSchema(name="id", dtype=VectorDataType.VARCHAR, is_primary=True))
    schema.add_field(FieldSchema(name="embedding", dtype=VectorDataType.FLOAT_VECTOR, dim=768))
    schema.add_field(FieldSchema(name="title", dtype=VectorDataType.VARCHAR))
    schema.add_field(FieldSchema(name="content", dtype=VectorDataType.VARCHAR))
    schema.add_field(FieldSchema(name="category", dtype=VectorDataType.VARCHAR))

    await store.create_collection("documents", schema, distance_metric="COSINE")

    # Add documents
    docs = [
        {
            "id": "1",
            "embedding": [0.1] * 768,
            "title": "Python Tutorial",
            "content": "Python is a programming language",
            "category": "programming",
        },
        {
            "id": "2",
            "embedding": [0.2] * 768,
            "title": "Machine Learning Basics",
            "content": "Machine learning is a branch of AI",
            "category": "AI",
        },
    ]

    await store.add_docs("documents", docs)

    # Search
    query = [0.15] * 768
    results = await store.search("documents", query, "embedding", top_k=5)

    for result in results:
        print(f"Score: {result.score:.4f}")
        print(f"Title: {result.fields['title']}")

    # Cleanup
    await store.delete_collection("documents")
    await es.close()


asyncio.run(main())
```

---

## Notes

1. **Elasticsearch Version**: Requires Elasticsearch 8.x or higher for native k-NN search support.
2. **Client Version Compatibility**: **Ensure the Elasticsearch Python client version matches the server version.** When the client version does not match the server version (e.g., server 8.17.1, client 9.3.0), connection failures will occur with an error:
   ```
   BadRequestError(400, 'media_type_header_exception', 'Invalid media-type value on headers [Accept, Content-Type]',
   Accept version must be either version 8 or 7, but found 9. Accept=application/vnd.elasticsearch+json; compatible-with=9)
   ```
   It is recommended to install a client package matching the server version, e.g.: `pip install elasticsearch==8.17.1`
3. **Remote Connection**: Supports connecting to both local and remote Elasticsearch servers. When connecting to a remote server:
   - Ensure network connectivity, check firewall rules allow access to the Elasticsearch port (default 9200)
   - For HTTPS connections, it is recommended to set `verify_certs=True` and provide valid certificates
   - Adjust `request_timeout` parameter appropriately based on network latency
   - If the server has security authentication enabled, provide `basic_auth` or `api_key` authentication information
4. **Index Naming**: Index name format is `{index_prefix}__{collection_name}`.
5. **Metadata Storage**: Collection metadata is stored in a special document with ID `__collection_metadata__` within the index.
6. **Async Operations**: All methods are asynchronous and must be called with `await`.
7. **Batch Operations**: Adding and deleting documents supports batch processing, adjustable via the `batch_size` parameter.
8. **Vector Dimension**: The vector field dimension must be specified when creating a collection.
9. **Primary Key Field**: It is recommended to always specify a primary key field for document management.
10. **Index Refresh**: The index is automatically refreshed after adding documents to ensure data is searchable.

---

## Related Documentation

- [BaseVectorStore](../../foundation/store/base_vector_store.md) - Vector store base class
- [CollectionSchema](../../foundation/store/base_vector_store.md) - Collection Schema
- [VectorSearchResult](../../foundation/store/base_vector_store.md) - Search results
- [ES Vector Store Example](../../../../examples/es_vector_store_example.py) - Full usage example
