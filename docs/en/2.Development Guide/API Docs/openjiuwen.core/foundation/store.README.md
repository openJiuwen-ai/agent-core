# store

`openjiuwen.core.foundation.store` provides the storage abstraction module for openJiuwen.

**Detailed API Documentation**: [store.md](./store/store.md)

**Classes**:

| CLASS | DESCRIPTION |
|-------|-------------|
| **BaseKVStore** | KV storage abstract base class. |
| **BaseDbStore** | Database storage abstract base class. |
| **BaseVectorStore** | Vector storage abstract base class; also the stable public API for third-party plugins. |
| **BaseObjectStorageClient** | Object storage client abstract base class. |
| **InMemoryKVStore** | In-memory KV storage implementation. |
| **DbBasedKVStore** | Database-based KV storage implementation. |
| **DefaultDbStore** | Default database storage implementation. |
| **AioBotoClient** | Async S3 client implementation using aioboto3. |

**Functions & Constants**:

| Name | DESCRIPTION |
|------|-------------|
| **create_vector_store** | Vector-store factory; resolution order: built-in → `register_vector_store` → `openjiuwen.vector_stores` entry_points. |
| **register_vector_store** | Programmatically register a third-party vector-store implementation (useful for private backends that do not ship via PyPI entry_points). |
| **VECTOR_STORE_ENTRY_POINT_GROUP** | Stable public constant: the Python entry_points group name third-party vector-store plugins must declare. |

> For third-party plugin authoring, see [Store Plugin Development](../../Advanced%20Usage/Store%20Plugin%20Development.md).

**graph** (graph store):

| Document | DESCRIPTION |
|----------|-------------|
| [graph](./store/graph/README.md) | Graph-structured vector store: GraphStore protocol, Entity/Relation/Episode, MilvusGraphStore, config and constants. |