# openjiuwen.extensions.store.db.gauss_db_store

## 设计方案

### 架构设计原则

`GaussDbStore` 的设计遵循以下核心原则：

1. **最小化依赖**: 只依赖标准 SQLAlchemy 接口，避免与特定数据库强耦合
2. **隔离性**: 不污染全局命名空间，确保与其他数据库驱动兼容
3. **扩展性**: 基于 SQLAlchemy 方言机制，易于维护和升级
4. **标准兼容**: 遵循 Python 和 SQLAlchemy 最佳实践

### 方言实现方案

#### 为什么使用 `import_dbapi()` 而非猴子补丁？

**旧方案（猴子补丁）的问题**：
```python
# 旧实现：全局污染 sys.modules
def _apply_monkey_patch():
    import async_gaussdb
    sys.modules['asyncpg'] = async_gaussdb  # 全局污染
    sys.modules['asyncpg.exceptions'] = async_gaussdb.exceptions
```

**缺点**：
- ❌ **全局污染**: 修改 `sys.modules`，影响所有后续代码
- ❌ **不可预测**: 其他使用真正 `asyncpg` 的代码会受到影响
- ❌ **不符合架构**: 违背 SQLAlchemy 官方推荐的方言实现方式
- ❌ **难以调试**: 隐式的全局状态修改难以追踪

**新方案（import_dbapi）的优势**：
```python
# 新实现：局部加载驱动
class GaussDialect_asyncpg(PGDialect_asyncpg):
    @classmethod
    def import_dbapi(cls):
        import async_gaussdb
        from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi
        return AsyncAdapt_asyncpg_dbapi(async_gaussdb)
```

**优点**：
- ✅ **无全局污染**: 不修改 `sys.modules`，完全隔离
- ✅ **符合规范**: 使用 SQLAlchemy 官方提供的扩展点
- ✅ **易于维护**: 代码结构清晰，职责明确
- ✅ **类型安全**: 使用 `AsyncAdapt_asyncpg_dbapi` 包装器确保类型兼容

#### 为什么要继承 `BaseDbStore` 而不是 `DefaultDbStore`？

虽然 `GaussDbStore` 和 `DefaultDbStore` 的当前实现几乎相同，但继承 `BaseDbStore` 的设计考虑如下：

**架构层面**：

1. **语义清晰性**
   - `BaseDbStore` 是抽象基类，定义统一的存储接口
   - `GaussDbStore` 明确标识这是 GaussDB 专用实现
   - 便于在代码中区分不同数据库的存储实现

2. **模块隔离**
   - `DefaultDbStore` 位于 `openjiuwen.core.foundation.store.db`（核心模块）
   - `GaussDbStore` 位于 `openjiuwen.extensions.store.db`（扩展模块）
   - 保持核心模块的纯净，将特定数据库实现放在扩展模块

3. **未来扩展性**
   - `GaussDbStore` 可以在未来添加 GaussDB 特定功能
   - 例如：GaussDB 特有的连接池配置、监控指标、性能优化等
   - 不会影响核心模块的稳定性

 4. **依赖管理**
    - `DefaultDbStore` 随核心模块一起发布
    - `GaussDbStore` 作为可选扩展，通过 `pip install openjiuwen[gaussdb]` 或 `pip install openjiuwen[all-storage]` 安装
    - 符合"核心最小化，扩展可选化"的设计理念

### 使用限制

#### 支持的功能

✅ **标准 SQLAlchemy 操作**
- 基本的 CRUD 操作（创建、读取、更新、删除）
- 事务管理
- 连接池管理
- 异步查询

✅ **PostgreSQL 兼容语法**
- `GaussDialect_asyncpg` 继承自 `PGDialect_asyncpg`
- 支持大部分 PostgreSQL 数据类型和函数
- 适用于标准 SQL 查询和事务操作

#### 不支持的功能

❌ **GaussDB 特有高阶语法**
- 不支持 GaussDB 特有的存储过程和函数调用
- 不支持 GaussDB 特有的数据类型扩展
- 不支持 GaussDB 专有的性能优化特性（如特定的索引策略）

❌ **asyncpg 特性**
- 由于使用 `async_gaussdb` 驱动，不支持 `asyncpg` 的特定功能
- 不支持 `asyncpg` 的自定义类型编解码器
- 不支持 `asyncpg` 的通知/监听机制

❌ **高级连接特性**
- 不支持服务端游标的某些高级用法
- 不支持 PostgreSQL 特有的复制功能
- 不支持 PostgreSQL 的 LISTEN/NOTIFY 机制

#### 兼容性说明

**完全兼容的场景**：
- 标准 ORM 操作（SQLAlchemy Core 和 ORM）
- 基本的数据类型（INTEGER, VARCHAR, TEXT, TIMESTAMP 等）
- 标准事务和连接管理
- 简单的查询和更新操作

**可能需要适配的场景**：
- 使用 PostgreSQL 特有函数的复杂查询
- 需要数据库特定优化的场景
- 使用高级并发控制（如行级锁的高级用法）

### 技术实现细节

#### 驱动补丁机制

由于 `async_gaussdb` 缺少部分 DBAPI 标准属性，需要补齐：

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

**原因**：确保 `async_gaussdb` 符合 Python DB-API 2.0 规范，使 SQLAlchemy 能正确识别和使用该驱动。

#### 异步适配器

```python
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi

return AsyncAdapt_asyncpg_dbapi(patched_driver)
```

**原因**：`AsyncAdapt_asyncpg_dbapi` 提供了 SQLAlchemy 需要的异步连接适配层，包括 `await_()` 等方法，确保异步操作能正常工作。

### 最佳实践建议

1. **使用标准 SQLAlchemy 接口**
   ```python
   # 推荐：使用标准 ORM 操作
   async with async_session() as session:
       user = User(name="Alice")
       session.add(user)
       await session.commit()
   ```

2. **避免使用数据库特定语法**
   ```python
   # 避免：使用 PostgreSQL 特有函数
   # stmt = select(User).where(User.created_at > func.now())
   
   # 推荐：使用标准 SQL 函数
   from sqlalchemy import func
   stmt = select(User).where(User.created_at > func.current_timestamp())
   ```

3. **处理可选依赖**
   ```python
   try:
       from openjiuwen.extensions.store.db import GaussDbStore
   except ImportError:
       # 降级到默认存储
       from openjiuwen.core.foundation.store.db import DefaultDbStore
   ```

## 调用链路

```
from openjiuwen.extensions.store.db import GaussDbStore
  │
  ▼
gauss_db_store.py → import gauss_dialect（模块加载，仅执行一次）
  │
  ▼
gauss_dialect.py 模块加载时自动完成：
  ├── registry.register("gaussdb", ...)   # 注册 GaussDialect_asyncpg 到 SQLAlchemy
  └── GaussDialect_asyncpg.import_dbapi() # 局部加载 async_gaussdb 驱动
  │
  ▼
GaussDbStore(async_conn=engine)           # 创建存储实例
  │
  ▼
store.get_async_engine()                  # 获取 AsyncEngine → 通过 SQLAlchemy 执行数据库操作
```

> Python 模块缓存机制保证 `gauss_dialect.py` 中的初始化逻辑只执行一次，无需用户手动调用。
> 新实现使用 `import_dbapi()` 方法局部加载驱动，不会污染全局 `sys.modules`。

## class openjiuwen.extensions.store.db.gauss_db_store.GaussDbStore

```python
class openjiuwen.extensions.store.db.gauss_db_store.GaussDbStore(async_conn: AsyncEngine)
```

GaussDB 数据库存储实现，继承自 `BaseDbStore`。封装 SQLAlchemy `AsyncEngine`，上层业务通过 `get_async_engine()` 获取引擎后即可使用标准 SQLAlchemy 异步接口执行增删改查操作。

导入此模块时会自动触发 `gauss_dialect` 注册（通过 `import_dbapi()` 局部加载驱动），无需用户手动调用。

**参数**：

- **async_conn**(AsyncEngine)：SQLAlchemy 异步引擎实例。

**样例**：

```python
>>> from sqlalchemy.ext.asyncio import create_async_engine
>>> from openjiuwen.extensions.store.db import GaussDbStore
>>>
>>> engine = create_async_engine("gaussdb+async_gaussdb://user:password@host:port/database")
>>> store = GaussDbStore(async_conn=engine)
>>> # 通过 store.get_async_engine() 获取引擎进行数据库操作
```

### get_async_engine

```python
get_async_engine() -> AsyncEngine
```

返回内部持有的 `AsyncEngine` 实例。

**返回**：

**AsyncEngine**：SQLAlchemy 异步引擎实例。多次调用返回同一个实例。

**样例**：

```python
>>> engine = store.get_async_engine()
>>> assert engine is store.get_async_engine()  # 同一实例
```

### async_conn

```python
async_conn: AsyncEngine
```

内部持有的 `AsyncEngine` 实例属性。

## 与记忆引擎集成

`GaussDbStore` 可作为 `LongTermMemory` 的 `db_store` 参数使用，替代默认的 `DefaultDbStore`：

**样例**：

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

## 完整的增删改查代码示例

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

    # 建表
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 插入
    async with async_session() as session:
        product = Product(name="智能手机", price=6999.00, category="电子产品", stock=50)
        session.add(product)
        await session.commit()
        await session.refresh(product)
        print(f"插入成功: ID={product.id}")

    # 查询
    async with async_session() as session:
        result = await session.execute(select(Product).where(Product.name == "智能手机"))
        p = result.scalar_one()
        print(f"查询结果: {p.name}, Price={p.price}")

    # 更新
    async with async_session() as session:
        stmt = update(Product).where(Product.name == "智能手机").values(price=5999.00)
        await session.execute(stmt)
        await session.commit()

    # 删除
    async with async_session() as session:
        stmt = delete(Product).where(Product.name == "智能手机")
        await session.execute(stmt)
        await session.commit()

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
```

## 连接字符串格式

```
gaussdb+async_gaussdb://username:password@host:port/database
```

## 依赖

| 依赖包 | 版本要求 | 说明 |
|--------|----------|------|
| `sqlalchemy` | >= 2.0.41 | 已在项目主依赖中 |
| `async-gaussdb` | ~= 0.30.0 | GaussDB 异步驱动（0.30.x 兼容），作为可选依赖 |
| `asyncpg` | >= 0.29.0 | PostgreSQL 异步驱动，SQLAlchemy `PGDialect_asyncpg` 在运行时需要此依赖用于错误翻译（`_asyncpg_error_translate`）及异步连接适配 |

### 为什么需要 asyncpg

虽然 `GaussDbStore` 使用 `async_gaussdb` 作为主驱动，但 `asyncpg` 仍然是必需的运行时依赖，原因如下：

1. **SQLAlchemy asyncpg 方言基础设施**：`GaussDialectAsyncpg` 继承自 `PGDialect_asyncpg`，复用了 SQLAlchemy 的 asyncpg 适配层（`AsyncAdapt_asyncpg_dbapi`）。SQLAlchemy asyncpg 方言中的错误翻译机制（`_asyncpg_error_translate`）在运行时引用了 `asyncpg.exceptions.*`。

2. **运行时错误处理**：当查询执行过程中发生数据库异常时，SQLAlchemy 的 `AsyncAdapt_asyncpg_connection._handle_exception` 会访问 `_asyncpg_error_translate`，其中直接执行 `import asyncpg`。如果未安装 `asyncpg`，在错误处理阶段将导致 `ImportError`。

3. **连接适配层**：`AsyncAdapt_asyncpg_dbapi` 提供了 SQLAlchemy 所需的异步连接适配层，内部依赖 asyncpg 兼容接口。

### 安装方式

```bash
# 方式一：安装 GaussDB 可选依赖（已包含 asyncpg）
pip install openjiuwen[gaussdb]

# 方式二：安装所有存储依赖（推荐）
pip install openjiuwen[all-storage]

# 方式三：单独安装驱动
# 方式三：单独安装驱动
pip install async-gaussdb asyncpg
```
