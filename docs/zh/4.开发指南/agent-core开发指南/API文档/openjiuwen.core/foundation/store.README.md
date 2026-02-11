# store

`openjiuwen.core.foundation.store`提供了openJiuwen的存储抽象模块。

**详细 API 文档**：[store.md](./store/store.md)

**Classes**：

| CLASS | DESCRIPTION |
|-------|-------------|
| **BaseKVStore** | KV存储抽象基类。 |
| **BaseDbStore** | 数据库存储抽象基类。 |
| **BaseObjectStorageClient** | 对象存储客户端抽象基类。 |
| **InMemoryKVStore** | 内存KV存储实现。 |
| **DbBasedKVStore** | 基于数据库的KV存储实现。 |
| **DefaultDbStore** | 默认数据库存储实现。 |
| **AioBotoClient** | 基于 aioboto3 的异步 S3 客户端实现。 |