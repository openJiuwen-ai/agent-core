# vector_store

`openjiuwen.core.retrieval.vector_store` provides abstract interfaces and implementations for vector stores.

**Classes**:

| CLASS | DESCRIPTION | Detailed API |
|-------|-------------|---------------|
| **VectorStore** | Vector store abstract base class. | [base.md](./base.md) |
| **ChromaVectorStore** | ChromaDB vector store implementation. | [chroma_store.md](./chroma_store.md) |
| **MilvusVectorStore** | Milvus vector store implementation. | [milvus_store.md](./milvus_store.md) |

**Functions**:

| FUNCTION | DESCRIPTION | Detailed API |
|----------|-------------|---------------|
| **create_vector_store** | Factory function to create vector stores dynamically based on `VectorStoreConfig`. | [store.md](./store.md) |
