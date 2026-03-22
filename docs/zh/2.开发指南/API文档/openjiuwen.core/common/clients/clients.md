# openjiuwen.core.clients

## get_client_registry()

```python
def get_client_registry() -> ClientRegistry
```

获取全局客户端注册中心实例。

**返回**：
**ClientRegistry**，全局客户端注册中心

**样例**：

```python
from openjiuwen.core.common.clients import get_client_registry

# 获取全局注册中心
registry = get_client_registry()

# 注册客户端类
class MyClient(BaseClient):
    __client_name__ = "myclient"
    __client_type__ = "custom"
    
    async def close(self) -> bool:
        return True

# 获取客户端实例
client = await registry.get_client("myclient", client_type="custom", config={"key": "value"})
```

---

## class ConnectorPoolConfig

### generate_key

```python
def generate_key(self) -> str
```

为配置生成唯一键。

**返回**：

**str**，唯一标识此配置的键（MD5 哈希）

**样例**：

```python
from openjiuwen.core.common.clients import ConnectorPoolConfig

# 创建两个相同的配置
config1 = ConnectorPoolConfig(limit=100, limit_per_host=30)
config2 = ConnectorPoolConfig(limit=100, limit_per_host=30)
config3 = ConnectorPoolConfig(limit=200, limit_per_host=50)

# 生成唯一键
key1 = config1.generate_key()  # 例如: "5f4dcc3b5aa765d61d8327deb882cf99"
key2 = config2.generate_key()  # 与 key1 相同
key3 = config3.generate_key()  # 与 key1, key2 不同

print(f"相同配置的键相等: {key1 == key2}")  # True
print(f"不同配置的键相等: {key1 == key3}")  # False
```

---

## get_connector_pool_manager()

```python
def get_connector_pool_manager() -> ConnectorPoolManager
```

获取全局连接池管理器实例。

**返回**：

**ConnectorPoolManager**，全局连接池管理器

**样例**：

```python
from openjiuwen.core.common.clients import get_connector_pool_manager
from openjiuwen.core.common.clients import ConnectorPoolConfig

# 获取管理器
manager = get_connector_pool_manager()

# 创建配置
config = ConnectorPoolConfig(limit=100, keepalive_timeout=30)

# 获取连接池
pool = await manager.get_connector_pool("default", config=config)

# 使用连接池
connector = pool.conn()

# 释放引用
await manager.release_connector_pool(config)
```

---

## class ConnectorPoolManager

### get_connector_pool

```python
async def get_connector_pool(self, connector_pool_type: str = "default", *,
                             config: Optional[ConnectorPoolConfig] = None) -> ConnectorPool
```

获取或创建连接池。

**参数**：

* **connector_pool_type** (str): 连接池类型，默认为 "default"。内置的连接池如下：
  * **default**: 基于 aiohttp.TCPConnector 实现的默认连接池，用于 aiohttp 客户端的连接管理。
  * **httpx**：基于 HTTPX 和 httpcore.AsyncConnectionPool 实现的连接池，用于 HTTPX 客户端的连接管理。
* **config** (Optional[ConnectorPoolConfig]): 可选配置

**返回**：

**ConnectorPool**，连接池实例

**样例**：

```python
from openjiuwen.core.common.clients import get_connector_pool_manager
from openjiuwen.core.common.clients import ConnectorPoolConfig
from openjiuwen.core.common.clients.llm_client import HttpXConnectorPoolConfig

manager = get_connector_pool_manager()

# 获取默认 TCP 连接池
tcp_config = ConnectorPoolConfig(limit=100, limit_per_host=30)
tcp_pool = await manager.get_connector_pool("default", config=tcp_config)

# 获取 HTTPX 连接池
httpx_config = HttpXConnectorPoolConfig(limit=100, proxy="http://proxy:8080")
httpx_pool = await manager.get_connector_pool("httpx", config=httpx_config)

# 多次获取相同配置返回同一实例
pool1 = await manager.get_connector_pool("default", config=tcp_config)  # 新创建
pool2 = await manager.get_connector_pool("default", config=tcp_config)  # 返回 pool1
print(pool1 is pool2)  # True
```

### release_connector_pool

```python
async def release_connector_pool(self, config: Optional[ConnectorPoolConfig] = None)
```

释放对连接池的引用。

**参数**：

* **config** (Optional[ConnectorPoolConfig]): 要释放的连接池配置

**样例**：

```python
from openjiuwen.core.common.clients import get_connector_pool_manager
from openjiuwen.core.common.clients import ConnectorPoolConfig

manager = get_connector_pool_manager()
config = ConnectorPoolConfig(limit=100)

# 第一次获取 - 创建新连接池，引用计数为 1
pool1 = await manager.get_connector_pool("default", config=config)

# 第二次获取 - 复用连接池，引用计数增加到 2
pool2 = await manager.get_connector_pool("default", config=config)

print(f"引用计数: {pool1.ref_count}")  # 输出: 2

# 第一次释放 - 引用计数减少到 1
await manager.release_connector_pool(config)

# 第二次释放 - 引用计数减少到 0，连接池自动关闭
await manager.release_connector_pool(config)

print(f"连接池已关闭: {pool1.closed}")  # 输出: True
```

### close_connector_pool

```python
async def close_connector_pool(self, *, config: Optional[ConnectorPoolConfig] = None,
                               force: bool = False)
```

关闭指定的连接池。

**参数**：

* **config** (Optional[ConnectorPoolConfig]): 要关闭的连接池配置
* **force** (bool): 是否强制关闭（即使存在引用）

**样例**：

```python
from openjiuwen.core.common.clients import get_connector_pool_manager
from openjiuwen.core.common.clients import ConnectorPoolConfig

manager = get_connector_pool_manager()
config = ConnectorPoolConfig(limit=100)

# 获取连接池
pool = await manager.get_connector_pool("default", config=config)

# 正常关闭 - 引用计数 > 0 时无法关闭
try:
    await manager.close_connector_pool(config=config, force=False)
except Exception as e:
    print(f"无法关闭: {e}")  # 无法关闭: 引用计数为 1

# 强制关闭 - 忽略引用计数
await manager.close_connector_pool(config=config, force=True)
print(f"连接池已强制关闭: {pool.closed}")  # True
```

### close_all

```python
async def close_all(self)
```

关闭所有连接池并关闭管理器。

**样例**：

```python
from openjiuwen.core.common.clients import get_connector_pool_manager
from openjiuwen.core.common.clients import ConnectorPoolConfig

manager = get_connector_pool_manager()

# 创建多个连接池
config1 = ConnectorPoolConfig(limit=100)
config2 = ConnectorPoolConfig(limit=200)

pool1 = await manager.get_connector_pool("default", config=config1)
pool2 = await manager.get_connector_pool("httpx", config=config2)

# 获取统计信息
stats = manager.get_stats()
print(f"关闭前连接池数量: {stats['total_connector_pools']}")  # 输出: 2

# 关闭所有连接池
await manager.close_all()

print(f"连接池1已关闭: {pool1.closed}")  # True
print(f"连接池2已关闭: {pool2.closed}")  # True
print(f"管理器已关闭: {manager._closed}")  # True
```

### get_stats

```python
def get_stats(self) -> Dict
```

获取所有连接池的统计信息。

**样例**：

```python
from openjiuwen.core.common.clients import get_connector_pool_manager
from openjiuwen.core.common.clients import ConnectorPoolConfig

manager = get_connector_pool_manager()

# 创建一些连接池
config1 = ConnectorPoolConfig(limit=100, limit_per_host=30)
config2 = ConnectorPoolConfig(limit=200, keepalive_timeout=60)

pool1 = await manager.get_connector_pool("default", config=config1)
pool2 = await manager.get_connector_pool("default", config=config2)

# 获取统计信息
stats = manager.get_stats()

print(f"总连接池数: {stats['total_connector_pools']}")
print(f"最大连接池数: {stats['max_pools']}")
print(f"管理器状态: {'已关闭' if stats['closed'] else '运行中'}")

# 遍历每个连接池的详细统计
for pool_key, pool_stats in stats['connectors'].items():
    print(f"\n连接池 {pool_key}:")
    print(f"  - 已关闭: {pool_stats['closed']}")
    print(f"  - 引用计数: {pool_stats['ref_count']}")
    print(f"  - 存活时间: {pool_stats['age']:.2f}秒")
    if 'limit' in pool_stats:
        print(f"  - 连接限制: {pool_stats['limit']}")
        print(f"  - 每主机限制: {pool_stats['limit_per_host']}")
```

---

## class ConnectorPool

```python
def __init__(config: ConnectorPoolConfig)
```

**参数**：

* **config** (ConnectorPoolConfig): 连接池配置

**样例**：

```python
from openjiuwen.core.common.clients import ConnectorPool
from openjiuwen.core.common.clients import ConnectorPoolConfig

# ConnectorPool 是抽象基类，通常不直接实例化
# 使用具体实现如 TcpConnectorPool
from openjiuwen.core.common.clients.connector_pool import TcpConnectorPool

config = ConnectorPoolConfig(limit=100, keepalive_timeout=30)
pool = TcpConnectorPool(config)

print(f"引用计数: {pool.ref_count}")  # 初始为 1
print(f"创建时间: {pool.created_at}")
print(f"存活时间: {pool.age:.2f}秒")
```

### decrement_ref

```python
def decrement_ref(self) -> bool
```

减少引用计数。

**返回**：

**bool**，如果减少后计数 <= 0 则返回 True

**样例**：

```python
from openjiuwen.core.common.clients import get_connector_pool_manager
from openjiuwen.core.common.clients import ConnectorPoolConfig

manager = get_connector_pool_manager()
config = ConnectorPoolConfig(limit=100)

# 获取连接池
pool = await manager.get_connector_pool("default", config=config)
print(f"初始引用计数: {pool.ref_count}")  # 1

# 增加引用
pool.increment_ref()
print(f"增加后引用计数: {pool.ref_count}")  # 2

# 减少引用
should_close = pool.decrement_ref()
print(f"减少后引用计数: {pool.ref_count}")  # 1
print(f"是否应该关闭: {should_close}")  # False

# 再次减少
should_close = pool.decrement_ref()
print(f"减少后引用计数: {pool.ref_count}")  # 0
print(f"是否应该关闭: {should_close}")  # True
```

### close

```python
async def close(self, **kwargs)
```

关闭资源。减少引用计数并执行实际关闭操作。

**样例**：

```python
from openjiuwen.core.common.clients import get_connector_pool_manager
from openjiuwen.core.common.clients import ConnectorPoolConfig

manager = get_connector_pool_manager()
config = ConnectorPoolConfig(limit=100)

# 获取连接池
pool = await manager.get_connector_pool("default", config=config)

# 使用连接池
connector = pool.conn()

# 手动关闭（通常由管理器自动处理）
await pool.close()

print(f"已关闭: {pool.closed}")  # True
print(f"引用计数: {pool.ref_count}")  # 0
```

### conn

```python
@abstractmethod
def conn(self) -> Any
```

获取底层连接器。

**样例**：

```python
from openjiuwen.core.common.clients import get_connector_pool_manager
from openjiuwen.core.common.clients import ConnectorPoolConfig
import aiohttp

manager = get_connector_pool_manager()
config = ConnectorPoolConfig(limit=100)

# 获取默认 TCP 连接池
pool = await manager.get_connector_pool("default", config=config)

# 获取底层 TCPConnector
connector = pool.conn()
print(f"连接器类型: {type(connector)}")  # <class 'aiohttp.connector.TCPConnector'>
print(f"连接限制: {connector.limit}")
print(f"每主机限制: {connector.limit_per_host}")

# 与 aiohttp 会话配合使用
async with aiohttp.ClientSession(connector=connector) as session:
    async with session.get("https://api.example.com") as resp:
        print(f"状态码: {resp.status}")
```

### _do_close

```python
@abstractmethod
async def _do_close(self, **kwargs) -> None
```

执行实际的关闭操作。子类需实现此方法处理特定连接器的清理。

**样例**：

```python
from openjiuwen.core.common.clients import ConnectorPool

# 自定义连接池实现
class MyConnectorPool(ConnectorPool):
    def __init__(self, config):
        super().__init__(config)
        self._my_connector = MyConnector()
    
    def conn(self) -> Any:
        return self._my_connector
    
    async def _do_close(self, **kwargs):
        # 实现特定的清理逻辑
        if self._my_connector:
            await self._my_connector.cleanup()
            self._my_connector = None
```

### is_expired

```python
def is_expired(self) -> bool
```

检查连接池是否已过期。

**样例**：

```python
from openjiuwen.core.common.clients import get_connector_pool_manager
from openjiuwen.core.common.clients import ConnectorPoolConfig
import asyncio

# 设置较短的 TTL 用于测试
config = ConnectorPoolConfig(ttl=2)  # 2秒后过期

manager = get_connector_pool_manager()
pool = await manager.get_connector_pool("default", config=config)

print(f"刚创建是否过期: {pool.is_expired()}")  # False

await asyncio.sleep(3)  # 等待超过 TTL

print(f"等待后是否过期: {pool.is_expired()}")  # True
```

### stat

```python
def stat(self) -> Dict[str, Any]
```

获取连接池统计信息。

**样例**：

```python
from openjiuwen.core.common.clients import get_connector_pool_manager
from openjiuwen.core.common.clients import ConnectorPoolConfig

manager = get_connector_pool_manager()
config = ConnectorPoolConfig(limit=100, keepalive_timeout=30)

pool = await manager.get_connector_pool("default", config=config)

# 增加引用计数
pool.increment_ref()
pool.increment_ref()

# 获取统计信息
stats = pool.stat()
print(f"引用计数详情: {stats['ref_detail']}")
print(f"引用计数: {stats['ref_count']}")
print(f"已关闭: {stats['closed']}")
print(f"最后使用时间: {stats['last_used']}")
```

---

## class HttpSessionManager

### acquire

```python
async def acquire(self, config) -> Tuple[HttpSession, bool]
```

获取资源。

**返回**：

**Tuple[HttpSession, bool]**，资源对象和是否新创建的标志

**样例**：

```python
from openjiuwen.core.common.clients import get_http_session_manager
from openjiuwen.core.common.clients import SessionConfig

manager = get_http_session_manager()

# 创建配置
config = SessionConfig(timeout=30, headers={"User-Agent": "MyApp/1.0"})

# 第一次获取 - 创建新会话
session1, is_new1 = await manager.acquire(config)
print(f"新创建的会话: {is_new1}")  # True

# 第二次获取 - 复用会话
session2, is_new2 = await manager.acquire(config)
print(f"新创建的会话: {is_new2}")  # False
print(f"是否是同一实例: {session1 is session2}")  # True

# 使用会话
async with session1.session().get("https://api.example.com") as resp:
    data = await resp.json()

# 释放会话
await manager.release(config)
```

### release_session

```python
async def release_session(self, config: SessionConfig)
```

将会话释放回管理器。

**样例**：

```python
from openjiuwen.core.common.clients import get_http_session_manager
from openjiuwen.core.common.clients import SessionConfig

manager = get_http_session_manager()
config = SessionConfig(timeout=30)

# 获取会话
session, is_new = await manager.acquire(config)

try:
    # 使用会话
    async with session.session().get("https://api.example.com/users") as resp:
        users = await resp.json()
finally:
    # 释放会话
    await manager.release_session(config)

# 也可以使用上下文管理器更安全
async with manager.get_session(config) as session:
    async with session.session().get("https://api.example.com/users") as resp:
        users = await resp.json()
```

---

## class HttpSession

### session

```python
def session(self) -> ClientSession
```

获取底层的 aiohttp ClientSession。

**样例**：

```python
from openjiuwen.core.common.clients import get_http_session_manager
from openjiuwen.core.common.clients import SessionConfig

manager = get_http_session_manager()
config = SessionConfig(headers={"User-Agent": "MyApp"})

async with manager.get_session(config) as http_session:
    # 获取底层 ClientSession
    session = http_session.session()
    
    # 使用 session 发起请求
    async with session.get("https://api.example.com/users") as resp:
        print(f"状态码: {resp.status}")
        headers = resp.headers
        data = await resp.json()
    
    # 可以重用同一个 session 发起多个请求
    async with session.post("https://api.example.com/users", json={"name": "John"}) as resp:
        new_user = await resp.json()
```

---

## class HttpClient

openjiuwen 提供的 HTTP 客户端，通过 get_client_registry().get_client("http") 获取，内部封装了连接池管理和会话复用机制，支持自动资源回收和高效连接复用。

```python
def __ init__(config: Optional[Union[SessionConfig, Dict[str, Any]]] = None,*,reuse_session: bool = True
)
```

**参数**：

* **config**: 可选会话配置（可以是 SessionConfig 对象或字典）
* **reuse_session** (bool): 是否重用会话，默认为 True

> **说明**：
> `reuse_session` 参数控制会话的生命周期管理策略：
> 
> * **`reuse_session=True`（默认）**：客户端内部维护一个长期会话，所有请求复用同一个会话。适用于需要多次请求的场景（如 API 客户端），可减少连接建立开销。客户端关闭时才会释放会话。
> * **`reuse_session=False`**：每个请求都从会话管理器获取新会话，请求完成后立即释放。适用于偶尔的单次请求，避免长期占用会话资源。

**样例**：

```python
from openjiuwen.core.common.clients import get_client_registry
from openjiuwen.core.common.clients import SessionConfig

# 1. 获取默认 HTTP 客户端（reuse_session=True）
http_client = await get_client_registry().get_client('http')

# 使用同一会话执行多个请求
async with http_client:
    # 这些请求共享同一个 TCP 连接和会话
    users = await http_client.get("https://api.example.com/users")
    posts = await http_client.get("https://api.example.com/posts")
    comments = await http_client.get("https://api.example.com/comments")

# 2. 自定义配置的客户端
custom_config = SessionConfig(
    timeout=30,
    headers={"User-Agent": "MyApp/1.0"},
    connect_timeout=5
)
http_client_custom = await get_client_registry().get_client(
    'http',
    config=custom_config
)

async with http_client_custom:
    result = await http_client_custom.get("https://api.example.com/data")
    print(result)

# 3. 不使用会话复用（每次请求独立会话）
http_client_no_reuse = await get_client_registry().get_client(
    'http',
    reuse_session=False
)

# 每个请求都会创建和销毁会话
result1 = await http_client_no_reuse.get("https://api.example.com/users")
result2 = await http_client_no_reuse.get("https://api.example.com/posts")
await http_client_no_reuse.close()  # 需要手动关闭

# 4. 从注册中心获取时传递配置
config_dict = {
    "timeout": 10,
    "headers": {"Authorization": "Bearer token"},
    "connector_pool_config": {
        "limit": 200,
        "keepalive_timeout": 30
    }
}
http_client_with_config = await get_client_registry().get_client(
    'http',
    config=config_dict
)

# 使用客户端
async with http_client_with_config:
    response = await http_client_with_config.get(
        "https://api.example.com/protected",
        params={"id": 123}
    )
    print(f"状态码: {response['code']}")
    print(f"数据: {response['data']}")

# 5. 直接实例化（通常不推荐，建议通过注册中心获取）
from openjiuwen.core.common.clients.http_client import HttpClient

manual_client = HttpClient(
    config={"timeout": 5},
    reuse_session=True
)

async with manual_client:
    result = await manual_client.get("https://api.example.com/health")
    print(result)
```

### get

```python
async def get(self, url: str, params: Optional[Dict] = None, **kwargs) -> Dict
```

执行 HTTP GET 请求。

**参数**：

* * **url** (str): 请求 URL
  * **params** (Optional[Dict]): 查询参数，会自动编码为 URL 查询字符串
  * **kwargs**: 额外请求参数
    * **headers** (Dict): 请求头，默认为 `None`（使用初始化时配置的默认 headers）
    * **timeout** (float): 请求超时时间（秒），默认为 `None`（使用初始化时配置的默认 timeout）
    * **chunked** (bool): 是否分块读取，默认为 `False`
    * **chunk_size** (int): 分块大小（字节），默认为 `1024`
    * **timeout_args** (Dict): 详细的超时参数配置（sock_read_timeout, sock_connect_timeout 等）
    * **response_bytes_size_limit** (int): 分块读取时的响应大小限制，默认为 `10 * 1024 * 1024`（10MB）

**返回**：

**Dict**，包含以下字段的响应字典：

* **code** (int): HTTP 状态码
* **data** (Any): 响应数据（自动根据 Content-Type 解析为 JSON、文本或二进制）
* **url** (str): 最终请求的 URL（可能包含重定向）
* **headers** (Dict): 响应头字典
* **reason** (str): HTTP 状态说明

> **说明**：
> GET 请求用于获取资源。响应内容会根据 Content-Type 自动解析：
> 
> * `application/json`：解析为 Python 字典/列表
> * `text/*`：解析为字符串
> * 其他：返回原始字节数据

**样例**：

```python
from openjiuwen.core.common.clients import get_client_registry

# 获取 HTTP 客户端
http_client = await get_client_registry().get_client('http')

# 简单 GET 请求
result = await http_client.get("https://api.example.com/users")
print(f"状态码: {result['code']}")
print(f"数据: {result['data']}")

# 带参数的 GET 请求
params = {"page": 1, "limit": 10}
headers = {"Authorization": "Bearer token123"}
result = await http_client.get(
    "https://api.example.com/users",
    params=params,
    headers=headers,
    timeout=5.0
)
print(f"响应头: {result['headers']}")
print(f"响应数据: {result['data']}")
```

### post

```python
async def post(self, url: str, body: Optional[Dict] = None, **kwargs) -> Dict
```

执行 HTTP POST 请求。

**参数**：

* **url** (str): 请求 URL
* **body** (Optional[Dict]): 请求体，将自动 JSON 编码
* **kwargs**: 额外请求参数
  * **headers** (Dict): 请求头，默认为 `None`（使用初始化时配置的默认 headers）
  * **timeout** (float): 请求超时时间（秒），默认为 `None`
  * **chunked** (bool): 是否分块读取响应，默认为 `False`
  * **chunk_size** (int): 分块大小（字节），默认为 `1024`
  * **timeout_args** (Dict): 详细的超时参数配置
  * **response_bytes_size_limit** (int): 分块读取时的响应大小限制，默认为 10MB

**返回**：

**Dict**，包含与 get 方法相同的响应字段

> **说明**：
> POST 请求用于创建资源。请求体自动进行 JSON 编码，并添加 `Content-Type: application/json` 头。响应内容的解析规则与 GET 相同。

**样例**：

```python
from openjiuwen.core.common.clients import get_client_registry

# 获取 HTTP 客户端
http_client = await get_client_registry().get_client('http')

# POST JSON 数据
user_data = {
    "name": "John Doe",
    "email": "john@example.com",
    "age": 30
}

result = await http_client.post(
    "https://api.example.com/users",
    body=user_data,
    headers={"X-Custom-Header": "value"},
    timeout=10
)

print(f"创建的用户: {result['data']}")
print(f"状态码: {result['code']}")
```

### put

```python
async def put(self, url: str, body: Optional[Dict] = None, **kwargs) -> Dict
```

执行 HTTP PUT 请求。

**参数**：

* **url** (str): 请求 URL
* **body** (Optional[Dict]): 请求体，将自动 JSON 编码
* **kwargs**: 额外请求参数（同 post 方法）

**返回**：

**Dict**，包含与 get 方法相同的响应字段

> **说明**：
> PUT 请求用于完整更新资源。与 POST 不同，PUT 通常是幂等的，多次调用相同请求应产生相同结果。请求体自动进行 JSON 编码。

**样例**：

```python
from openjiuwen.core.common.clients import get_client_registry

http_client = await get_client_registry().get_client('http')

# 更新用户数据
update_data = {
    "name": "John Updated",
    "age": 31
}

result = await http_client.put(
    "https://api.example.com/users/123",
    body=update_data
)

print(f"更新结果: {result['data']}")
```

### delete

```python
async def delete(self, url: str, **kwargs) -> Dict
```

执行 HTTP DELETE 请求。

**参数**：

* **url** (str): 请求 URL
* **kwargs**: 额外请求参数
  * **headers** (Dict): 请求头
  * **timeout** (float): 请求超时时间

**返回**：

**Dict**，包含与 get 方法相同的响应字段

> **说明**：
> DELETE 请求用于删除资源。通常返回 200（成功）或 204（无内容）状态码。

**样例**：

```python
from openjiuwen.core.common.clients import get_client_registry

http_client = await get_client_registry().get_client('http')

# 删除用户
result = await http_client.delete("https://api.example.com/users/123")
print(f"删除状态码: {result['code']}")
```

### patch

```python
async def patch(self, url: str, body: Optional[Dict] = None, **kwargs) -> Dict
```

执行 HTTP PATCH 请求。

**参数**：

* **url** (str): 请求 URL
* **body** (Optional[Dict]): 请求体，包含要部分更新的字段，将自动 JSON 编码
* **kwargs**: 额外请求参数（同 post 方法）

**返回**：

**Dict**，包含与 get 方法相同的响应字段

> **说明**：
> PATCH 请求用于部分更新资源。与 PUT 不同，PATCH 只更新提供的字段，其他字段保持不变。请求体自动进行 JSON 编码。

**样例**：

```python
from openjiuwen.core.common.clients import get_client_registry

http_client = await get_client_registry().get_client('http')

# 部分更新用户
patch_data = {
    "email": "newemail@example.com"
}

result = await http_client.patch(
    "https://api.example.com/users/123",
    body=patch_data
)
```

### head

```python
async def head(self, url: str, **kwargs) -> Dict
```

执行 HTTP HEAD 请求。
**参数**：

* **url** (str): 请求 URL
* **kwargs**: 额外请求参数
  * **headers** (Dict): 请求头
  * **timeout** (float): 请求超时时间

**返回**：

**Dict**，包含以下字段：

* **code** (int): HTTP 状态码
* **data** (None): HEAD 请求无响应体，始终为 None
* **url** (str): 最终请求的 URL
* **headers** (Dict): 响应头字典
* **reason** (str): HTTP 状态说明

> **说明**：
> HEAD 请求与 GET 类似，但服务器不返回响应体。通常用于检查资源是否存在、获取元数据（如内容长度、最后修改时间等），而不实际传输资源内容。

**样例**：

```python
from openjiuwen.core.common.clients import get_client_registry

http_client = await get_client_registry().get_client('http')

# 获取响应头
result = await http_client.head("https://api.example.com/users")
print(f"响应头: {result['headers']}")
print(f"内容类型: {result['headers'].get('Content-Type')}")
```

### options

```python
async def options(self, url: str, **kwargs) -> Dict
```

执行 HTTP OPTIONS 请求。

**参数**：

* **url** (str): 请求 URL
* **kwargs**: 额外请求参数
  * **headers** (Dict): 请求头
  * **timeout** (float): 请求超时时间

**返回**：

**Dict**，包含与 head 方法相同的响应字段

> **说明**：
> OPTIONS 请求用于获取目标资源支持的 HTTP 方法。响应头中的 `Allow` 字段列出所有允许的方法。常用于 CORS 预检请求或 API 能力发现。

**样例**：

```python
from openjiuwen.core.common.clients import get_client_registry

http_client = await get_client_registry().get_client('http')

# 获取允许的方法
result = await http_client.options("https://api.example.com/users")
allow_header = result['headers'].get('Allow', '')
print(f"允许的方法: {allow_header}")
```

### stream_get

```python
async def stream_get(self, url: str, params: Optional[Dict] = None, **kwargs)
```

流式 HTTP GET 响应。

**参数**：

* **url** (str): 请求 URL
* **params** (Optional[Dict]): 查询参数
* **kwargs**: 额外参数
  * **chunked** (bool): 是否分块读取，默认为 `False`
  * **chunk_size** (int): 分块大小（字节），默认为 `1024`
  * **on_stream_received** (Optional[Union[Callable[[bytes], Any], Callable[[bytes], Awaitable[Any]]]]): 每块数据的回调函数，可以是同步或异步函数
  * **headers** (Dict): 请求头
  * **timeout** (float): 请求超时时间
  * **timeout_args** (Dict): 详细的超时参数配置

**返回**：

**AsyncGenerator**，异步生成器，产生处理后的数据块

> **说明**：
> `stream_get` 适用于处理大文件下载或实时数据流。当 `chunked=True` 时，按固定大小分块；当 `chunked=False` 时，按行读取（适用于文本流）。可通过 `on_stream_received` 回调对每个数据块进行实时处理。

**样例**：

```python
from openjiuwen.core.common.clients import get_client_registry

http_client = await get_client_registry().get_client('http')

# 流式处理大文件下载
async for chunk in http_client.stream_get(
    "https://example.com/large-file.zip",
    chunked=True,
    chunk_size=8192
):
    # 处理每个数据块
    print(f"收到 {len(chunk)} 字节")
    # 可以写入文件或实时处理

# 使用回调函数处理
def process_chunk(chunk):
    print(f"处理 {len(chunk)} 字节")
    return len(chunk)

async for result in http_client.stream_get(
    "https://api.example.com/stream",
    on_stream_received=process_chunk,
    chunked=True
):
    print(f"回调返回: {result}")
```

### stream_post

```python
async def stream_post(self, url: str, body: Optional[Dict] = None, **kwargs)
```

流式 HTTP POST 响应。

**参数**：

* **url** (str): 请求 URL
* **body** (Optional[Dict]): 请求体，将自动 JSON 编码
* **kwargs**: 额外参数
  * **chunked** (bool): 是否分块读取响应，默认为 `False`
  * **chunk_size** (int): 分块大小（字节），默认为 `1024`
  * **on_stream_received** (Optional[Union[Callable[[bytes], Any], Callable[[bytes], Awaitable[Any]]]]): 每块数据的回调函数
  * **headers** (Dict): 请求头
  * **timeout** (float): 请求超时时间

**返回**：

**AsyncGenerator**，异步生成器，产生处理后的数据块

> **说明**：
> `stream_post` 适用于需要发送请求体并接收流式响应的场景，如大数据导出、实时日志流等。请求体自动 JSON 编码，响应处理规则与 `stream_get` 相同。

**样例**：

```python
from openjiuwen.core.common.clients import get_client_registry

http_client = await get_client_registry().get_client('http')

# 流式处理 POST 响应
async def handle_chunk(chunk):
    print(f"收到数据块: {chunk[:50]}...")  # 打印前50字节

async for chunk in http_client.stream_post(
    "https://api.example.com/process",
    body={"task": "large-data-export"},
    chunked=True,
    chunk_size=4096,
    on_stream_received=handle_chunk
):
    # 实时处理流式响应
    pass

# 处理文本行（chunked=False 时按行读取）
async for line in http_client.stream_post(
    "https://api.example.com/log-stream",
    body={"query": "tail -f app.log"}
):
    print(f"日志行: {line.decode().strip()}")
```

### close

```python
async def close(self)
```

关闭 HTTP 客户端并释放持有的会话。

> **说明**：
> 当 `reuse_session=True` 时（默认），客户端会持有一个长期会话。`close()` 方法会释放该会话回会话管理器，并标记客户端为已关闭状态。关闭后再次使用客户端将抛出 `RuntimeError`。推荐使用上下文管理器（`async with`）自动处理关闭。

**样例**：

```python
from openjiuwen.core.common.clients import get_client_registry

# 获取 HTTP 客户端
http_client = await get_client_registry().get_client('http')

try:
    # 执行多个请求
    users = await http_client.get("https://api.example.com/users")
    posts = await http_client.get("https://api.example.com/posts")
    
    # 处理数据
    print(f"用户数: {len(users['data'])}")
    print(f"文章数: {len(posts['data'])}")
finally:
    # 确保关闭客户端释放资源
    await http_client.close()

# 或者使用上下文管理器自动关闭
async with http_client:
    result = await http_client.get("https://api.example.com/data")
    print(result)
```

## class HttpXConnectorPoolConfig

HTTPX 连接池配置数据类，扩展自 ConnectorPoolConfig。


* **max_keepalive_connections** (int): 最大保持连接数，默认为 `20`。连接池中保持的空闲连接数量，这些连接可被后续请求复用，减少 TCP 握手开销。
* **local_address** (Optional[str]): 本地绑定地址，默认为 `None`。指定出站连接使用的本地 IP 地址或主机名，适用于多网卡系统或需要绑定特定网络接口的场景。
* **proxy** (Optional[str]): 代理服务器 URL，默认为 `None`。格式如 `"http://proxy.example.com:8080"` 或 `"https://proxy.example.com:8443"`。
* **base_config**: 继承自 ConnectorPoolConfig 的参数：
  * **limit** (int): 总连接数限制，默认为 `100`
  * **ssl_verify** (bool): 是否验证 SSL，默认为 `True`
  * **ssl_cert** (Optional[str]): SSL 证书路径，默认为 `None`
  * **keepalive_timeout** (Optional[float]): 保持连接超时（秒），默认为 `60.0`
  * **ttl** (Optional[int]): 连接池生存时间（秒），默认为 `3600`（1小时）
  * **max_idle_time** (Optional[int]): 最大空闲时间（秒），默认为 `300`（5分钟）
  * **extend_params** (Dict[str, Any]): 扩展参数，默认为 `{}`

> **说明**：
> `HttpXConnectorPoolConfig` 专为 HTTPX 库优化，提供了更精细的连接池控制。与基类 `ConnectorPoolConfig` 相比，增加了 HTTPX 特有的三个配置项：
> 
> * `max_keepalive_connections` 控制空闲连接池大小，影响后续请求的启动速度
> * `local_address` 用于多网卡环境或源 IP 绑定需求
> * `proxy` 提供内置的代理支持，无需额外配置
> 
> 其他参数继承自基类，保持与通用连接池配置的一致性。这些配置最终会传递给 `httpcore.AsyncConnectionPool`。

**样例**：

```python
from openjiuwen.core.common.clients import HttpXConnectorPoolConfig

# 1. 基本配置
config = HttpXConnectorPoolConfig(
    limit=200,  # 最大总连接数
    max_keepalive_connections=50,  # 最大保持连接数
    keepalive_timeout=30,  # 保持连接超时
    ssl_verify=True  # SSL 验证
)

# 2. 带代理的配置
proxy_config = HttpXConnectorPoolConfig(
    limit=100,
    max_keepalive_connections=20,
    proxy="http://proxy.company.com:8080",  # HTTP 代理
    ssl_verify=True
)

# 3. 绑定本地地址
local_config = HttpXConnectorPoolConfig(
    limit=100,
    local_address="192.168.1.100",  # 绑定特定网卡
    max_keepalive_connections=30,
    keepalive_timeout=60
)

# 4. 完整的生产环境配置
production_config = HttpXConnectorPoolConfig(
    # 连接池大小
    limit=500,
    max_keepalive_connections=100,
    
    # 超时设置
    keepalive_timeout=45,
    ttl=7200,  # 2小时生存时间
    max_idle_time=600,  # 10分钟空闲超时
    
    # 网络配置
    local_address="10.0.0.10",
    proxy="http://gateway.company.com:3128",
    
    # SSL 配置
    ssl_verify=True,
    ssl_cert="/etc/ssl/certs/ca-bundle.crt",
    
    # 扩展参数
    extend_params={
        "uds": "/tmp/httpx.sock",  # Unix Domain Socket
        "retries": 3  # 自定义重试次数
    }
)

# 5. 与 create_httpx_client 配合使用
from openjiuwen.core.common.clients import get_client_registry

# 使用配置创建 HTTPX 客户端
config = HttpXConnectorPoolConfig(
    limit=200,
    max_keepalive_connections=50,
    proxy="http://proxy:8080"
)

client = await get_client_registry().get_client(
    'httpx',
    config=config,
    need_async=True
)

# 6. 使用字典配置（自动转换为 HttpXConnectorPoolConfig）
dict_config = {
    "limit": 300,
    "max_keepalive_connections": 60,
    "proxy": "http://proxy:8080",
    "keepalive_timeout": 45,
    "local_address": "192.168.1.200"
}

client2 = await get_client_registry().get_client(
    'httpx',
    config=dict_config,
    need_async=False
)

# 7. 生成配置键（用于连接池复用）
config1 = HttpXConnectorPoolConfig(limit=100, proxy="http://proxy:8080")
config2 = HttpXConnectorPoolConfig(limit=100, proxy="http://proxy:8080")
config3 = HttpXConnectorPoolConfig(limit=200, proxy="http://proxy:8080")

key1 = config1.generate_key()
key2 = config2.generate_key()
key3 = config3.generate_key()

print(f"相同配置键相等: {key1 == key2}")  # True
print(f"不同配置键不等: {key1 == key3}")  # False
```

## create_httpx_client()

```python
@get_client_registry().register_client("httpx")
async def create_httpx_client(config: Union[HttpXConnectorPoolConfig, Dict[str, Any]],
    need_async: bool = False) -> Union['httpx.Client', 'httpx.AsyncClient']
```

创建带连接池的 HTTPX 客户端。

**参数**：

* **config**: 连接池配置（HttpXConnectorPoolConfig 实例或字典）
* **need_async** (bool): 是否返回异步客户端，默认为 False
  * `need_async=True`: 返回 `httpx.AsyncClient`，适用于 asyncio 异步环境
  * `need_async=False`: 返回 `httpx.Client`，适用于同步代码

> **说明**：
> 此工厂函数创建的 HTTPX 客户端会与全局连接池管理器集成，共享连接池资源。客户端底层使用 `HttpXConnectorPool` 提供的连接池，支持连接复用、代理、SSL 配置等特性。创建的客户端可用于直接发起 HTTP 请求，或作为其他库（如 OpenAI）的传输层。

**返回**：

**Union[httpx.Client, httpx.AsyncClient]**，HTTPX 客户端实例

**样例**：

```python
from openjiuwen.core.common.clients import get_client_registry
from openjiuwen.core.common.clients import HttpXConnectorPoolConfig

# 1. 创建同步 HTTPX 客户端
config = HttpXConnectorPoolConfig(
    limit=100,
    max_keepalive_connections=20,
    proxy="http://proxy.company.com:8080",
    ssl_verify=True
)

httpx_client = await get_client_registry().get_client(
    'httpx',
    config=config,
    need_async=False
)

# 使用同步客户端
response = httpx_client.get("https://api.example.com/users")
print(response.json())

# 2. 创建异步 HTTPX 客户端
async_httpx_client = await get_client_registry().get_client(
    'httpx',
    config={"proxy": "http://proxy:8080", "limit": 50},
    need_async=True
)

async with async_httpx_client:
    response = await async_httpx_client.get("https://api.example.com/data")
    print(response.json())

# 3. 使用字典配置
client = await get_client_registry().get_client(
    'httpx',
    config={
        "limit": 200,
        "max_keepalive_connections": 50,
        "keepalive_timeout": 30,
        "local_address": "192.168.1.100"
    },
    need_async=True
)

# 4. 直接使用工厂函数（不通过注册中心）
from openjiuwen.core.common.clients.llm_client import create_httpx_client

config = HttpXConnectorPoolConfig(limit=100)
client = await create_httpx_client(config, need_async=True)
```

## create_async_openai_client()

```python
@get_client_registry().register_client("async_openai")
async def create_async_openai_client(config: Union["ModelClientConfig", Dict[str, Any]],
                                     **kwargs) -> 'AsyncOpenAI'
```

创建异步 OpenAI 客户端，配置共享 HTTPX 连接池。

创建异步 OpenAI 客户端，配置共享 HTTPX 连接池。

**参数**：

* **config**: OpenAI 客户端配置，可以是 ModelClientConfig 实例或字典，支持字段：
  * `api_key` (str): OpenAI API 密钥
  * `api_base` (str): API 基础 URL，默认为 "[https://api.openai.com/v1](https://api.openai.com/v1)"
  * `timeout` (float): 请求超时时间（秒）
  * `max_retries` (int): 最大重试次数
  * `verify_ssl` (bool): 是否验证 SSL 证书
  * `ssl_cert` (Optional[str]): SSL 证书路径
* **kwargs**: 传递给 HTTPX 客户端的额外参数
  * `proxy` (str): 代理服务器 URL
  * `ssl_verify` (bool): SSL 验证
  * `ssl_cert` (str): SSL 证书路径
  * 以及其他 HttpXConnectorPoolConfig 支持的参数

> **说明**：
> 此工厂函数创建的 AsyncOpenAI 客户端会自动集成全局连接池管理器，使用共享的 HTTPX 连接池。它会根据 `api_base` 自动获取全局代理配置（通过 `UrlUtils.get_global_proxy_url()`），并支持 SSL 配置。创建的客户端适用于需要高并发和连接复用的 OpenAI API 调用场景。

**返回**：

**AsyncOpenAI**，异步 OpenAI 客户端实例

**样例**：

```python
from openjiuwen.core.common.clients import get_client_registry

# 1. 基本用法
config = {
    "api_key": "sk-xxxxxxxxxxxxxxxx",
    "api_base": "https://api.openai.com/v1",
    "timeout": 30,
    "max_retries": 3
}

async_openai = await get_client_registry().get_client(
    'async_openai',
    config=config
)

# 使用异步客户端
response = await async_openai.chat.completions.create(
    model="gpt-4",
    messages=[{"role": "user", "content": "Hello!"}]
)
print(response.choices[0].message.content)

# 2. 带代理配置
config_with_proxy = {
    "api_key": "sk-xxxxxxxx",
    "api_base": "https://api.openai.com/v1",
    "timeout": 60
}

async_openai_proxy = await get_client_registry().get_client(
    'async_openai',
    config=config_with_proxy,
    proxy="http://proxy.company.com:8080",  # 通过 kwargs 传递代理
    ssl_verify=True
)

# 3. 使用 ModelClientConfig 对象
from openjiuwen.core.foundation.llm import ModelClientConfig

model_config = ModelClientConfig(
    api_key="sk-xxxxxxxx",
    api_base="https://custom-openai-endpoint.com/v1",
    timeout=45,
    max_retries=5,
    verify_ssl=True
)

async_openai_custom = await get_client_registry().get_client(
    'async_openai',
    config=model_config,
    max_keepalive_connections=50  # 连接池配置
)

# 4. 流式响应
stream = await async_openai.chat.completions.create(
    model="gpt-4",
    messages=[{"role": "user", "content": "Tell me a story"}],
    stream=True
)

async for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

## create_openai_client

```python
async def create_openai_client(config: Union["ModelClientConfig", Dict[str, Any]],
                               **kwargs) -> 'OpenAI'
```

创建同步 OpenAI 客户端，配置共享 HTTPX 连接池。

**参数**：

* **config**: OpenAI 客户端配置，同 create_async_openai_client
* **kwargs**: 传递给 HTTPX 客户端的额外参数，同 create_async_openai_client

> **说明**：
> 此工厂函数创建的同步 OpenAI 客户端同样集成全局连接池管理器。适用于同步代码环境，或在异步环境中执行阻塞操作。与异步版本相比，连接池配置和使用方式完全相同，只是返回的客户端是同步的。

**返回**：

**OpenAI**，同步 OpenAI 客户端实例

**样例**：

```python
from openjiuwen.core.common.clients import get_client_registry

# 1. 基本用法
config = {
    "api_key": "sk-xxxxxxxxxxxxxxxx",
    "api_base": "https://api.openai.com/v1",
    "timeout": 30
}

openai_client = await get_client_registry().get_client(
    'openai',
    config=config
)

# 使用同步客户端
response = openai_client.chat.completions.create(
    model="gpt-4",
    messages=[{"role": "user", "content": "Hello!"}]
)
print(response.choices[0].message.content)

# 2. 带自定义连接池配置
openai_client_custom = await get_client_registry().get_client(
    'openai',
    config={
        "api_key": "sk-xxxxxxxx",
        "api_base": "https://api.openai.com/v1"
    },
    limit=200,  # 连接池最大连接数
    max_keepalive_connections=50,  # 最大保持连接数
    keepalive_timeout=60,  # 保持连接超时
    proxy="http://proxy:8080"  # 代理
)

# 3. 多次调用共享连接池
for i in range(10):
    response = openai_client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": f"Question {i}"}]
    )
    print(f"Response {i}: {response.choices[0].message.content}")

# 4. 在异步环境中调用同步客户端（需使用 run_in_executor）
import asyncio

async def call_openai_sync():
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(
        None,
        lambda: openai_client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}]
        )
    )
    return response

# 5. 错误处理和重试（客户端自动处理 max_retries）
try:
    response = openai_client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": "Hello"}],
        timeout=10  # 请求级超时
    )
except Exception as e:
    print(f"OpenAI API 调用失败: {e}")
```
