# openjiuwen.core.retrieval.vector_store.store

## func create_vector_store

```python
create_vector_store(config: VectorStoreConfig, **kwargs) -> VectorStore
```

Factory function to create vector store instances dynamically based on the `store_provider` field in `VectorStoreConfig`.

**Parameters**:

* **config**(VectorStoreConfig): Vector store configuration. The `store_provider` field determines which implementation to instantiate (`StoreType.Milvus`, `StoreType.Chroma`, or `StoreType.PGVector`).
* **kwargs**: Additional keyword arguments forwarded to the concrete store constructor (e.g., `milvus_uri`, `milvus_token`, `chroma_path`, `pg_uri`).

**Returns**:

**VectorStore** — An instance of the selected vector store implementation (`MilvusVectorStore`, `ChromaVectorStore`, or `PGVectorStore`).

**Raises**:

**BaseError** — If the `store_provider` value is not supported.

**Example**:

```python
>>> from openjiuwen.core.retrieval.common.config import VectorStoreConfig, StoreType
>>> from openjiuwen.core.retrieval.vector_store.store import create_vector_store
>>>
>>> # Create a Milvus vector store
>>> config = VectorStoreConfig(
...     store_provider=StoreType.Milvus,
...     collection_name="my_collection",
...     distance_metric="cosine",
... )
>>> store = create_vector_store(config, milvus_uri="http://localhost:19530")
>>>
>>> # Create a ChromaDB vector store
>>> config = VectorStoreConfig(
...     store_provider=StoreType.Chroma,
...     collection_name="my_collection",
... )
>>> store = create_vector_store(config, chroma_path="/tmp/chroma_data")
>>>
>>> # Create a PGVector store
>>> config = VectorStoreConfig(
...     store_provider=StoreType.PGVector,
...     collection_name="my_collection",
... )
>>> store = create_vector_store(config, pg_uri="postgresql+asyncpg://user:pass@localhost/db")
```
