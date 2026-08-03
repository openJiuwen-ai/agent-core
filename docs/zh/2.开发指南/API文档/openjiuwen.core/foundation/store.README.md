# store

`openjiuwen.core.foundation.store`提供了openJiuwen的存储抽象模块。

**详细 API 文档**：[store.md](./store/store.md)

**Classes**：

| CLASS | DESCRIPTION |
|-------|-------------|
| **BaseKVStore** | KV存储抽象基类。 |
| **BaseDbStore** | 数据库存储抽象基类。 |
| **BaseVectorStore** | 向量存储抽象基类，亦为第三方插件的稳定公共 API。 |
| **BaseObjectStorageClient** | 对象存储客户端抽象基类。 |
| **InMemoryKVStore** | 内存KV存储实现。 |
| **DbBasedKVStore** | 基于数据库的KV存储实现。 |
| **DefaultDbStore** | 默认数据库存储实现。 |
| **AioBotoClient** | 基于 aioboto3 的异步 S3 客户端实现。 |
| **BaseMemoryIndex** | 记忆索引抽象基类（增删改查、按用户/作用域批量删除、语义检索、备份与版本管理）。 |
| **MemoryDoc** | 记忆文档数据模型（id/text/type/timestamp/扩展字段）。 |
| **StorageCodec** | 记忆文本编解码协议（encode/decode，如加解密）。 |
| **SimpleMemoryIndex** | 基于 KV + 向量存储的记忆索引实现，**已弃用**，仅兼容旧版数据。 |

**函数与常量**：

| 名称 | DESCRIPTION |
|------|-------------|
| **create_vector_store** | 向量存储工厂；解析顺序：built-in → `register_vector_store` 注册 → `openjiuwen.vector_stores` entry_points。 |
| **register_vector_store** | 程序化注册第三方向量存储实现（适合私有后端，不走 PyPI entry_points 的场景）。 |
| **VECTOR_STORE_ENTRY_POINT_GROUP** | 第三方向量存储插件所需的 Python entry_points group 名，稳定公共常量。 |

> 第三方插件开发指引见[插件开发-存储后端](../../高阶用法/插件开发-存储后端.md)。

**memory index**（记忆索引）：

| 文档 | DESCRIPTION |
|------|-------------|
| [memory_index.md](./store/memory_index.md) | `BaseMemoryIndex` 抽象基类、`MemoryDoc` 数据模型、`StorageCodec` 编解码协议与弃用实现 `SimpleMemoryIndex`（旧版 KV + 向量数据布局）。 |

**graph**（图存储）：

| 文档 | DESCRIPTION |
|------|-------------|
| [graph](./store/graph/README.md) | 图结构向量存储：GraphStore 协议、Entity/Relation/Episode、MilvusGraphStore、配置与常量。 |