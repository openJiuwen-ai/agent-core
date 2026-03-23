# graph

`openjiuwen.core.foundation.store.graph` 提供图结构向量存储与检索能力，支持实体（Entity）、关系（Relation）、情节（Episode）三类图对象，以及基于 Milvus 的混合检索与 BFS 图扩展搜索。

> **参考示例**：更多使用示例请参考 [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) 仓库中 `examples/store/` 目录下的示例代码，包括：`showcase_milvus_graph_store.py`（MilvusGraphStore 完整演示：连接、写入实体/关系/情节、混合检索、BFS 图扩展、按 id/表达式查询、刷新、删除、关闭）、`graph_scenario_data.py`（各类 GraphObject 的构建示例：`Entity`、`Relation`、`Episode` 及 `Relation.update_connected_entities()` 的用法）。

**文档索引**：

| 文档 | 说明 |
|------|------|
| [graph_store](./graph_store.md) | GraphStore 协议、GraphStoreFactory、MilvusGraphStore |
| [graph_objects](./graph_objects.md) | Entity、Relation、Episode 等图对象数据类 |
| [config](./config.md) | GraphConfig、GraphStoreIndexConfig、GraphStoreStorageConfig 及常量 |

**常用常量**：

| 常量 | 说明 |
|------|------|
| **ENTITY_COLLECTION** | 实体集合名 |
| **RELATION_COLLECTION** | 关系集合名 |
| **EPISODE_COLLECTION** | 情节集合名 |
