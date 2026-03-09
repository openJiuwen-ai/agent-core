# vector_store

`openjiuwen.core.retrieval.vector_store` 提供了向量存储的抽象接口和实现。

**Classes**：

| CLASS | DESCRIPTION | 详细 API |
|-------|-------------|----------|
| **VectorStore** | 向量存储抽象基类。 | [base.md](./base.md) |
| **ChromaVectorStore** | ChromaDB 向量存储实现。 | [chroma_store.md](./chroma_store.md) |
| **MilvusVectorStore** | Milvus 向量存储实现。 | [milvus_store.md](./milvus_store.md) |

**Functions**：

| 函数 | 说明 | 详细 API |
|------|------|----------|
| **create_vector_store** | 根据 `VectorStoreConfig` 动态创建向量存储的工厂函数。 | [store.md](./store.md) |
