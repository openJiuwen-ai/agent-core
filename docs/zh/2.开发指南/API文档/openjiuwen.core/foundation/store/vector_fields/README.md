# vector_fields

`openjiuwen.core.foundation.store.vector_fields` 提供了向量数据库字段配置的抽象接口和实现，用于配置不同向量数据库的索引类型和参数。

**Classes**：

| CLASS | DESCRIPTION | 详细 API |
|-------|-------------|----------|
| **VectorField** | 向量字段配置基类。 | [base.md](./base.md) |
| **ChromaVectorField** | ChromaDB 向量数据库的 HNSW 索引配置。 | [chroma_fields.md](./chroma_fields.md) |
| **MilvusFLAT** | Milvus 的 FLAT 索引配置。 | [milvus_fields.md](./milvus_fields.md) |
| **MilvusAUTO** | Milvus 的 AUTOINDEX 配置。 | [milvus_fields.md](./milvus_fields.md) |
| **MilvusSCANN** | Milvus 的 SCANN 索引配置。 | [milvus_fields.md](./milvus_fields.md) |
| **MilvusIVF** | Milvus 的倒排文件（IVF）索引配置。 | [milvus_fields.md](./milvus_fields.md) |
| **MilvusHNSW** | Milvus 的分层可导航小世界（HNSW）索引配置。 | [milvus_fields.md](./milvus_fields.md) |
