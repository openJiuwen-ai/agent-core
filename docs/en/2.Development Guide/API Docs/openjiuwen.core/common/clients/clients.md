# openjiuwen.core.clients

## get_client_registry()

```python
def get_client_registry() -> ClientRegistry
```

Get the global client registry instance.

**Returns**:

**ClientRegistry**, the global client registry

**Example**:

```python
from openjiuwen.core.common.clients import get_client_registry

# Get the global registry
registry = get_client_registry()

# Register a client class
class MyClient(BaseClient):
    __client_name__ = "myclient"
    __client_type__ = "custom"
    
    async def close(self) -> bool:
        return True

# Get a client instance
client = await registry.get_client("myclient", client_type="custom", config={"key": "value"})
```

---

## class ConnectorPoolConfig

### generate_key

```python
def generate_key(self) -> str
```

Generate a unique key for the configuration.

**Returns**:

**str**, a unique key identifying this configuration (MD5 hash)

**Example**:

```python
from openjiuwen.core.common.clients import ConnectorPoolConfig

# Create two identical configurations
config1 = ConnectorPoolConfig(limit=100, limit_per_host=30)
config2 = ConnectorPoolConfig(limit=100, limit_per_host=30)
config3 = ConnectorPoolConfig(limit=200, limit_per_host=50)

# Generate unique keys
key1 = config1.generate_key()  # e.g.: "5f4dcc3b5aa765d61d8327deb882cf99"
key2 = config2.generate_key()  # Same as key1
key3 = config3.generate_key()  # Different from key1, key2

print(f"Keys for identical configurations are equal: {key1 == key2}")  # True
print(f"Keys for different configurations are equal: {key1 == key3}")  # False
```

---

## get_connector_pool_manager()

```python
def get_connector_pool_manager() -> ConnectorPoolManager
```

Get the global connector pool manager instance.

**Returns**:

**ConnectorPoolManager**, the global connector pool manager

**Example**:

```python
from openjiuwen.core.common.clients import get_connector_pool_manager
from openjiuwen.core.common.clients import ConnectorPoolConfig

# Get the manager
manager = get_connector_pool_manager()

# Create a configuration
config = ConnectorPoolConfig(limit=100, keepalive_timeout=30)

# Get a connection pool
pool = await manager.get_connector_pool("default", config=config)

# Use the connection pool
connector = pool.conn()

# Release the reference
await manager.release_connector_pool(config)
```

---

## class ConnectorPoolManager

### get_connector_pool

```python
async def get_connector_pool(self, connector_pool_type: str = "default", *,
                             config: Optional[ConnectorPoolConfig] = None) -> ConnectorPool
```

Get or create a connection pool.

**Parameters**:

* **connector_pool_type** (str): Connection pool type, defaults to "default". Built-in connection pools include:
  * **default**: Default connection pool based on aiohttp.TCPConnector, used for connection management of aiohttp clients.
  * **httpx**: Connection pool based on HTTPX and httpcore.AsyncConnectionPool, used for connection management of HTTPX clients.
* **config** (Optional[ConnectorPoolConfig]): Optional configuration

**Returns**:

**ConnectorPool**, the connection pool instance

**Example**:

```python
from openjiuwen.core.common.clients import get_connector_pool_manager
from openjiuwen.core.common.clients import ConnectorPoolConfig
from openjiuwen.core.common.clients.llm_client import HttpXConnectorPoolConfig

manager = get_connector_pool_manager()

# Get the default TCP connection pool
tcp_config = ConnectorPoolConfig(limit=100, limit_per_host=30)
tcp_pool = await manager.get_connector_pool("default", config=tcp_config)

# Get an HTTPX connection pool
httpx_config = HttpXConnectorPoolConfig(limit=100, proxy="http://proxy:8080")
httpx_pool = await manager.get_connector_pool("httpx", config=httpx_config)

# Getting the same configuration multiple times returns the same instance
pool1 = await manager.get_connector_pool("default", config=tcp_config)  # Newly created
pool2 = await manager.get_connector_pool("default", config=tcp_config)  # Returns pool1
print(pool1 is pool2)  # True
```

### release_connector_pool

```python
async def release_connector_pool(self, config: Optional[ConnectorPoolConfig] = None)
```

Release a reference to a connection pool.

**Parameters**:

* **config** (Optional[ConnectorPoolConfig]): The configuration of the connection pool to release

**Example**:

```python
from openjiuwen.core.common.clients import get_connector_pool_manager
from openjiuwen.core.common.clients import ConnectorPoolConfig

manager = get_connector_pool_manager()
config = ConnectorPoolConfig(limit=100)

# First get - creates a new connection pool, reference count is 1
pool1 = await manager.get_connector_pool("default", config=config)

# Second get - reuses the connection pool, reference count increases to 2
pool2 = await manager.get_connector_pool("default", config=config)

print(f"Reference count: {pool1.ref_count}")  # Output: 2

# First release - reference count decreases to 1
await manager.release_connector_pool(config)

# Second release - reference count decreases to 0, connection pool closes automatically
await manager.release_connector_pool(config)

print(f"Connection pool closed: {pool1.closed}")  # Output: True
```

### close_connector_pool

```python
async def close_connector_pool(self, *, config: Optional[ConnectorPoolConfig] = None,
                               force: bool = False)
```

Close the specified connection pool.

**Parameters**:

* **config** (Optional[ConnectorPoolConfig]): The configuration of the connection pool to close
* **force** (bool): Whether to force close (even if there are references)

**Example**:

```python
from openjiuwen.core.common.clients import get_connector_pool_manager
from openjiuwen.core.common.clients import ConnectorPoolConfig

manager = get_connector_pool_manager()
config = ConnectorPoolConfig(limit=100)

# Get the connection pool
pool = await manager.get_connector_pool("default", config=config)

# Normal close - cannot close when reference count > 0
try:
    await manager.close_connector_pool(config=config, force=False)
except Exception as e:
    print(f"Cannot close: {e}")  # Cannot close: Reference count is 1

# Force close - ignores reference count
await manager.close_connector_pool(config=config, force=True)
print(f"Connection pool force closed: {pool.closed}")  # True
```

### close_all

```python
async def close_all(self)
```

Close all connection pools and close the manager.

**Example**:

```python
from openjiuwen.core.common.clients import get_connector_pool_manager
from openjiuwen.core.common.clients import ConnectorPoolConfig

manager = get_connector_pool_manager()

# Create multiple connection pools
config1 = ConnectorPoolConfig(limit=100)
config2 = ConnectorPoolConfig(limit=200)

pool1 = await manager.get_connector_pool("default", config=config1)
pool2 = await manager.get_connector_pool("httpx", config=config2)

# Get statistics
stats = manager.get_stats()
print(f"Number of connection pools before closing: {stats['total_connector_pools']}")  # Output: 2

# Close all connection pools
await manager.close_all()

print(f"Connection pool 1 closed: {pool1.closed}")  # True
print(f"Connection pool 2 closed: {pool2.closed}")  # True
print(f"Manager closed: {manager._closed}")  # True
```

### get_stats

```python
def get_stats(self) -> Dict
```

Get statistics for all connection pools.

**Example**:

```python
from openjiuwen.core.common.clients import get_connector_pool_manager
from openjiuwen.core.common.clients import ConnectorPoolConfig

manager = get_connector_pool_manager()

# Create some connection pools
config1 = ConnectorPoolConfig(limit=100, limit_per_host=30)
config2 = ConnectorPoolConfig(limit=200, keepalive_timeout=60)

pool1 = await manager.get_connector_pool("default", config=config1)
pool2 = await manager.get_connector_pool("default", config=config2)

# Get statistics
stats = manager.get_stats()

print(f"Total connection pools: {stats['total_connector_pools']}")
print(f"Maximum pools: {stats['max_pools']}")
print(f"Manager status: {'Closed' if stats['closed'] else 'Running'}")

# Iterate over detailed statistics for each connection pool
for pool_key, pool_stats in stats['connectors'].items():
    print(f"\nConnection pool {pool_key}:")
    print(f"  - Closed: {pool_stats['closed']}")
    print(f"  - Reference count: {pool_stats['ref_count']}")
    print(f"  - Age: {pool_stats['age']:.2f} seconds")
    if 'limit' in pool_stats:
        print(f"  - Connection limit: {pool_stats['limit']}")
        print(f"  - Per host limit: {pool_stats['limit_per_host']}")
```

---

## class ConnectorPool

```python
def __init__(config: ConnectorPoolConfig)
```

**Parameters**:

* **config** (ConnectorPoolConfig): Connection pool configuration

**Example**:

```python
from openjiuwen.core.common.clients import ConnectorPool
from openjiuwen.core.common.clients import ConnectorPoolConfig

# ConnectorPool is an abstract base class, usually not instantiated directly
# Use concrete implementations like TcpConnectorPool
from openjiuwen.core.common.clients.connector_pool import TcpConnectorPool

config = ConnectorPoolConfig(limit=100, keepalive_timeout=30)
pool = TcpConnectorPool(config)

print(f"Reference count: {pool.ref_count}")  # Initially 1
print(f"Creation time: {pool.created_at}")
print(f"Age: {pool.age:.2f} seconds")
```

### decrement_ref

```python
def decrement_ref(self) -> bool
```

Decrease the reference count.

**Returns**:

**bool**, True if the count after decrement is <= 0

**Example**:

```python
from openjiuwen.core.common.clients import get_connector_pool_manager
from openjiuwen.core.common.clients import ConnectorPoolConfig

manager = get_connector_pool_manager()
config = ConnectorPoolConfig(limit=100)

# Get the connection pool
pool = await manager.get_connector_pool("default", config=config)
print(f"Initial reference count: {pool.ref_count}")  # 1

# Increase reference
pool.increment_ref()
print(f"Reference count after increment: {pool.ref_count}")  # 2

# Decrease reference
should_close = pool.decrement_ref()
print(f"Reference count after decrement: {pool.ref_count}")  # 1
print(f"Should close: {should_close}")  # False

# Decrease again
should_close = pool.decrement_ref()
print(f"Reference count after decrement: {pool.ref_count}")  # 0
print(f"Should close: {should_close}")  # True
```

### close

```python
async def close(self, **kwargs)
```

Close the resource. Decrease the reference count and perform the actual close operation.

**Example**:

```python
from openjiuwen.core.common.clients import get_connector_pool_manager
from openjiuwen.core.common.clients import ConnectorPoolConfig

manager = get_connector_pool_manager()
config = ConnectorPoolConfig(limit=100)

# Get the connection pool
pool = await manager.get_connector_pool("default", config=config)

# Use the connection pool
connector = pool.conn()

# Close manually (usually handled automatically by the manager)
await pool.close()

print(f"Closed: {pool.closed}")  # True
print(f"Reference count: {pool.ref_count}")  # 0
```

### conn

```python
@abstractmethod
def conn(self) -> Any
```

Get the underlying connector.

**Example**:

```python
from openjiuwen.core.common.clients import get_connector_pool_manager
from openjiuwen.core.common.clients import ConnectorPoolConfig
import aiohttp

manager = get_connector_pool_manager()
config = ConnectorPoolConfig(limit=100)

# Get the default TCP connection pool
pool = await manager.get_connector_pool("default", config=config)

# Get the underlying TCPConnector
connector = pool.conn()
print(f"Connector type: {type(connector)}")  # <class 'aiohttp.connector.TCPConnector'>
print(f"Connection limit: {connector.limit}")
print(f"Per host limit: {connector.limit_per_host}")

# Use with aiohttp session
async with aiohttp.ClientSession(connector=connector) as session:
    async with session.get("https://api.example.com") as resp:
        print(f"Status code: {resp.status}")
```

### _do_close

```python
@abstractmethod
async def _do_close(self, **kwargs) -> None
```

Perform the actual close operation. Subclasses need to implement this method to handle specific connector cleanup.

**Example**:

```python
from openjiuwen.core.common.clients import ConnectorPool

# Custom connection pool implementation
class MyConnectorPool(ConnectorPool):
    def __init__(self, config):
        super().__init__(config)
        self._my_connector = MyConnector()
    
    def conn(self) -> Any:
        return self._my_connector
    
    async def _do_close(self, **kwargs):
        # Implement specific cleanup logic
        if self._my_connector:
            await self._my_connector.cleanup()
            self._my_connector = None
```

### is_expired

```python
def is_expired(self) -> bool
```

Check if the connection pool has expired.

**Example**:

```python
from openjiuwen.core.common.clients import get_connector_pool_manager
from openjiuwen.core.common.clients import ConnectorPoolConfig
import asyncio

# Set a short TTL for testing
config = ConnectorPoolConfig(ttl=2)  # Expires after 2 seconds

manager = get_connector_pool_manager()
pool = await manager.get_connector_pool("default", config=config)

print(f"Just created, expired: {pool.is_expired()}")  # False

await asyncio.sleep(3)  # Wait beyond TTL

print(f"After waiting, expired: {pool.is_expired()}")  # True
```

### stat

```python
def stat(self) -> Dict[str, Any]
```

Get connection pool statistics.

**Example**:

```python
from openjiuwen.core.common.clients import get_connector_pool_manager
from openjiuwen.core.common.clients import ConnectorPoolConfig

manager = get_connector_pool_manager()
config = ConnectorPoolConfig(limit=100, keepalive_timeout=30)

pool = await manager.get_connector_pool("default", config=config)

# Increase reference count
pool.increment_ref()
pool.increment_ref()

# Get statistics
stats = pool.stat()
print(f"Reference detail: {stats['ref_detail']}")
print(f"Reference count: {stats['ref_count']}")
print(f"Closed: {stats['closed']}")
print(f"Last used: {stats['last_used']}")
```

---

## class HttpSessionManager

### acquire

```python
async def acquire(self, config) -> Tuple[HttpSession, bool]
```

Acquire a resource.

**Returns**:

**Tuple[HttpSession, bool]**, the resource object and a flag indicating whether it was newly created

**Example**:

```python
from openjiuwen.core.common.clients import get_http_session_manager
from openjiuwen.core.common.clients import SessionConfig

manager = get_http_session_manager()

# Create configuration
config = SessionConfig(timeout=30, headers={"User-Agent": "MyApp/1.0"})

# First acquire - creates a new session
session1, is_new1 = await manager.acquire(config)
print(f"Newly created session: {is_new1}")  # True

# Second acquire - reuses the session
session2, is_new2 = await manager.acquire(config)
print(f"Newly created session: {is_new2}")  # False
print(f"Are they the same instance: {session1 is session2}")  # True

# Use the session
async with session1.session().get("https://api.example.com") as resp:
    data = await resp.json()

# Release the session
await manager.release(config)
```

### release_session

```python
async def release_session(self, config: SessionConfig)
```

Release a session back to the manager.

**Example**:

```python
from openjiuwen.core.common.clients import get_http_session_manager
from openjiuwen.core.common.clients import SessionConfig

manager = get_http_session_manager()
config = SessionConfig(timeout=30)

# Acquire a session
session, is_new = await manager.acquire(config)

try:
    # Use the session
    async with session.session().get("https://api.example.com/users") as resp:
        users = await resp.json()
finally:
    # Release the session
    await manager.release_session(config)

# It's safer to use the context manager
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

Get the underlying aiohttp ClientSession.

**Example**:

```python
from openjiuwen.core.common.clients import get_http_session_manager
from openjiuwen.core.common.clients import SessionConfig

manager = get_http_session_manager()
config = SessionConfig(headers={"User-Agent": "MyApp"})

async with manager.get_session(config) as http_session:
    # Get the underlying ClientSession
    session = http_session.session()
    
    # Use the session to make requests
    async with session.get("https://api.example.com/users") as resp:
        print(f"Status code: {resp.status}")
        headers = resp.headers
        data = await resp.json()
    
    # The same session can be reused for multiple requests
    async with session.post("https://api.example.com/users", json={"name": "John"}) as resp:
        new_user = await resp.json()
```

---

## class HttpClient

HTTP client provided by openjiuwen, obtained via `get_client_registry().get_client("http")`. It internally encapsulates connection pool management and session reuse mechanisms, supporting automatic resource reclamation and efficient connection reuse.

```python
def __init__(config: Optional[Union[SessionConfig, Dict[str, Any]]] = None,*,reuse_session: bool = True
)
```

**Parameters**:

* **config**: Optional session configuration (can be a SessionConfig object or a dictionary)
* **reuse_session** (bool): Whether to reuse sessions, defaults to True

> **Note**:
> The `reuse_session` parameter controls the session lifecycle management strategy:
> 
> * **`reuse_session=True` (default)**: The client maintains a long-lived session internally, and all requests reuse the same session. Suitable for scenarios requiring multiple requests (e.g., API clients), reducing connection establishment overhead. The session is released only when the client is closed.
> * **`reuse_session=False`**: Each request acquires a new session from the session manager and releases it immediately after the request completes. Suitable for occasional single requests, avoiding long-term occupation of session resources.

**Example**:

```python
from openjiuwen.core.common.clients import get_client_registry
from openjiuwen.core.common.clients import SessionConfig

# 1. Get the default HTTP client (reuse_session=True)
http_client = await get_client_registry().get_client('http')

# Use the same session to execute multiple requests
async with http_client:
    # These requests share the same TCP connection and session
    users = await http_client.get("https://api.example.com/users")
    posts = await http_client.get("https://api.example.com/posts")
    comments = await http_client.get("https://api.example.com/comments")

# 2. Client with custom configuration
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

# 3. Without session reuse (each request uses an independent session)
http_client_no_reuse = await get_client_registry().get_client(
    'http',
    reuse_session=False
)

# Each request creates and destroys a session
result1 = await http_client_no_reuse.get("https://api.example.com/users")
result2 = await http_client_no_reuse.get("https://api.example.com/posts")
await http_client_no_reuse.close()  # Needs to be closed manually

# 4. Passing configuration when getting from the registry
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

# Use the client
async with http_client_with_config:
    response = await http_client_with_config.get(
        "https://api.example.com/protected",
        params={"id": 123}
    )
    print(f"Status code: {response['code']}")
    print(f"Data: {response['data']}")

# 5. Direct instantiation (usually not recommended, prefer getting via registry)
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

Execute an HTTP GET request.

**Parameters**:

* **url** (str): Request URL
* **params** (Optional[Dict]): Query parameters, automatically encoded into the URL query string
* **kwargs**: Additional request parameters
  * **headers** (Dict): Request headers, defaults to `None` (uses the default headers configured during initialization)
  * **timeout** (float): Request timeout in seconds, defaults to `None` (uses the default timeout configured during initialization)
  * **chunked** (bool): Whether to read the response in chunks, defaults to `False`
  * **chunk_size** (int): Chunk size in bytes, defaults to `1024`
  * **timeout_args** (Dict): Detailed timeout parameter configuration (sock_read_timeout, sock_connect_timeout, etc.)
  * **response_bytes_size_limit** (int): Response size limit when reading in chunks, defaults to `10 * 1024 * 1024` (10MB)

**Returns**:

**Dict**, a response dictionary containing the following fields:

* **code** (int): HTTP status code
* **data** (Any): Response data (automatically parsed as JSON, text, or binary based on Content-Type)
* **url** (str): The final request URL (may include redirects)
* **headers** (Dict): Response headers dictionary
* **reason** (str): HTTP status phrase

> **Note**:
> GET requests are used to retrieve resources. The response content is automatically parsed based on Content-Type:
> 
> * `application/json`: Parsed into a Python dictionary/list
> * `text/*`: Parsed into a string
> * Others: Returns raw byte data

**Example**:

```python
from openjiuwen.core.common.clients import get_client_registry

# Get HTTP client
http_client = await get_client_registry().get_client('http')

# Simple GET request
result = await http_client.get("https://api.example.com/users")
print(f"Status code: {result['code']}")
print(f"Data: {result['data']}")

# GET request with parameters
params = {"page": 1, "limit": 10}
headers = {"Authorization": "Bearer token123"}
result = await http_client.get(
    "https://api.example.com/users",
    params=params,
    headers=headers,
    timeout=5.0
)
print(f"Response headers: {result['headers']}")
print(f"Response data: {result['data']}")
```

### post

```python
async def post(self, url: str, body: Optional[Dict] = None, **kwargs) -> Dict
```

Execute an HTTP POST request.

**Parameters**:

* **url** (str): Request URL
* **body** (Optional[Dict]): Request body, automatically JSON encoded
* **kwargs**: Additional request parameters
  * **headers** (Dict): Request headers, defaults to `None` (uses the default headers configured during initialization)
  * **timeout** (float): Request timeout in seconds, defaults to `None`
  * **chunked** (bool): Whether to read the response in chunks, defaults to `False`
  * **chunk_size** (int): Chunk size in bytes, defaults to `1024`
  * **timeout_args** (Dict): Detailed timeout parameter configuration
  * **response_bytes_size_limit** (int): Response size limit when reading in chunks, defaults to 10MB

**Returns**:

**Dict**, a response dictionary with the same fields as the get method

> **Note**:
> POST requests are used to create resources. The request body is automatically JSON encoded, and a `Content-Type: application/json` header is added. Response content parsing rules are the same as for GET.

**Example**:

```python
from openjiuwen.core.common.clients import get_client_registry

# Get HTTP client
http_client = await get_client_registry().get_client('http')

# POST JSON data
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

print(f"Created user: {result['data']}")
print(f"Status code: {result['code']}")
```

### put

```python
async def put(self, url: str, body: Optional[Dict] = None, **kwargs) -> Dict
```

Execute an HTTP PUT request.

**Parameters**:

* **url** (str): Request URL
* **body** (Optional[Dict]): Request body, automatically JSON encoded
* **kwargs**: Additional request parameters (same as the post method)

**Returns**:

**Dict**, a response dictionary with the same fields as the get method

> **Note**:
> PUT requests are used to fully update resources. Unlike POST, PUT is typically idempotent, meaning multiple identical requests should produce the same result. The request body is automatically JSON encoded.

**Example**:

```python
from openjiuwen.core.common.clients import get_client_registry

http_client = await get_client_registry().get_client('http')

# Update user data
update_data = {
    "name": "John Updated",
    "age": 31
}

result = await http_client.put(
    "https://api.example.com/users/123",
    body=update_data
)

print(f"Update result: {result['data']}")
```

### delete

```python
async def delete(self, url: str, **kwargs) -> Dict
```

Execute an HTTP DELETE request.

**Parameters**:

* **url** (str): Request URL
* **kwargs**: Additional request parameters
  * **headers** (Dict): Request headers
  * **timeout** (float): Request timeout

**Returns**:

**Dict**, a response dictionary with the same fields as the get method

> **Note**:
> DELETE requests are used to delete resources. Typically returns a 200 (success) or 204 (no content) status code.

**Example**:

```python
from openjiuwen.core.common.clients import get_client_registry

http_client = await get_client_registry().get_client('http')

# Delete a user
result = await http_client.delete("https://api.example.com/users/123")
print(f"Delete status code: {result['code']}")
```

### patch

```python
async def patch(self, url: str, body: Optional[Dict] = None, **kwargs) -> Dict
```

Execute an HTTP PATCH request.

**Parameters**:

* **url** (str): Request URL
* **body** (Optional[Dict]): Request body, containing fields to be partially updated, automatically JSON encoded
* **kwargs**: Additional request parameters (same as the post method)

**Returns**:

**Dict**, a response dictionary with the same fields as the get method

> **Note**:
> PATCH requests are used to partially update resources. Unlike PUT, PATCH only updates the provided fields, leaving other fields unchanged. The request body is automatically JSON encoded.

**Example**:

```python
from openjiuwen.core.common.clients import get_client_registry

http_client = await get_client_registry().get_client('http')

# Partially update a user
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

Execute an HTTP HEAD request.
**Parameters**:

* **url** (str): Request URL
* **kwargs**: Additional request parameters
  * **headers** (Dict): Request headers
  * **timeout** (float): Request timeout

**Returns**:

**Dict**, containing the following fields:

* **code** (int): HTTP status code
* **data** (None): HEAD requests have no response body, always None
* **url** (str): The final request URL
* **headers** (Dict): Response headers dictionary
* **reason** (str): HTTP status phrase

> **Note**:
> HEAD requests are similar to GET, but the server does not return a response body. They are typically used to check if a resource exists, or to obtain metadata (such as content length, last modified time, etc.) without actually transferring the resource content.

**Example**:

```python
from openjiuwen.core.common.clients import get_client_registry

http_client = await get_client_registry().get_client('http')

# Get response headers
result = await http_client.head("https://api.example.com/users")
print(f"Response headers: {result['headers']}")
print(f"Content type: {result['headers'].get('Content-Type')}")
```

### options

python

```
async def options(self, url: str, **kwargs) -> Dict
```

Execute an HTTP OPTIONS request.

**Parameters**:

* **url** (str): Request URL
* **kwargs**: Additional request parameters
  * **headers** (Dict): Request headers
  * **timeout** (float): Request timeout

**Returns**:

**Dict**, a response dictionary with the same fields as the head method

> **Note**:
> OPTIONS requests are used to obtain the HTTP methods supported by the target resource. The `Allow` header in the response lists all allowed methods. Commonly used for CORS preflight requests or API capability discovery.

**Example**:

python

```
from openjiuwen.core.common.clients import get_client_registry

http_client = await get_client_registry().get_client('http')

# Get allowed methods
result = await http_client.options("https://api.example.com/users")
allow_header = result['headers'].get('Allow', '')
print(f"Allowed methods: {allow_header}")
```

### stream_get

python

```
async def stream_get(self, url: str, params: Optional[Dict] = None, **kwargs)
```

Stream an HTTP GET response.

**Parameters**:

* **url** (str): Request URL
* **params** (Optional[Dict]): Query parameters
* **kwargs**: Additional parameters
  * **chunked** (bool): Whether to read in chunks, defaults to `False`
  * **chunk_size** (int): Chunk size in bytes, defaults to `1024`
  * **on_stream_received** (Optional[Union[Callable[[bytes], Any], Callable[[bytes], Awaitable[Any]]]]): Callback function for each chunk of data, can be synchronous or asynchronous
  * **headers** (Dict): Request headers
  * **timeout** (float): Request timeout
  * **timeout_args** (Dict): Detailed timeout parameter configuration

**Returns**:

**AsyncGenerator**, an asynchronous generator yielding processed data chunks

> **Note**:
> `stream_get` is suitable for handling large file downloads or real-time data streams. When `chunked=True`, it reads in fixed-size chunks; when `chunked=False`, it reads line by line (suitable for text streams). The `on_stream_received` callback can be used to process each data chunk in real-time.

**Example**:

python

```
from openjiuwen.core.common.clients import get_client_registry

http_client = await get_client_registry().get_client('http')

# Stream a large file download
async for chunk in http_client.stream_get(
    "https://example.com/large-file.zip",
    chunked=True,
    chunk_size=8192
):
    # Process each data chunk
    print(f"Received {len(chunk)} bytes")
    # Can write to a file or process in real-time

# Using a callback function
def process_chunk(chunk):
    print(f"Processing {len(chunk)} bytes")
    return len(chunk)

async for result in http_client.stream_get(
    "https://api.example.com/stream",
    on_stream_received=process_chunk,
    chunked=True
):
    print(f"Callback returned: {result}")
```

### stream_post

python

```
async def stream_post(self, url: str, body: Optional[Dict] = None, **kwargs)
```

Stream an HTTP POST response.

**Parameters**:

* **url** (str): Request URL
* **body** (Optional[Dict]): Request body, automatically JSON encoded
* **kwargs**: Additional parameters
  * **chunked** (bool): Whether to read the response in chunks, defaults to `False`
  * **chunk_size** (int): Chunk size in bytes, defaults to `1024`
  * **on_stream_received** (Optional[Union[Callable[[bytes], Any], Callable[[bytes], Awaitable[Any]]]]): Callback function for each chunk of data
  * **headers** (Dict): Request headers
  * **timeout** (float): Request timeout

**Returns**:

**AsyncGenerator**, an asynchronous generator yielding processed data chunks

> **Note**:
> `stream_post` is suitable for scenarios where you need to send a request body and receive a streaming response, such as large data exports, real-time log streams, etc. The request body is automatically JSON encoded, and response handling rules are the same as for `stream_get`.

**Example**:

python

```
from openjiuwen.core.common.clients import get_client_registry

http_client = await get_client_registry().get_client('http')

# Stream a POST response
async def handle_chunk(chunk):
    print(f"Received data chunk: {chunk[:50]}...")  # Print first 50 bytes

async for chunk in http_client.stream_post(
    "https://api.example.com/process",
    body={"task": "large-data-export"},
    chunked=True,
    chunk_size=4096,
    on_stream_received=handle_chunk
):
    # Process streaming response in real-time
    pass

# Process text lines (reads line by line when chunked=False)
async for line in http_client.stream_post(
    "https://api.example.com/log-stream",
    body={"query": "tail -f app.log"}
):
    print(f"Log line: {line.decode().strip()}")
```

### close

python

```
async def close(self)
```

Close the HTTP client and release the held session.

> **Note**:
> When `reuse_session=True` (default), the client holds a long-lived session. The `close()` method releases this session back to the session manager and marks the client as closed. Using the client after it's closed will raise a `RuntimeError`. It is recommended to use a context manager (`async with`) to handle closing automatically.

**Example**:

python

```
from openjiuwen.core.common.clients import get_client_registry

# Get HTTP client
http_client = await get_client_registry().get_client('http')

try:
    # Execute multiple requests
    users = await http_client.get("https://api.example.com/users")
    posts = await http_client.get("https://api.example.com/posts")
    
    # Process data
    print(f"Number of users: {len(users['data'])}")
    print(f"Number of posts: {len(posts['data'])}")
finally:
    # Ensure the client is closed to release resources
    await http_client.close()

# Or use a context manager for automatic closing
async with http_client:
    result = await http_client.get("https://api.example.com/data")
    print(result)
```

## class HttpXConnectorPoolConfig

HTTPX connection pool configuration data class, extending from ConnectorPoolConfig.

* **max_keepalive_connections** (int): Maximum number of keep-alive connections, defaults to `20`. The number of idle connections kept in the connection pool that can be reused for subsequent requests, reducing TCP handshake overhead.
* **local_address** (Optional[str]): Local binding address, defaults to `None`. Specifies the local IP address or hostname to use for outgoing connections, suitable for multi-NIC systems or scenarios requiring binding to a specific network interface.
* **proxy** (Optional[str]): Proxy server URL, defaults to `None`. Format like `"http://proxy.example.com:8080"` or `"https://proxy.example.com:8443"`.
* **base_config**: Parameters inherited from ConnectorPoolConfig:
  * **limit** (int): Total connection limit, defaults to `100`
  * **ssl_verify** (bool): Whether to verify SSL, defaults to `True`
  * **ssl_cert** (Optional[str]): SSL certificate path, defaults to `None`
  * **keepalive_timeout** (Optional[float]): Keep-alive timeout in seconds, defaults to `60.0`
  * **ttl** (Optional[int]): Connection pool time-to-live in seconds, defaults to `3600` (1 hour)
  * **max_idle_time** (Optional[int]): Maximum idle time in seconds, defaults to `300` (5 minutes)
  * **extend_params** (Dict[str, Any]): Extended parameters, defaults to `{}`

> **Note**:
> `HttpXConnectorPoolConfig` is optimized for the HTTPX library, providing more granular control over the connection pool. Compared to the base class `ConnectorPoolConfig`, it adds three HTTPX-specific configuration items:
> 
> * `max_keepalive_connections` controls the size of the idle connection pool, affecting the startup speed of subsequent requests.
> * `local_address` is used for multi-NIC environments or source IP binding requirements.
> * `proxy` provides built-in proxy support without additional configuration.
> 
> Other parameters are inherited from the base class, maintaining consistency with the general connection pool configuration. These configurations are ultimately passed to `httpcore.AsyncConnectionPool`.

**Example**:

python

```
from openjiuwen.core.common.clients import HttpXConnectorPoolConfig

# 1. Basic configuration
config = HttpXConnectorPoolConfig(
    limit=200,  # Maximum total connections
    max_keepalive_connections=50,  # Maximum keep-alive connections
    keepalive_timeout=30,  # Keep-alive timeout
    ssl_verify=True  # SSL verification
)

# 2. Configuration with proxy
proxy_config = HttpXConnectorPoolConfig(
    limit=100,
    max_keepalive_connections=20,
    proxy="http://proxy.company.com:8080",  # HTTP proxy
    ssl_verify=True
)

# 3. Binding to a local address
local_config = HttpXConnectorPoolConfig(
    limit=100,
    local_address="192.168.1.100",  # Bind to a specific NIC
    max_keepalive_connections=30,
    keepalive_timeout=60
)

# 4. Complete production configuration
production_config = HttpXConnectorPoolConfig(
    # Connection pool size
    limit=500,
    max_keepalive_connections=100,
    
    # Timeout settings
    keepalive_timeout=45,
    ttl=7200,  # 2 hours TTL
    max_idle_time=600,  # 10 minutes idle timeout
    
    # Network configuration
    local_address="10.0.0.10",
    proxy="http://gateway.company.com:3128",
    
    # SSL configuration
    ssl_verify=True,
    ssl_cert="/etc/ssl/certs/ca-bundle.crt",
    
    # Extended parameters
    extend_params={
        "uds": "/tmp/httpx.sock",  # Unix Domain Socket
        "retries": 3  # Custom retry count
    }
)

# 5. Used with create_httpx_client
from openjiuwen.core.common.clients import get_client_registry

# Create an HTTPX client using the configuration
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

# 6. Using dictionary configuration (automatically converted to HttpXConnectorPoolConfig)
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

# 7. Generating configuration keys (for connection pool reuse)
config1 = HttpXConnectorPoolConfig(limit=100, proxy="http://proxy:8080")
config2 = HttpXConnectorPoolConfig(limit=100, proxy="http://proxy:8080")
config3 = HttpXConnectorPoolConfig(limit=200, proxy="http://proxy:8080")

key1 = config1.generate_key()
key2 = config2.generate_key()
key3 = config3.generate_key()

print(f"Keys for identical configurations are equal: {key1 == key2}")  # True
print(f"Keys for different configurations are not equal: {key1 == key3}")  # False
```

## create_httpx_client()

python

```
@get_client_registry().register_client("httpx")
async def create_httpx_client(config: Union[HttpXConnectorPoolConfig, Dict[str, Any]],
    need_async: bool = False) -> Union['httpx.Client', 'httpx.AsyncClient']
```

Create an HTTPX client with a connection pool.

**Parameters**:

* **config**: Connection pool configuration (HttpXConnectorPoolConfig instance or dictionary)
* **need_async** (bool): Whether to return an asynchronous client, defaults to False
  * `need_async=True`: Returns `httpx.AsyncClient`, suitable for asyncio asynchronous environments
  * `need_async=False`: Returns `httpx.Client`, suitable for synchronous code

> **Note**:
> This factory function creates an HTTPX client that integrates with the global connection pool manager, sharing connection pool resources. The client uses the `HttpXConnectorPool` underneath, providing features like connection reuse, proxy support, SSL configuration, etc. The created client can be used to make HTTP requests directly or as a transport layer for other libraries (like OpenAI).

**Returns**:

**Union[httpx.Client, httpx.AsyncClient]**, an HTTPX client instance

**Example**:

python

```
from openjiuwen.core.common.clients import get_client_registry
from openjiuwen.core.common.clients import HttpXConnectorPoolConfig

# 1. Create a synchronous HTTPX client
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

# Use the synchronous client
response = httpx_client.get("https://api.example.com/users")
print(response.json())

# 2. Create an asynchronous HTTPX client
async_httpx_client = await get_client_registry().get_client(
    'httpx',
    config={"proxy": "http://proxy:8080", "limit": 50},
    need_async=True
)

async with async_httpx_client:
    response = await async_httpx_client.get("https://api.example.com/data")
    print(response.json())

# 3. Using dictionary configuration
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

# 4. Using the factory function directly (not via registry)
from openjiuwen.core.common.clients.llm_client import create_httpx_client

config = HttpXConnectorPoolConfig(limit=100)
client = await create_httpx_client(config, need_async=True)
```

## create_async_openai_client()

python

```
@get_client_registry().register_client("async_open_ai")
async def create_async_openai_client(config: Union["ModelClientConfig", Dict[str, Any]],
                                     **kwargs) -> 'AsyncOpenAI'
```

Create an asynchronous OpenAI client, configured with a shared HTTPX connection pool.

Create an asynchronous OpenAI client, configured with a shared HTTPX connection pool.

**Parameters**:

* **config**: OpenAI client configuration, can be a ModelClientConfig instance or a dictionary, supporting fields:
  * `api_key` (str): OpenAI API key
  * `api_base` (str): API base URL, defaults to "[https://api.openai.com/v1](https://api.openai.com/v1)"
  * `timeout` (float): Request timeout in seconds
  * `max_retries` (int): Maximum number of retries
  * `verify_ssl` (bool): Whether to verify SSL certificates
  * `ssl_cert` (Optional[str]): SSL certificate path
* **kwargs**: Additional parameters passed to the HTTPX client
  * `proxy` (str): Proxy server URL
  * `ssl_verify` (bool): SSL verification
  * `ssl_cert` (str): SSL certificate path
  * And other parameters supported by HttpXConnectorPoolConfig

> **Note**:
> This factory function creates an AsyncOpenAI client that automatically integrates with the global connection pool manager, using a shared HTTPX connection pool. It automatically obtains the global proxy configuration based on `api_base` (via `UrlUtils.get_global_proxy_url()`) and supports SSL configuration. The created client is suitable for high-concurrency OpenAI API calls requiring connection reuse.

**Returns**:

**AsyncOpenAI**, an asynchronous OpenAI client instance

**Example**:

python

```
from openjiuwen.core.common.clients import get_client_registry

# 1. Basic usage
config = {
    "api_key": "sk-xxxxxxxxxxxxxxxx",
    "api_base": "https://api.openai.com/v1",
    "timeout": 30,
    "max_retries": 3
}

async_openai = await get_client_registry().get_client(
    'async_open_ai',
    config=config
)

# Use the asynchronous client
response = await async_openai.chat.completions.create(
    model="gpt-4",
    messages=[{"role": "user", "content": "Hello!"}]
)
print(response.choices[0].message.content)

# 2. With proxy configuration
config_with_proxy = {
    "api_key": "sk-xxxxxxxx",
    "api_base": "https://api.openai.com/v1",
    "timeout": 60
}

async_openai_proxy = await get_client_registry().get_client(
    'async_open_ai',
    config=config_with_proxy,
    proxy="http://proxy.company.com:8080",  # Pass proxy via kwargs
    ssl_verify=True
)

# 3. Using a ModelClientConfig object
from openjiuwen.core.foundation.llm import ModelClientConfig

model_config = ModelClientConfig(
    api_key="sk-xxxxxxxx",
    api_base="https://custom-openai-endpoint.com/v1",
    timeout=45,
    max_retries=5,
    verify_ssl=True
)

async_openai_custom = await get_client_registry().get_client(
    'async_open_ai',
    config=model_config,
    max_keepalive_connections=50  # Connection pool configuration
)

# 4. Streaming response
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

python

```
async def create_openai_client(config: Union["ModelClientConfig", Dict[str, Any]],
                               **kwargs) -> 'OpenAI'
```

Create a synchronous OpenAI client, configured with a shared HTTPX connection pool.

**Parameters**:

* **config**: OpenAI client configuration, same as create_async_openai_client
* **kwargs**: Additional parameters passed to the HTTPX client, same as create_async_openai_client

> **Note**:
> This factory function creates a synchronous OpenAI client that also integrates with the global connection pool manager. Suitable for synchronous code environments, or for performing blocking operations within an asynchronous environment. Compared to the asynchronous version, the connection pool configuration and usage are identical, but the returned client is synchronous.

**Returns**:

**OpenAI**, a synchronous OpenAI client instance

**Example**:

python

```
from openjiuwen.core.common.clients import get_client_registry

# 1. Basic usage
config = {
    "api_key": "sk-xxxxxxxxxxxxxxxx",
    "api_base": "https://api.openai.com/v1",
    "timeout": 30
}

openai_client = await get_client_registry().get_client(
    'openai',
    config=config
)

# Use the synchronous client
response = openai_client.chat.completions.create(
    model="gpt-4",
    messages=[{"role": "user", "content": "Hello!"}]
)
print(response.choices[0].message.content)

# 2. With custom connection pool configuration
openai_client_custom = await get_client_registry().get_client(
    'openai',
    config={
        "api_key": "sk-xxxxxxxx",
        "api_base": "https://api.openai.com/v1"
    },
    limit=200,  # Maximum connections in the pool
    max_keepalive_connections=50,  # Maximum keep-alive connections
    keepalive_timeout=60,  # Keep-alive timeout
    proxy="http://proxy:8080"  # Proxy
)

# 3. Multiple calls sharing the connection pool
for i in range(10):
    response = openai_client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": f"Question {i}"}]
    )
    print(f"Response {i}: {response.choices[0].message.content}")

# 4. Calling the synchronous client in an asynchronous environment (needs run_in_executor)
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

# 5. Error handling and retries (the client automatically handles max_retries)
try:
    response = openai_client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": "Hello"}],
        timeout=10  # Request-level timeout
    )
except Exception as e:
    print(f"OpenAI API call failed: {e}")
```
