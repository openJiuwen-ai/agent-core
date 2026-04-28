# store

`openjiuwen.extensions.store` 提供可选的存储扩展实现，包括向量存储和关系型数据库存储，支持 Elasticsearch 和 GaussDB 的集成。

## store.vector

`openjiuwen.extensions.store.vector` 提供基于 Elasticsearch 的向量存储扩展实现，使用 Elasticsearch 的 `dense_vector` 字段类型和 k-NN 搜索提供向量相似度搜索功能。

**模块**：

| 模块 | 说明 |
|---|---|
| [es_vector_store](./store/es_vector_store.md) | 基于 Elasticsearch 的向量存储实现，继承 `BaseVectorStore`，使用 `AsyncElasticsearch` 进行向量增删改查和 k-NN 相似度搜索。 |

## store.db

`openjiuwen.extensions.store.db` 提供基于 GaussDB 的关系型数据库存储扩展实现，通过 `async_gaussdb` 异步驱动与 GaussDB 数据库交互。

**模块**：

| 模块 | 说明 |
|---|---|
| [gauss_db_store](./store/gauss_db_store.md) | 基于 GaussDB 的数据库存储实现，继承 `BaseDbStore`，封装 `AsyncEngine` 进行异步数据库操作。 |
