# openjiuwen.extensions.checkpointer.redis

## class openjiuwen.extensions.checkpointer.redis.RedisCheckpointer

```python
class openjiuwen.extensions.checkpointer.redis.RedisCheckpointer(redis_store: RedisStore, ttl: Optional[dict[str, Any]] = None)
```

基于 Redis 的检查点实现，支持独立 Redis 和 Redis 集群模式。所有 Redis 操作通过 `RedisStore` 接口进行，不直接使用 Redis 客户端 API。

**参数**：

- **redis_store**(RedisStore)：Redis 存储对象，用于所有 Redis 操作。
- **ttl**(Optional[dict[str, Any]], 可选)：TTL 配置字典。默认值：`None`。

**样例**：

```python
>>> from openjiuwen.extensions.checkpointer.redis import RedisCheckpointer
>>> from openjiuwen.extensions.store.kv import RedisStore
>>> from redis.asyncio import Redis
>>> 
>>> redis_client = Redis.from_url("redis://localhost:6379")
>>> redis_store = RedisStore(redis_client)
>>> checkpointer = RedisCheckpointer(redis_store)
>>> # 使用 checkpointer 进行状态管理
```

### pre_agent_execute

```python
async pre_agent_execute(session: BaseSession, inputs)
```

在 Agent 执行前调用，用于恢复 Agent 状态。

**参数**：

- **session**(BaseSession)：Agent 会话对象。
- **inputs**：Agent 输入。如果提供，会将其添加到会话状态中。

### interrupt_agent_execute

```python
async interrupt_agent_execute(session: BaseSession)
```

当 Agent 需要中断等待用户交互时调用，用于保存 Agent 状态。

**参数**：

- **session**(BaseSession)：Agent 会话对象。

### post_agent_execute

```python
async post_agent_execute(session: BaseSession)
```

在 Agent 执行完成后调用，用于保存 Agent 状态。

**参数**：

- **session**(BaseSession)：Agent 会话对象。

### pre_workflow_execute

```python
async pre_workflow_execute(session: BaseSession, inputs: InteractiveInput)
```

在工作流执行前调用，用于恢复或清理工作流状态。

**参数**：

- **session**(BaseSession)：工作流会话对象。
- **inputs**(InteractiveInput)：工作流输入。如果为 `InteractiveInput` 类型，则恢复工作流状态；否则检查是否存在状态，如果存在且未启用强制删除，则抛出异常。

### post_workflow_execute

```python
async post_workflow_execute(session: BaseSession, result, exception)
```

在工作流执行后调用，用于保存或清理工作流状态。

**参数**：

- **session**(BaseSession)：工作流会话对象。
- **result**：工作流执行结果。
- **exception**：如果工作流执行过程中发生异常，则传入异常对象；否则为 `None`。

### session_exists

```python
async session_exists(session_id: str) -> bool
```

检查指定会话 ID 是否存在。

**参数**：

- **session_id**(str)：会话 ID。

**返回**：

**bool**：如果会话存在则返回 `True`，否则返回 `False`。

### release

```python
async release(session_id: str, agent_id: Optional[str] = None)
```

释放指定会话的资源。如果提供了 `agent_id`，则只释放该 Agent 的资源；否则释放整个会话的所有资源。

**参数**：

- **session_id**(str)：会话 ID。
- **agent_id**(Optional[str], 可选)：Agent ID。默认值：`None`。

### graph_store

```python
graph_store() -> Store
```

获取图状态存储对象。

**返回**：

**Store**：图状态存储对象。

## class openjiuwen.extensions.checkpointer.redis.RedisCheckpointerProvider

```python
class openjiuwen.extensions.checkpointer.redis.RedisCheckpointerProvider
```

Redis 检查点提供者，用于创建基于 Redis 的检查点实例。

支持独立 Redis 和 Redis 集群模式，使用结构化配置并自动验证。

### create

```python
async create(conf: dict) -> Checkpointer
```

创建 RedisCheckpointer 实例。

**参数**：

- **conf**(dict)：配置字典，包含 `connection` 和可选的 `ttl` 键。`connection` 字典必须包含 `redis_client` 或 `url`。

**返回**：

**Checkpointer**：RedisCheckpointer 实例。

**配置格式**：

```python
{
    "connection": {
        "redis_client": Redis(...),  # 可选：预配置的客户端
        "url": "redis://...",  # 如果未提供 redis_client 则必需
        "cluster_mode": True,  # 可选：从 URL 自动检测（如果为 None）
        "connection_args": {...}  # 可选：额外的连接参数
    },
    "ttl": {  # 可选
        "default_ttl": 5,  # 可选：TTL（分钟）
        "refresh_on_read": True  # 可选：读取时刷新 TTL
    }
}
```

**样例**：

```python
>>> from openjiuwen.core.session.checkpointer import CheckpointerFactory, CheckpointerConfig
>>> 
>>> # 独立 Redis
>>> conf = {
...     "connection": {"url": "redis://localhost:6379"}
... }
>>> config = CheckpointerConfig(type="redis", conf=conf)
>>> checkpointer = await CheckpointerFactory.create(config)
>>> 
>>> # 集群模式
>>> conf = {
...     "connection": {
...         "url": "redis://localhost:7000",
...         "cluster_mode": True
...     }
... }
>>> config = CheckpointerConfig(type="redis", conf=conf)
>>> checkpointer = await CheckpointerFactory.create(config)
>>> 
>>> # 带 TTL
>>> conf = {
...     "connection": {"url": "redis://localhost:6379"},
...     "ttl": {"default_ttl": 5, "refresh_on_read": True}
... }
>>> config = CheckpointerConfig(type="redis", conf=conf)
>>> checkpointer = await CheckpointerFactory.create(config)
```

## class openjiuwen.extensions.checkpointer.redis.RedisCheckpointerConfig

```python
class openjiuwen.extensions.checkpointer.redis.RedisCheckpointerConfig(connection: RedisConnectionConfig, ttl: Optional[RedisTTLConfig] = None)
```

Redis 检查点的完整配置类。

提供结构化、类型安全的 Redis 检查点配置，支持自动验证和合理的默认值。

**参数**：

- **connection**(RedisConnectionConfig)：Redis 连接配置。
- **ttl**(Optional[RedisTTLConfig], 可选)：存储数据的 TTL 配置。默认值：`None`。

**样例**：

```python
>>> from openjiuwen.extensions.checkpointer.redis import (
...     RedisCheckpointerConfig,
...     RedisConnectionConfig,
...     RedisTTLConfig
... )
>>> 
>>> # 最小配置（独立 Redis）
>>> config = RedisCheckpointerConfig(
...     connection=RedisConnectionConfig(url="redis://localhost:6379")
... )
>>> 
>>> # 带 TTL 配置
>>> config = RedisCheckpointerConfig(
...     connection=RedisConnectionConfig(url="redis://localhost:6379"),
...     ttl=RedisTTLConfig(default_ttl=5, refresh_on_read=True)
... )
>>> 
>>> # 集群模式
>>> config = RedisCheckpointerConfig(
...     connection=RedisConnectionConfig(
...         url="redis://localhost:7000",
...         cluster_mode=True
...     )
... )
>>> 
>>> # 使用预配置客户端
>>> from redis.asyncio import Redis
>>> redis_client = Redis.from_url("redis://localhost:6379")
>>> config = RedisCheckpointerConfig(
...     connection=RedisConnectionConfig(redis_client=redis_client),
...     ttl=RedisTTLConfig(default_ttl=10)
... )
```

## class openjiuwen.extensions.checkpointer.redis.RedisConnectionConfig

```python
class openjiuwen.extensions.checkpointer.redis.RedisConnectionConfig(redis_client: Optional[Union[Redis, RedisCluster]] = None, url: Optional[str] = None, cluster_mode: Optional[bool] = None, connection_args: dict[str, Any] = {})
```

Redis 连接配置类。

提供结构化方式配置 Redis 连接，支持验证和类型安全。

**参数**：

- **redis_client**(Optional[Union[Redis, RedisCluster]], 可选)：预配置的 Redis 或 RedisCluster 客户端实例。如果提供，其他连接参数将被忽略。默认值：`None`。
- **url**(Optional[str], 可选)：Redis 连接 URL。可以是独立 URL（`redis://`）或集群 URL（`redis+cluster://` 或 `rediss+cluster://`）。默认值：`None`。
- **cluster_mode**(Optional[bool], 可选)：显式启用/禁用集群模式。如果为 `None`，则从 URL 方案自动检测。默认值：`None`。
- **connection_args**(dict[str, Any], 可选)：传递给 Redis 客户端的额外连接参数。默认值：`{}`。

**样例**：

```python
>>> from openjiuwen.extensions.checkpointer.redis import RedisConnectionConfig
>>> 
>>> # 独立 Redis
>>> config = RedisConnectionConfig(url="redis://localhost:6379")
>>> 
>>> # 带显式标志的集群模式
>>> config = RedisConnectionConfig(
...     url="redis://localhost:7000",
...     cluster_mode=True
... )
>>> 
>>> # 使用 URL 方案的集群模式
>>> config = RedisConnectionConfig(url="redis+cluster://localhost:7000")
>>> 
>>> # 使用预配置客户端
>>> from redis.asyncio import Redis
>>> redis_client = Redis.from_url("redis://localhost:6379")
>>> config = RedisConnectionConfig(redis_client=redis_client)
```

### is_cluster_mode

```python
is_cluster_mode() -> bool
```

确定是否应使用集群模式。

**返回**：

**bool**：如果应使用集群模式则返回 `True`，否则返回 `False`。

### get_connection_url

```python
get_connection_url() -> Optional[str]
```

获取连接 URL，如果需要则规范化集群 URL。

**返回**：

**Optional[str]**：规范化后的连接 URL。

## class openjiuwen.extensions.checkpointer.redis.RedisTTLConfig

```python
class openjiuwen.extensions.checkpointer.redis.RedisTTLConfig(default_ttl: Optional[float] = None, refresh_on_read: bool = False)
```

Redis 存储数据的 TTL（生存时间）配置。

**参数**：

- **default_ttl**(Optional[float], 可选)：存储数据的默认 TTL（分钟）。如果设置，所有存储的数据都将具有此过期时间。默认值：`None`。
- **refresh_on_read**(bool, 可选)：如果为 `True`，读取数据时将刷新 TTL。这会延长频繁访问的数据的生存时间。默认值：`False`。
