# openjiuwen.core.foundation.store

`openjiuwen.core.foundation.store` provides **abstract base classes and built-in implementations** for KV storage and database storage, for reuse by modules such as memory and session within the framework:

- Defines `BaseKVStore` key-value storage abstract interface (set, get, exists, delete, prefix queries, batch mget, etc.);
- Defines `BaseDbStore` database storage abstract interface (get async Engine);
- Provides `InMemoryKVStore` (in-memory implementation), `DbBasedKVStore` (SQLAlchemy-based implementation), `DefaultDbStore` (BaseDbStore default implementation).

Corresponding source code: `openjiuwen.core.foundation.store`.

## class BaseKVStore

```python
class openjiuwen.core.foundation.store.base_kv_store.BaseKVStore(ABC)
```

KV storage abstract base class, defining a unified key-value storage interface. All KV implementations must inherit from this class and implement its abstract methods.

Corresponding source code: `openjiuwen.core.foundation.store.base_kv_store.BaseKVStore`.

### abstractmethod async set

```python
async def set(key: str, value: str)
```

Set key-value pair; if key already exists, overwrite it.

**Parameters**:

- `key: str`: Key, unique identifier. Default value: none.
- `value: str`: Value, string payload. Default value: none.

### abstractmethod async exclusive_set

```python
async def exclusive_set(key: str, value: str, expiry: int | None = None) -> bool
```

Atomically set key-value pair, only when the key does not exist or has expired.

**Parameters**:

- `key: str`: Key. Default value: none.
- `value: str`: Value. Default value: none.
- `expiry: int | None`: Key-value pair expiration time (seconds), optional. Default value: `None`.

**Returns**:

- `bool`: Returns `True` on successful set; returns `False` if key already exists and has not expired.

### abstractmethod async get

```python
async def get(key: str) -> str | None
```

Get value by key; returns `None` when key does not exist.

**Parameters**:

- `key: str`: Key. Default value: none.

**Returns**:

- `str | None`: Value corresponding to key, `None` if it doesn't exist.

### abstractmethod async exists

```python
async def exists(key: str) -> bool
```

Check if key exists.

**Parameters**:

- `key: str`: Key. Default value: none.

**Returns**:

- `bool`: `True` if exists, otherwise `False`.

### abstractmethod async delete

```python
async def delete(key: str)
```

Delete specified key; if key does not exist, no operation is performed.

**Parameters**:

- `key: str`: Key. Default value: none.

### abstractmethod async get_by_prefix

```python
async def get_by_prefix(prefix: str) -> dict[str, str]
```

Get all matching key-value pairs by prefix.

**Parameters**:

- `prefix: str`: Prefix string. Default value: none.

**Returns**:

- `dict[str, str]`: Dictionary of prefix-matched key-value pairs.

### abstractmethod async delete_by_prefix

```python
async def delete_by_prefix(prefix: str)
```

Delete all matching key-value pairs by prefix.

**Parameters**:

- `prefix: str`: Prefix string. Default value: none.

### abstractmethod async mget

```python
async def mget(keys: List[str]) -> List[str | None]
```

Batch get values for multiple keys; when a key does not exist, the corresponding position in the returned list is `None`.

**Parameters**:

- `keys: List[str]`: List of keys. Default value: none.

**Returns**:

- `List[str | None]`: List of values corresponding to `keys` in order, `None` where not present.

---

## class BaseDbStore

```python
class openjiuwen.core.foundation.store.base_db_store.BaseDbStore(ABC)
```

Database storage abstract base class, defining the interface for getting async DB Engine, for callers to perform async database operations (e.g., create tables, execute SQL).

Corresponding source code: `openjiuwen.core.foundation.store.base_db_store.BaseDbStore`.

### abstractmethod get_async_engine

```python
def get_async_engine(self) -> AsyncEngine
```

Return async SQLAlchemy engine instance.

**Returns**:

- `AsyncEngine`: Async SQLAlchemy engine for executing async database operations.

---

## class InMemoryKVStore

```python
class openjiuwen.core.foundation.store.in_memory_kv_store.InMemoryKVStore(BaseKVStore)
```

In-memory KV storage implementation, implementing all interfaces of `BaseKVStore`; supports `exclusive_set`'s `expiry` expiration time, expired keys are treated as non-existent during `get` (not automatically deleted).

Corresponding source code: `openjiuwen.core.foundation.store.in_memory_kv_store.InMemoryKVStore`.

```python
InMemoryKVStore()
```

Parameterless constructor; internally uses dictionary and `asyncio.Lock` to ensure concurrency safety.

**Behavior**:

1. Initialize internal dictionary `_store: dict[str, tuple[str, Optional[int]]]` (value and expiration timestamp);
2. Initialize `_lock = asyncio.Lock()`;
3. Implement `BaseKVStore`'s set, get, delete, exclusive_set, exists, get_by_prefix, delete_by_prefix, mget; expired keys return `None` during get, not automatically removed from dictionary.

---

## class DbBasedKVStore

```python
class openjiuwen.core.foundation.store.db_based_kv_store.DbBasedKVStore(BaseKVStore)
```

SQLAlchemy async engine-based KV storage implementation, using table `kv_store` (key, value columns); automatically creates table on first call to any interface.

Corresponding source code: `openjiuwen.core.foundation.store.db_based_kv_store.DbBasedKVStore`.

```python
DbBasedKVStore(engine: AsyncEngine)
```

**Parameters**:

- `engine: AsyncEngine`: Async database engine for creating sessions and executing SQL.

**Behavior**:

1. Save `engine`, create `async_sessionmaker(engine, ..., class_=AsyncSession)`;
2. Set `table_created = False`, create `kv_store` table through `_create_table_if_not_exist()` on first call to set/get, etc.;
3. Implement all `BaseKVStore` interfaces; `exclusive_set`'s expiry is implemented by storing `{"value": ..., "expiry": ...}` in JSON within value.

---

## class DefaultDbStore

```python
class openjiuwen.core.foundation.store.default_db_store.DefaultDbStore(BaseDbStore)
```

Default implementation of `BaseDbStore`, directly holds and returns the passed `AsyncEngine`.

Corresponding source code: `openjiuwen.core.foundation.store.default_db_store.DefaultDbStore`.

```python
DefaultDbStore(async_conn: AsyncEngine)
```

**Parameters**:

- `async_conn: AsyncEngine`: Async database connection (engine).

### get_async_engine

```python
def get_async_engine(self) -> AsyncEngine
```

Return the `async_conn` passed during construction.

**Returns**:

- `AsyncEngine`: Async engine instance.

---

## Typical Usage Flow Example

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
    # 1. In-memory KV storage (no external dependencies)
    kv = InMemoryKVStore()
    await kv.set("user:001:name", "Alice")
    value = await kv.get("user:001:name")
    print(value)  # Alice

    exists = await kv.exists("user:001:name")
    print(exists)  # True

    await kv.delete("user:001:name")
    print(await kv.get("user:001:name"))  # None


async def demo_db_store():
    # 2. Database-based KV storage (requires SQLite/MySQL, etc.)
    async_engine = create_async_engine(
        "sqlite+aiosqlite:///demo.db",
        echo=False,
    )
    db_kv = DbBasedKVStore(async_engine)
    await db_kv.set("config:key1", "value1")
    result = await db_kv.get("config:key1")
    print(result)  # value1


async def demo_default_db_store():
    # 3. DefaultDbStore only exposes Engine for modules like memory engine to create tables, execute SQL
    async_engine = create_async_engine("sqlite+aiosqlite:///app.db")
    db_store = DefaultDbStore(async_engine)
    engine = db_store.get_async_engine()
    assert engine is async_engine


asyncio.run(demo_kv_store())
asyncio.run(demo_db_store())
asyncio.run(demo_default_db_store())
```

> **Note**: `InMemoryKVStore` is suitable for single-process, non-persistent scenarios; `DbBasedKVStore` is suitable for scenarios requiring persistence, multi-process sharing, or integration with memory/session modules; `DefaultDbStore` is typically used with `LongTermMemory.register_store(db_store=...)`, etc.
