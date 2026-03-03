# openjiuwen.core.foundation.store

`openjiuwen.core.foundation.store` 提供 KV 存储、数据库存储与向量存储的**抽象基类及内置实现**，供框架内记忆、会话等模块复用：

- 定义 `BaseKVStore` 键值存储抽象接口（set、get、exists、delete、前缀查询、批量 mget 等）；
- 定义 `BaseDbStore` 数据库存储抽象接口（获取异步 Engine）；
- 定义 `BaseVectorStore` 向量存储抽象接口（集合管理、文档插入、向量搜索、文档删除）；
- 提供 `InMemoryKVStore`（内存实现）、`DbBasedKVStore`（基于 SQLAlchemy 的实现）、`DefaultDbStore`（BaseDbStore 默认实现）；
- 提供 `create_vector_store()` 工厂函数用于创建向量存储实例。

对应源码：`openjiuwen.core.foundation.store`。

## class BaseKVStore

```python
class openjiuwen.core.foundation.store.base_kv_store.BaseKVStore(ABC)
```

KV 存储抽象基类，定义统一的键值存储接口，所有 KV 实现需继承此类并实现其抽象方法。

对应源码：`openjiuwen.core.foundation.store.base_kv_store.BaseKVStore`。

### abstractmethod async set

```python
async def set(key: str, value: str)
```

设置 key、value 键值对；若 key 已存在则覆盖。

**参数**：

- `key: str`：键，唯一标识。默认值：无。
- `value: str`：值，字符串负载。默认值：无。

### abstractmethod async exclusive_set

```python
async def exclusive_set(key: str, value: str, expiry: int | None = None) -> bool
```

原子性地设置 key、value，仅当该键不存在或已过期时才设置。

**参数**：

- `key: str`：键。默认值：无。
- `value: str`：值。默认值：无。
- `expiry: int | None`：键值对过期时间（秒），可选。默认值：`None`。

**返回**：

- `bool`：设置成功返回 `True`；键已存在且未过期返回 `False`。

### abstractmethod async get

```python
async def get(key: str) -> str | None
```

根据 key 获取 value；key 不存在时返回 `None`。

**参数**：

- `key: str`：键。默认值：无。

**返回**：

- `str | None`：key 对应的 value，不存在则为 `None`。

### abstractmethod async exists

```python
async def exists(key: str) -> bool
```

判断 key 是否存在。

**参数**：

- `key: str`：键。默认值：无。

**返回**：

- `bool`：存在为 `True`，否则为 `False`。

### abstractmethod async delete

```python
async def delete(key: str)
```

删除指定 key；若 key 不存在则不执行操作。

**参数**：

- `key: str`：键。默认值：无。

### abstractmethod async get_by_prefix

```python
async def get_by_prefix(prefix: str) -> dict[str, str]
```

按前缀获取所有匹配的 key-value 对。

**参数**：

- `prefix: str`：前缀字符串。默认值：无。

**返回**：

- `dict[str, str]`：前缀匹配的 key-value 字典。

### abstractmethod async delete_by_prefix

```python
async def delete_by_prefix(prefix: str)
```

按前缀删除所有匹配的 key-value 对。

**参数**：

- `prefix: str`：前缀字符串。默认值：无。

### abstractmethod async mget

```python
async def mget(keys: List[str]) -> List[str | None]
```

批量获取多个 key 的 value；某 key 不存在时，返回列表中对应位置为 `None`。

**参数**：

- `keys: List[str]`：键列表。默认值：无。

**返回**：

- `List[str | None]`：与 `keys` 顺序对应的 value 列表，不存在处为 `None`。

---

## class BaseDbStore

```python
class openjiuwen.core.foundation.store.base_db_store.BaseDbStore(ABC)
```

数据库存储抽象基类，定义获取异步 DB Engine 的接口，供调用方执行异步数据库操作（如建表、执行 SQL）。

对应源码：`openjiuwen.core.foundation.store.base_db_store.BaseDbStore`。

### abstractmethod get_async_engine

```python
def get_async_engine(self) -> AsyncEngine
```

返回异步 SQLAlchemy 引擎实例。

**返回**：

- `AsyncEngine`：异步 SQLAlchemy 引擎，用于执行异步数据库操作。

---

## class InMemoryKVStore

```python
class openjiuwen.core.foundation.store.in_memory_kv_store.InMemoryKVStore(BaseKVStore)
```

基于内存的 KV 存储实现，实现 `BaseKVStore` 全部接口；支持 `exclusive_set` 的 `expiry` 过期时间，过期键在 `get` 时视为不存在（不自动删除）。

对应源码：`openjiuwen.core.foundation.store.kv.in_memory_kv_store.InMemoryKVStore`。

```python
InMemoryKVStore()
```

无参构造；内部使用字典与 `asyncio.Lock` 保证并发安全。

**行为**：

1. 初始化内部字典 `_store: dict[str, tuple[str, Optional[int]]]`（value 与过期时间戳）；
2. 初始化 `_lock = asyncio.Lock()`；
3. 实现 `BaseKVStore` 的 set、get、delete、exclusive_set、exists、get_by_prefix、delete_by_prefix、mget；过期键在 get 时返回 `None`，不自动从字典中移除。

---

## class DbBasedKVStore

```python
class openjiuwen.core.foundation.store.db_based_kv_store.DbBasedKVStore(BaseKVStore)
```

基于 SQLAlchemy 异步引擎的 KV 存储实现，使用表 `kv_store`（key、value 列）；首次调用任意接口时自动建表。

对应源码：`openjiuwen.core.foundation.store.kv.db_based_kv_store.DbBasedKVStore`。

```python
DbBasedKVStore(engine: AsyncEngine)
```

**参数**：

- `engine: AsyncEngine`：异步数据库引擎，用于创建会话与执行 SQL。

**行为**：

1. 保存 `engine`，创建 `async_sessionmaker(engine, ..., class_=AsyncSession)`；
2. 设置 `table_created = False`，在首次调用 set/get 等时通过 `_create_table_if_not_exist()` 创建 `kv_store` 表；
3. 实现 `BaseKVStore` 全部接口；`exclusive_set` 的 expiry 通过 value 内 JSON 存储 `{"value": ..., "expiry": ...}` 实现。

---

## class DefaultDbStore

```python
class openjiuwen.core.foundation.store.default_db_store.DefaultDbStore(BaseDbStore)
```

`BaseDbStore` 的默认实现，直接持有并返回传入的 `AsyncEngine`。

对应源码：`openjiuwen.core.foundation.store.db.default_db_store.DefaultDbStore`。

```python
DefaultDbStore(async_conn: AsyncEngine)
```

**参数**：

- `async_conn: AsyncEngine`：异步数据库连接（引擎）。

### get_async_engine

```python
def get_async_engine(self) -> AsyncEngine
```

返回构造时传入的 `async_conn`。

**返回**：

- `AsyncEngine`：异步引擎实例。

---

## class BaseVectorStore

```python
class openjiuwen.core.foundation.store.base_vector_store.BaseVectorStore(ABC)
```

向量存储抽象基类，定义统一的向量存储接口，支持集合管理、文档插入、向量搜索和文档删除操作。

对应源码：`openjiuwen.core.foundation.store.base_vector_store.BaseVectorStore`。

### abstractmethod async create_collection

```python
async def create_collection(
    self,
    collection_name: str,
    schema: Union[CollectionSchema, Dict[str, Any]],
    **kwargs: Any,
) -> None
```

创建指定 schema 的新集合。

**参数**：

- `collection_name: str`：要创建的集合名称
- `schema: Union[CollectionSchema, Dict[str, Any]]`：CollectionSchema 实例或 schema 字典
- `**kwargs: Any`：额外参数
    - `distance_metric: str`：向量搜索距离度量（如 "COSINE"、"L2"、"IP"）

### abstractmethod async delete_collection

```python
async def delete_collection(self, collection_name: str, **kwargs: Any) -> None
```

按名称删除集合。

**参数**：

- `collection_name: str`：要删除的集合名称
- `**kwargs: Any`：额外参数

### abstractmethod async collection_exists

```python
async def collection_exists(self, collection_name: str, **kwargs: Any) -> bool
```

检查集合是否存在。

**参数**：

- `collection_name: str`：集合名称
- `**kwargs: Any`：额外参数

**返回**：

- `bool`：集合存在返回 `True`，否则返回 `False`

### abstractmethod async get_schema

```python
async def get_schema(self, collection_name: str, **kwargs: Any) -> CollectionSchema
```

获取集合的 schema。

**参数**：

- `collection_name: str`：集合名称
- `**kwargs: Any`：额外参数

**返回**：

- `CollectionSchema`：集合的 schema

### abstractmethod async add_docs

```python
async def add_docs(
    self,
    collection_name: str,
    docs: List[Dict[str, Any]],
    **kwargs: Any,
) -> None
```

向集合添加文档。

**参数**：

- `collection_name: str`：目标集合名称
- `docs: List[Dict[str, Any]]`：要添加的文档列表，每个文档包含：
    - `id: str`（可选）：文档 ID
    - `embedding: List[float]`：文档向量嵌入
    - `text: str`：文档文本内容
    - `metadata: Dict[str, Any]`（可选）：额外元数据
- `**kwargs: Any`：额外参数
    - `batch_size: int`（可选）：批量插入的批次大小

### abstractmethod async search

```python
async def search(
    self,
    collection_name: str,
    query_vector: List[float],
    vector_field: str,
    top_k: int = 5,
    filters: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> List[VectorSearchResult]
```

通过向量相似度搜索最相关的文档。

**参数**：

- `collection_name: str`：要搜索的集合名称
- `query_vector: List[float]`：用于相似度搜索的查询向量
- `vector_field: str`：要搜索的向量字段名称（如 "embedding"）
- `top_k: int`：返回的最相关文档数量，默认 5
- `filters: Optional[Dict[str, Any]]`：标量字段过滤器（等值过滤），默认 `None`
- `**kwargs: Any`：额外搜索参数
    - `metric_type: str`（可选）：距离度量类型
    - `output_fields: List[str]`（可选）：返回的字段列表

**返回**：

- `List[VectorSearchResult]`：搜索结果列表，每个结果包含：
    - `score: float`：相关性得分（越高越相关）
    - `fields: Dict[str, Any]`：文档的所有字段值

### abstractmethod async delete_docs_by_ids

```python
async def delete_docs_by_ids(
    self,
    collection_name: str,
    ids: List[str],
    **kwargs: Any,
) -> None
```

按文档 ID 删除文档。

**参数**：

- `collection_name: str`：集合名称
- `ids: List[str]`：要删除的文档 ID 列表
- `**kwargs: Any`：额外参数

### abstractmethod async delete_docs_by_filters

```python
async def delete_docs_by_filters(
    self,
    collection_name: str,
    filters: Dict[str, Any],
    **kwargs: Any,
) -> None
```

按标量字段过滤器删除文档。

**参数**：

- `collection_name: str`：集合名称
- `filters: Dict[str, Any]`：标量字段过滤器（等值过滤）
- `**kwargs: Any`：额外参数

### abstractmethod async list_collection_names

```python
async def list_collection_names(self) -> List[str]
```

列出向量存储中的所有集合名称。

该方法返回当前向量存储中所有集合的名称列表。

**返回**：

- `List[str]`：集合名称列表

### abstractmethod async update_schema

```python
async def update_schema(
    self,
    collection_name: str,
    operations: List[BaseOperation],
) -> None
```

更新集合的 schema，用于向量数据迁移。

该方法应用一系列 schema 迁移操作来修改集合的结构。支持的操作包括：
- `AddScalarFieldOperation`：添加新的标量字段
- `RenameScalarFieldOperation`：重命名现有标量字段
- `UpdateScalarFieldTypeOperation`：更改标量字段的数据类型
- `UpdateEmbeddingDimensionOperation`：修改向量嵌入的维度

**参数**：

- `collection_name: str`：要修改的集合名称
- `operations: List[BaseOperation]`：要应用的迁移操作列表

### abstractmethod async get_collection_metadata

```python
async def get_collection_metadata(
    self,
    collection_name: str,
) -> Dict[str, Any]
```

获取集合的元数据信息。

该方法返回集合的元数据，包括距离度量类型、向量字段名和 schema 版本等信息。实现类应当从本地缓存或 Milvus 集合属性中获取这些信息。

**参数**：

- `collection_name: str`：集合名称

**返回**：

- `Dict[str, Any]`：集合元数据字典，包含以下键：
    - `distance_metric: str`：距离度量类型（如 "COSINE"、"L2"、"IP"）
    - `vector_field: str`：向量字段名称
    - `schema_version: int`：schema 版本号（未设置时为 0）

### abstractmethod async update_collection_metadata

```python
async def update_collection_metadata(
    self,
    collection_name: str,
    metadata: Dict[str, Any],
) -> None
```

更新集合的元数据信息。

该方法更新集合的元数据，将指定的键值对存储到 Milvus 集合属性中。所有值都会被转换为字符串存储。同时，本地缓存也会被更新以保持数据一致性。

**参数**：

- `collection_name: str`：集合名称
- `metadata: Dict[str, Any]`：要更新的元数据字典，支持以下键：
    - `schema_version: int`：schema 版本号（必须是非负整数）
    - 其他自定义属性（所有值会被转换为字符串）
---

## function create_vector_store

```python
def openjiuwen.core.foundation.store.create_vector_store(
    store_type: str,
    **kwargs: Any,
) -> BaseVectorStore | None
```

向量存储工厂函数，根据类型创建相应的向量存储实例。

**参数**：

- `store_type: str`：存储类型，支持 `"chroma"` 或 `"milvus"`
- `**kwargs: Any`：传递给具体存储实现的额外参数

**返回**：

- `BaseVectorStore | None`：向量存储实例；不支持的类型返回 `None`

---

## 典型使用流程示例

```python
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine

from openjiuwen.core.foundation.store import (
    BaseKVStore,
    BaseDbStore,
    InMemoryKVStore,
    DbBasedKVStore,
    DefaultDbStore,
    BaseVectorStore,
    CollectionSchema,
    FieldSchema,
    VectorDataType,
    create_vector_store,
)


async def demo_kv_store():
    # 1. 内存 KV 存储（无需外部依赖）
    kv = InMemoryKVStore()
    await kv.set("user:001:name", "Alice")
    value = await kv.get("user:001:name")
    print(value)  # Alice

    exists = await kv.exists("user:001:name")
    print(exists)  # True

    await kv.delete("user:001:name")
    print(await kv.get("user:001:name"))  # None


async def demo_db_store():
    # 2. 基于数据库的 KV 存储（需 SQLite/MySQL 等）
    async_engine = create_async_engine(
        "sqlite+aiosqlite:///demo.db",
        echo=False,
    )
    db_kv = DbBasedKVStore(async_engine)
    await db_kv.set("config:key1", "value1")
    result = await db_kv.get("config:key1")
    print(result)  # value1


async def demo_default_db_store():
    # 3. DefaultDbStore 仅暴露 Engine，供记忆引擎等模块建表、执行 SQL
    async_engine = create_async_engine("sqlite+aiosqlite:///app.db")
    db_store = DefaultDbStore(async_engine)
    engine = db_store.get_async_engine()
    assert engine is async_engine


async def demo_vector_store():
    # 4. 向量存储（使用 ChromaDB 或 Milvus）
    # 使用工厂函数创建 ChromaDB 向量存储
    store = create_vector_store("chroma", persist_directory="./data/chroma")

    # 定义集合 schema
    schema = CollectionSchema(
        description="文档集合",
        enable_dynamic_field=False,
    )
    schema.add_field(FieldSchema(
        name="id",
        dtype=VectorDataType.VARCHAR,
        max_length=256,
        is_primary=True,
    ))
    schema.add_field(FieldSchema(
        name="embedding",
        dtype=VectorDataType.FLOAT_VECTOR,
        dim=768,
    ))
    schema.add_field(FieldSchema(
        name="text",
        dtype=VectorDataType.VARCHAR,
        max_length=65535,
    ))
    schema.add_field(FieldSchema(
        name="metadata",
        dtype=VectorDataType.JSON,
    ))

    # 创建集合
    await store.create_collection("documents", schema, distance_metric="cosine")

    # 添加文档
    docs = [
        {
            "id": "doc1",
            "embedding": [0.1] * 768,
            "text": "这是第一篇文档",
            "metadata": {"category": "tech"},
        },
        {
            "id": "doc2",
            "embedding": [0.2] * 768,
            "text": "这是第二篇文档",
            "metadata": {"category": "news"},
        },
    ]
    await store.add_docs("documents", docs)

    # 向量搜索
    query_vector = [0.15] * 768
    results = await store.search(
        collection_name="documents",
        query_vector=query_vector,
        vector_field="embedding",
        top_k=10,
        filters={"category": "tech"},
    )

    for result in results:
        print(f"Score: {result.score:.4f}, Text: {result.fields.get('text')}")


asyncio.run(demo_kv_store())
asyncio.run(demo_db_store())
asyncio.run(demo_default_db_store())
asyncio.run(demo_vector_store())
```

> **说明**：`InMemoryKVStore` 适用于单进程、无需持久化的场景；`DbBasedKVStore` 适用于需要持久化、多进程共享或与记忆/会话模块集成的场景；`DefaultDbStore` 通常与 `LongTermMemory.register_store(db_store=...)` 等配合使用。
