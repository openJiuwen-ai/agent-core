# openjiuwen.core.retrieval.vector_store.store

## func create_vector_store

```python
create_vector_store(config: VectorStoreConfig, **kwargs) -> VectorStore
```

根据 `VectorStoreConfig` 中的 `store_provider` 字段动态创建向量存储实例的工厂函数。

**参数**：

* **config**(VectorStoreConfig)：向量存储配置。`store_provider` 字段决定实例化哪个实现（`StoreType.Milvus`、`StoreType.Chroma` 或 `StoreType.PGVector`）。
* **kwargs**：传递给具体存储构造函数的额外关键字参数（如 `milvus_uri`、`milvus_token`、`chroma_path`、`pg_uri`）。

**返回**：

**VectorStore** — 所选向量存储实现的实例（`MilvusVectorStore`、`ChromaVectorStore` 或 `PGVectorStore`）。

**异常**：

**BaseError** — 如果 `store_provider` 值不受支持。

**样例**：

```python
>>> from openjiuwen.core.retrieval.common.config import VectorStoreConfig, StoreType
>>> from openjiuwen.core.retrieval.vector_store.store import create_vector_store
>>>
>>> # 创建 Milvus 向量存储
>>> config = VectorStoreConfig(
...     store_provider=StoreType.Milvus,
...     collection_name="my_collection",
...     distance_metric="cosine",
... )
>>> store = create_vector_store(config, milvus_uri="http://localhost:19530")
>>>
>>> # 创建 ChromaDB 向量存储
>>> config = VectorStoreConfig(
...     store_provider=StoreType.Chroma,
...     collection_name="my_collection",
... )
>>> store = create_vector_store(config, chroma_path="/tmp/chroma_data")
>>>
>>> # 创建 PGVector 存储
>>> config = VectorStoreConfig(
...     store_provider=StoreType.PGVector,
...     collection_name="my_collection",
... )
>>> store = create_vector_store(config, pg_uri="postgresql+asyncpg://user:pass@localhost/db")
```
