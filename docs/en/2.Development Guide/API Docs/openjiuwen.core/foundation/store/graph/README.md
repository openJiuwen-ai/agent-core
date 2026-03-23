# graph

`openjiuwen.core.foundation.store.graph` provides graph-structured vector storage and retrieval for entities, relations, and episodes, with hybrid search and BFS graph expansion backed by Milvus.

> **Reference examples**: For more usage examples, see the [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) repository under `examples/store/`: `showcase_milvus_graph_store.py` (full MilvusGraphStore demo: connect, add entities/relations/episodes, hybrid search, BFS graph expansion, query by id/expr, refresh, delete, close), and `graph_scenario_data.py` (building `Entity`, `Relation`, `Episode` and using `Relation.update_connected_entities()`).

**Document index**:

| Document | Description |
|----------|-------------|
| [graph_store](./graph_store.md) | GraphStore protocol, GraphStoreFactory, MilvusGraphStore |
| [graph_objects](./graph_objects.md) | Entity, Relation, Episode and related data classes |
| [config](./config.md) | GraphConfig, GraphStoreIndexConfig, GraphStoreStorageConfig and constants |

**Constants**:

| Constant | Description |
|----------|-------------|
| **ENTITY_COLLECTION** | Entity collection name |
| **RELATION_COLLECTION** | Relation collection name |
| **EPISODE_COLLECTION** | Episode collection name |
