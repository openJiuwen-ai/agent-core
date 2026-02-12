# openjiuwen.extensions.checkpointer.redis

## class openjiuwen.extensions.checkpointer.redis.RedisCheckpointer

```python
class openjiuwen.extensions.checkpointer.redis.RedisCheckpointer(redis_store: RedisStore, ttl: Optional[dict[str, Any]] = None)
```

Redis-based checkpoint implementation supporting both standalone Redis and Redis Cluster modes. All Redis operations are performed through the `RedisStore` interface, not directly using Redis client APIs.

**Parameters**:

- **redis_store**(RedisStore): Redis store object for all Redis operations.
- **ttl**(Optional[dict[str, Any]], optional): TTL configuration dictionary. Default: `None`.

**Example**:

```python
>>> from openjiuwen.extensions.checkpointer.redis import RedisCheckpointer
>>> from openjiuwen.extensions.store.kv import RedisStore
>>> from redis.asyncio import Redis
>>> 
>>> redis_client = Redis.from_url("redis://localhost:6379")
>>> redis_store = RedisStore(redis_client)
>>> checkpointer = RedisCheckpointer(redis_store)
>>> # Use checkpointer for state management
```

### pre_agent_execute

```python
async pre_agent_execute(session: BaseSession, inputs)
```

Called before Agent execution to restore Agent state.

**Parameters**:

- **session**(BaseSession): Agent session object.
- **inputs**: Agent input. If provided, it will be added to the session state.

### interrupt_agent_execute

```python
async interrupt_agent_execute(session: BaseSession)
```

Called when the Agent needs to interrupt and wait for user interaction to save Agent state.

**Parameters**:

- **session**(BaseSession): Agent session object.

### post_agent_execute

```python
async post_agent_execute(session: BaseSession)
```

Called after Agent execution completes to save Agent state.

**Parameters**:

- **session**(BaseSession): Agent session object.

### pre_workflow_execute

```python
async pre_workflow_execute(session: BaseSession, inputs: InteractiveInput)
```

Called before workflow execution to restore or clear workflow state.

**Parameters**:

- **session**(BaseSession): Workflow session object.
- **inputs**(InteractiveInput): Workflow input. If it is of type `InteractiveInput`, restore workflow state; otherwise, check if state exists, and if it exists and forced deletion is not enabled, raise an exception.

### post_workflow_execute

```python
async post_workflow_execute(session: BaseSession, result, exception)
```

Called after workflow execution to save or clear workflow state.

**Parameters**:

- **session**(BaseSession): Workflow session object.
- **result**: Workflow execution result.
- **exception**: If an exception occurred during workflow execution, pass the exception object; otherwise `None`.

### session_exists

```python
async session_exists(session_id: str) -> bool
```

Check if the specified session ID exists.

**Parameters**:

- **session_id**(str): Session ID.

**Returns**:

**bool**: Returns `True` if the session exists, otherwise `False`.

### release

```python
async release(session_id: str, agent_id: Optional[str] = None)
```

Release resources for the specified session. If `agent_id` is provided, only release resources for that Agent; otherwise release all resources for the entire session.

**Parameters**:

- **session_id**(str): Session ID.
- **agent_id**(Optional[str], optional): Agent ID. Default: `None`.

### graph_store

```python
graph_store() -> Store
```

Get the graph state store object.

**Returns**:

**Store**: Graph state store object.

## class openjiuwen.extensions.checkpointer.redis.RedisCheckpointerProvider

```python
class openjiuwen.extensions.checkpointer.redis.RedisCheckpointerProvider
```

Redis checkpoint provider for creating Redis-based checkpoint instances.

Supports both standalone Redis and Redis Cluster modes, using structured configuration with automatic validation.

### create

```python
async create(conf: dict) -> Checkpointer
```

Create a RedisCheckpointer instance.

**Parameters**:

- **conf**(dict): Configuration dictionary containing `connection` and optional `ttl` keys. The `connection` dict must contain either `redis_client` or `url`.

**Returns**:

**Checkpointer**: RedisCheckpointer instance.

**Configuration Format**:

```python
{
    "connection": {
        "redis_client": Redis(...),  # Optional: Pre-configured client
        "url": "redis://...",  # Required if redis_client not provided
        "cluster_mode": True,  # Optional: Auto-detected from URL if None
        "connection_args": {...}  # Optional: Additional connection args
    },
    "ttl": {  # Optional
        "default_ttl": 5,  # Optional: TTL in minutes
        "refresh_on_read": True  # Optional: Refresh TTL on read
    }
}
```

**Example**:

```python
>>> from openjiuwen.core.session.checkpointer import CheckpointerFactory, CheckpointerConfig
>>> 
>>> # Standalone Redis
>>> conf = {
...     "connection": {"url": "redis://localhost:6379"}
... }
>>> config = CheckpointerConfig(type="redis", conf=conf)
>>> checkpointer = await CheckpointerFactory.create(config)
>>> 
>>> # Cluster mode
>>> conf = {
...     "connection": {
...         "url": "redis://localhost:7000",
...         "cluster_mode": True
...     }
... }
>>> config = CheckpointerConfig(type="redis", conf=conf)
>>> checkpointer = await CheckpointerFactory.create(config)
>>> 
>>> # With TTL
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

Complete configuration class for Redis checkpointer.

Provides structured, type-safe Redis checkpointer configuration with automatic validation and sensible defaults.

**Parameters**:

- **connection**(RedisConnectionConfig): Redis connection configuration.
- **ttl**(Optional[RedisTTLConfig], optional): TTL configuration for stored data. Default: `None`.

**Example**:

```python
>>> from openjiuwen.extensions.checkpointer.redis import (
...     RedisCheckpointerConfig,
...     RedisConnectionConfig,
...     RedisTTLConfig
... )
>>> 
>>> # Minimal configuration (standalone Redis)
>>> config = RedisCheckpointerConfig(
...     connection=RedisConnectionConfig(url="redis://localhost:6379")
... )
>>> 
>>> # With TTL configuration
>>> config = RedisCheckpointerConfig(
...     connection=RedisConnectionConfig(url="redis://localhost:6379"),
...     ttl=RedisTTLConfig(default_ttl=5, refresh_on_read=True)
... )
>>> 
>>> # Cluster mode
>>> config = RedisCheckpointerConfig(
...     connection=RedisConnectionConfig(
...         url="redis://localhost:7000",
...         cluster_mode=True
...     )
... )
>>> 
>>> # Using pre-configured client
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

Redis connection configuration class.

Provides a structured way to configure Redis connections with validation and type safety.

**Parameters**:

- **redis_client**(Optional[Union[Redis, RedisCluster]], optional): Pre-configured Redis or RedisCluster client instance. If provided, other connection parameters are ignored. Default: `None`.
- **url**(Optional[str], optional): Redis connection URL. Can be a standalone URL (`redis://`) or cluster URL (`redis+cluster://` or `rediss+cluster://`). Default: `None`.
- **cluster_mode**(Optional[bool], optional): Explicitly enable/disable cluster mode. If `None`, auto-detected from URL scheme. Default: `None`.
- **connection_args**(dict[str, Any], optional): Additional connection arguments passed to Redis client. Default: `{}`.

**Example**:

```python
>>> from openjiuwen.extensions.checkpointer.redis import RedisConnectionConfig
>>> 
>>> # Standalone Redis
>>> config = RedisConnectionConfig(url="redis://localhost:6379")
>>> 
>>> # Cluster mode with explicit flag
>>> config = RedisConnectionConfig(
...     url="redis://localhost:7000",
...     cluster_mode=True
... )
>>> 
>>> # Cluster mode with URL scheme
>>> config = RedisConnectionConfig(url="redis+cluster://localhost:7000")
>>> 
>>> # Using pre-configured client
>>> from redis.asyncio import Redis
>>> redis_client = Redis.from_url("redis://localhost:6379")
>>> config = RedisConnectionConfig(redis_client=redis_client)
```

### is_cluster_mode

```python
is_cluster_mode() -> bool
```

Determine if cluster mode should be used.

**Returns**:

**bool**: Returns `True` if cluster mode should be used, otherwise `False`.

### get_connection_url

```python
get_connection_url() -> Optional[str]
```

Get the connection URL, normalizing cluster URLs if needed.

**Returns**:

**Optional[str]**: Normalized connection URL.

## class openjiuwen.extensions.checkpointer.redis.RedisTTLConfig

```python
class openjiuwen.extensions.checkpointer.redis.RedisTTLConfig(default_ttl: Optional[float] = None, refresh_on_read: bool = False)
```

TTL (Time To Live) configuration for Redis stored data.

**Parameters**:

- **default_ttl**(Optional[float], optional): Default TTL in minutes for stored data. If set, all stored data will have this expiration time. Default: `None`.
- **refresh_on_read**(bool, optional): If `True`, TTL will be refreshed when data is read. This extends the lifetime of frequently accessed data. Default: `False`.
