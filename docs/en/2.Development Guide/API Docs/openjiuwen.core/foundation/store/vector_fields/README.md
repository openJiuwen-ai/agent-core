# vector_fields

`openjiuwen.core.foundation.store.vector_fields` provides abstract interfaces and implementations for vector database field configurations, used to configure index types and parameters for different vector databases.

**Classes**:

| CLASS | DESCRIPTION | Detailed API |
|-------|-------------|---------------|
| **VectorField** | Vector field configuration base class. | [base.md](./base.md) |
| **ChromaVectorField** | HNSW index configuration for ChromaDB vector database. | [chroma_fields.md](./chroma_fields.md) |
| **MilvusFLAT** | FLAT index configuration for Milvus. | [milvus_fields.md](./milvus_fields.md) |
| **MilvusAUTO** | AUTOINDEX configuration for Milvus. | [milvus_fields.md](./milvus_fields.md) |
| **MilvusSCANN** | SCANN index configuration for Milvus. | [milvus_fields.md](./milvus_fields.md) |
| **MilvusIVF** | Inverted File (IVF) index configuration for Milvus. | [milvus_fields.md](./milvus_fields.md) |
| **MilvusHNSW** | Hierarchical Navigable Small World (HNSW) index configuration for Milvus. | [milvus_fields.md](./milvus_fields.md) |
