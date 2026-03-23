# store

`openjiuwen.core.foundation.store` provides the storage abstraction module for openJiuwen.

**Detailed API Documentation**: [store.md](./store/store.md)

**Classes**:

| CLASS | DESCRIPTION |
|-------|-------------|
| **BaseKVStore** | KV storage abstract base class. |
| **BaseDbStore** | Database storage abstract base class. |
| **BaseObjectStorageClient** | Object storage client abstract base class. |
| **InMemoryKVStore** | In-memory KV storage implementation. |
| **DbBasedKVStore** | Database-based KV storage implementation. |
| **DefaultDbStore** | Default database storage implementation. |
| **AioBotoClient** | Async S3 client implementation using aioboto3. |

**graph** (graph store):

| Document | DESCRIPTION |
|----------|-------------|
| [graph](./store/graph/README.md) | Graph-structured vector store: GraphStore protocol, Entity/Relation/Episode, MilvusGraphStore, config and constants. |