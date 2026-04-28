# openjiuwen.extensions.store.db.gauss_db_store

## Design Philosophy

### Architectural Principles

`GaussDbStore` is designed following these core principles:

1. **Minimal Dependencies**: Only relies on standard SQLAlchemy interfaces, avoiding strong coupling to specific databases
2. **Isolation**: Does not pollute global namespace, ensuring compatibility with other database drivers
3. **Extensibility**: Based on SQLAlchemy dialect mechanism, easy to maintain and upgrade
4. **Standard Compliance**: Follows Python and SQLAlchemy best practices

### Dialect Implementation Approach

#### Why Use `import_dbapi()` Instead of Monkey Patching?

**Problems with the Old Approach (Monkey Patching)**:
```python
# Old implementation: Global pollution of sys.modules
def _apply_monkey_patch():
    import async_gaussdb
    sys.modules['asyncpg'] = async_gaussdb  # Global pollution
    sys.modules['asyncpg.exceptions'] = async_gaussdb.exceptions
```

**Drawbacks**:
- ❌ **Global Pollution**: Modifies `sys.modules`, affecting all subsequent code
- ❌ **Unpredictable**: Other code using the real `asyncpg` will be affected
- ❌ **Non-compliant**: Violates SQLAlchemy's officially recommended dialect implementation approach
- ❌ **Hard to Debug**: Implicit global state modifications are difficult to track

**Advantages of the New Approach (import_dbapi)**:
```python
# New implementation: Local driver loading
class GaussDialect_asyncpg(PGDialect_asyncpg):
    @classmethod
    def import_dbapi(cls):
        import async_gaussdb
        from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi
        return AsyncAdapt_asyncpg_dbapi(async_gaussdb)
```

**Benefits**:
- ✅ **No Global Pollution**: Does not modify `sys.modules`, completely isolated
- ✅ **Standards Compliant**: Uses SQLAlchemy's officially provided extension point
- ✅ **Easy to Maintain**: Clear code structure with well-defined responsibilities
- ✅ **Type Safe**: Uses `AsyncAdapt_asyncpg_dbapi` wrapper to ensure type compatibility

#### Why Inherit from `BaseDbStore` Instead of `DefaultDbStore`?

Although `GaussDbStore` and `DefaultDbStore` currently have almost identical implementations, the design choice to inherit from `BaseDbStore` considers the following:

**Architectural Considerations**:

1. **Semantic Clarity**
   - `BaseDbStore` is an abstract base class defining a unified storage interface
   - `GaussDbStore` clearly identifies this as a GaussDB-specific implementation
   - Makes it easy to distinguish different database storage implementations in code

2. **Module Isolation**
   - `DefaultDbStore` is located in `openjiuwen.core.foundation.store.db` (core module)
   - `GaussDbStore` is located in `openjiuwen.extensions.store.db` (extension module)
   - Keeps the core module clean, placing specific database implementations in extension modules

3. **Future Extensibility**
   - `GaussDbStore` can add GaussDB-specific functionality in the future
   - For example: GaussDB-specific connection pool configuration, monitoring metrics, performance optimizations, etc.
   - Won't affect the stability of the core module

 4. **Dependency Management**
    - `DefaultDbStore` is shipped with the core module
    - `GaussDbStore` is an optional extension, install via `pip install openjiuwen[gaussdb]` or `pip install openjiuwen[all-storage]`
    - Aligns with the design philosophy of "minimal core, optional extensions"

### Usage Limitations

#### Supported Features

✅ **Standard SQLAlchemy Operations**
- Basic CRUD operations (Create, Read, Update, Delete)
- Transaction management
- Connection pool management
- Async queries

✅ **PostgreSQL Compatible Syntax**
- `GaussDialect_asyncpg` inherits from `PGDialect_asyncpg`
- Supports most PostgreSQL data types and functions
- Suitable for standard SQL queries and transaction operations

#### Unsupported Features

❌ **GaussDB-Specific Advanced Syntax**
- Does not support GaussDB-specific stored procedures and function calls
- Does not support GaussDB-specific data type extensions
- Does not support GaussDB proprietary performance optimization features (e.g., specific index strategies)

❌ **asyncpg Features**
- Since using `async_gaussdb` driver, `asyncpg` specific features are not supported
- Does not support `asyncpg` custom type encoders/decoders
- Does not support `asyncpg` notification/listen mechanisms

❌ **Advanced Connection Features**
- Does not support certain advanced usages of server-side cursors
- Does not support PostgreSQL-specific replication features
- Does not support PostgreSQL's LISTEN/NOTIFY mechanism

#### Compatibility Notes

**Fully Compatible Scenarios**:
- Standard ORM operations (SQLAlchemy Core and ORM)
- Basic data types (INTEGER, VARCHAR, TEXT, TIMESTAMP, etc.)
- Standard transaction and connection management
- Simple queries and update operations

**Scenarios Requiring Adaptation**:
- Complex queries using PostgreSQL-specific functions
- Scenarios requiring database-specific optimizations
- Using advanced concurrency control (e.g., advanced row-level locking)

### Technical Implementation Details

#### Driver Patching Mechanism

Since `async_gaussdb` lacks some DBAPI standard attributes, we need to patch them:

```python
def _patch_gaussdb_driver(driver_module):
    if not hasattr(driver_module, 'paramstyle'):
        driver_module.paramstyle = 'format'
    if not hasattr(driver_module, 'Error'):
        driver_module.Error = getattr(driver_module, 'GaussDBError', Exception)
    if not hasattr(driver_module, 'apilevel'):
        driver_module.apilevel = '2.0'
    if not hasattr(driver_module, 'threadsafety'):
        driver_module.threadsafety = 0
    return driver_module
```

**Reason**: Ensures `async_gaussdb` complies with Python DB-API 2.0 specification, enabling SQLAlchemy to correctly recognize and use the driver.

#### Async Adapter

```python
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi

return AsyncAdapt_asyncpg_dbapi(patched_driver)
```

**Reason**: `AsyncAdapt_asyncpg_dbapi` provides the async connection adaptation layer required by SQLAlchemy, including methods like `await_()`, ensuring async operations work correctly.

### Best Practices

1. **Use Standard SQLAlchemy Interfaces**
   ```python
   # Recommended: Use standard ORM operations
   async with async_session() as session:
       user = User(name="Alice")
       session.add(user)
       await session.commit()
   ```

2. **Avoid Database-Specific Syntax**
   ```python
   # Avoid: Using PostgreSQL-specific functions
   # stmt = select(User).where(User.created_at > func.now())
   
   # Recommended: Use standard SQL functions
   from sqlalchemy import func
   stmt = select(User).where(User.created_at > func.current_timestamp())
   ```

3. **Handle Optional Dependencies**
   ```python
   try:
       from openjiuwen.extensions.store.db import GaussDbStore
   except ImportError:
       # Fallback to default storage
       from openjiuwen.core.foundation.store.db import DefaultDbStore
   ```

## Call Chain

```
from openjiuwen.extensions.store.db import GaussDbStore
  │
  ▼
gauss_db_store.py → import gauss_dialect (module load, executed once only)
  │
  ▼
gauss_dialect.py module load automatically completes:
  ├── registry.register("gaussdb", ...)   # Register GaussDialect_asyncpg with SQLAlchemy
  └── GaussDialect_asyncpg.import_dbapi() # Load async_gaussdb driver locally
  │
  ▼
GaussDbStore(async_conn=engine)           # Create store instance
  │
  ▼
store.get_async_engine()                  # Get AsyncEngine → Execute database operations via SQLAlchemy
```

> Python's module caching mechanism ensures the initialization logic in `gauss_dialect.py` is executed only once. No manual invocation is required.
> The new implementation uses `import_dbapi()` to load the driver locally, without polluting the global `sys.modules`.

## class openjiuwen.extensions.store.db.gauss_db_store.GaussDbStore

```python
class openjiuwen.extensions.store.db.gauss_db_store.GaussDbStore(async_conn: AsyncEngine)
```

GaussDB database storage implementation, inheriting from `BaseDbStore`. Wraps a SQLAlchemy `AsyncEngine`, allowing upper-layer business logic to obtain the engine via `get_async_engine()` and perform CRUD operations using standard SQLAlchemy async interfaces.

Importing this module automatically triggers `gauss_dialect` registration (via `import_dbapi()` local driver loading). No manual invocation is required.

**Parameters**:

- **async_conn**(AsyncEngine): SQLAlchemy async engine instance.

**Example**:

```python
>>> from sqlalchemy.ext.asyncio import create_async_engine
>>> from openjiuwen.extensions.store.db import GaussDbStore
>>>
>>> engine = create_async_engine("gaussdb+async_gaussdb://user:password@host:port/database")
>>> store = GaussDbStore(async_conn=engine)
>>> # Use store.get_async_engine() to get the engine for database operations
```

### get_async_engine

```python
get_async_engine() -> AsyncEngine
```

Returns the internally held `AsyncEngine` instance.

**Returns**:

**AsyncEngine**: SQLAlchemy async engine instance. Multiple calls return the same instance.

**Example**:

```python
>>> engine = store.get_async_engine()
>>> assert engine is store.get_async_engine()  # Same instance
```

### async_conn

```python
async_conn: AsyncEngine
```

The internally held `AsyncEngine` instance attribute.

## Integration with Memory Engine

`GaussDbStore` can be used as the `db_store` parameter for `LongTermMemory`, replacing the default `DefaultDbStore`:

**Example**:

```python
>>> from openjiuwen.core.memory import LongTermMemory
>>> from openjiuwen.extensions.store.db import GaussDbStore
>>> from sqlalchemy.ext.asyncio import create_async_engine
>>>
>>> engine = create_async_engine("gaussdb+async_gaussdb://user:password@host:port/agentmgr")
>>> db_store = GaussDbStore(async_conn=engine)
>>>
>>> memory = LongTermMemory()
>>> await memory.register_store(
...     kv_store=kv_store,
...     vector_store=vector_store,
...     db_store=db_store,
... )
```

## Complete CRUD Code Example

```python
import asyncio
from datetime import datetime
from sqlalchemy import String, Integer, Float, DateTime, Text, select, update, delete
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Mapped, mapped_column

from openjiuwen.extensions.store.db import GaussDbStore


class Base(DeclarativeBase):
    pass


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    category: Mapped[str] = mapped_column(String(50))
    stock: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


async def main():
    GAUSSDB_URL = "gaussdb+async_gaussdb://user:password@host:port/database"

    engine = create_async_engine(GAUSSDB_URL, echo=False)
    store = GaussDbStore(async_conn=engine)

    engine = store.get_async_engine()
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Insert
    async with async_session() as session:
        product = Product(name="Smartphone", price=6999.00, category="Electronics", stock=50)
        session.add(product)
        await session.commit()
        await session.refresh(product)
        print(f"Inserted: ID={product.id}")

    # Query
    async with async_session() as session:
        result = await session.execute(select(Product).where(Product.name == "Smartphone"))
        p = result.scalar_one()
        print(f"Query result: {p.name}, Price={p.price}")

    # Update
    async with async_session() as session:
        stmt = update(Product).where(Product.name == "Smartphone").values(price=5999.00)
        await session.execute(stmt)
        await session.commit()

    # Delete
    async with async_session() as session:
        stmt = delete(Product).where(Product.name == "Smartphone")
        await session.execute(stmt)
        await session.commit()

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
```

## Connection String Format

```
gaussdb+async_gaussdb://username:password@host:port/database
```

## Dependencies

| Package | Version | Description |
|---------|---------|-------------|
| `sqlalchemy` | >= 2.0.41 | Included in project main dependencies |
| `async-gaussdb` | ~= 0.30.0 | GaussDB async driver (0.30.x compatible), available as optional dependency |
| `asyncpg` | >= 0.29.0 | PostgreSQL async driver, required by SQLAlchemy's `PGDialect_asyncpg` for error translation (`_asyncpg_error_translate`) and async connection adaptation at runtime |

### Why asyncpg Is Required

Although `GaussDbStore` uses `async_gaussdb` as the primary driver, `asyncpg` is still a required runtime dependency because:

1. **SQLAlchemy asyncpg dialect infrastructure**: `GaussDialectAsyncpg` inherits from `PGDialect_asyncpg` and reuses SQLAlchemy's asyncpg adapter layer (`AsyncAdapt_asyncpg_dbapi`). The error translation mechanism in SQLAlchemy's asyncpg dialect (`_asyncpg_error_translate`) references `asyncpg.exceptions.*` at runtime when database errors occur.

2. **Runtime error handling**: When a database exception is raised during query execution, SQLAlchemy's `AsyncAdapt_asyncpg_connection._handle_exception` accesses `_asyncpg_error_translate`, which performs `import asyncpg` directly. Without `asyncpg` installed, this would cause an `ImportError` during error handling.

3. **Connection adaptation**: `AsyncAdapt_asyncpg_dbapi` provides the async connection adaptation layer required by SQLAlchemy, which relies on asyncpg-compatible interfaces internally.

### Installation

```bash
# Option 1: Install GaussDB optional dependency (includes asyncpg)
pip install openjiuwen[gaussdb]

# Option 2: Install all storage dependencies (recommended)
pip install openjiuwen[all-storage]

# Option 3: Install drivers separately
pip install async-gaussdb asyncpg
```
