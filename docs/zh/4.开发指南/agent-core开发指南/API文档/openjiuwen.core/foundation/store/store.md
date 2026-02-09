# openjiuwen.core.foundation.store

`openjiuwen.core.foundation.store` 提供 KV 存储与数据库存储的**抽象基类及内置实现**，供框架内记忆、会话等模块复用：

- 定义 `BaseKVStore` 键值存储抽象接口（set、get、exists、delete、前缀查询、批量 mget 等）；
- 定义 `BaseDbStore` 数据库存储抽象接口（获取异步 Engine）；
- 提供 `InMemoryKVStore`（内存实现）、`DbBasedKVStore`（基于 SQLAlchemy 的实现）、`DefaultDbStore`（BaseDbStore 默认实现）。

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

对应源码：`openjiuwen.core.foundation.store.in_memory_kv_store.InMemoryKVStore`。

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

对应源码：`openjiuwen.core.foundation.store.db_based_kv_store.DbBasedKVStore`。

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

对应源码：`openjiuwen.core.foundation.store.default_db_store.DefaultDbStore`。

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


asyncio.run(demo_kv_store())
asyncio.run(demo_db_store())
asyncio.run(demo_default_db_store())
```

> **说明**：`InMemoryKVStore` 适用于单进程、无需持久化的场景；`DbBasedKVStore` 适用于需要持久化、多进程共享或与记忆/会话模块集成的场景；`DefaultDbStore` 通常与 `LongTermMemory.register_store(db_store=...)` 等配合使用。
