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

**函数与常量**：

| 名称 | DESCRIPTION |
|------|-------------|
| **create_vector_store** | 向量存储工厂；解析顺序：built-in → `register_vector_store` 注册 → `openjiuwen.vector_stores` entry_points。 |
| **register_vector_store** | 程序化注册第三方向量存储实现（适合私有后端，不走 PyPI entry_points 的场景）。 |
| **VECTOR_STORE_ENTRY_POINT_GROUP** | 第三方向量存储插件所需的 Python entry_points group 名，稳定公共常量。 |

> 第三方插件开发指引见[插件开发-存储后端](../../高阶用法/插件开发-存储后端.md)。

**graph**（图存储）：

| 文档 | DESCRIPTION |
|------|-------------|
| [graph](./store/graph/README.md) | 图结构向量存储：GraphStore 协议、Entity/Relation/Episode、MilvusGraphStore、配置与常量。 |