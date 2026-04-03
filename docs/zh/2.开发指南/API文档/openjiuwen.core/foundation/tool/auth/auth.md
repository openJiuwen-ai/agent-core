# openjiuwen.core.foundation.tool.auth

`openjiuwen.core.foundation.tool.auth` 模块提供了工具（Tool）的认证机制，支持多种认证策略（如SSL、自定义Header和Query参数），并通过策略模式实现了灵活的认证扩展。

## class ToolAuthConfig

```python
@dataclass
class ToolAuthConfig
```

工具认证配置数据类，包含认证相关的配置信息。

* **auth_type**(str)：认证类型，如 "ssl"、"header_and_query" 等。
* **config**(Dict[str, Any])：认证配置参数，根据不同的认证类型包含不同的配置项。
* **tool_type**(str)：工具类型，如 "restful_api"、"mcp" 等。
* **tool_id**(Optional[str])：工具的唯一标识符。默认为 None。

## class ToolAuthResult

```python
@dataclass
class ToolAuthResult
```

工具认证结果数据类，包含认证执行后的结果信息。

* **success**(bool)：认证是否成功。
* **auth_data**(Dict[str, Any])：认证数据，如SSL上下文、认证提供器等，用于后续工具调用。
* **message**(str)：认证消息，默认为空字符串。
* **error**(Optional[Exception])：认证错误（如果有）。默认为 None。

## enum AuthType

```python
class AuthType(Enum)
```

认证类型枚举，定义了支持的认证策略类型。

* **SSL** = "ssl"：SSL证书认证。
* **HEADER_AND_QUERY** = "header_and_query"：自定义Header和Query参数认证。

## class AuthStrategy

```python
class AuthStrategy(ABC)
```

认证策略抽象基类，所有具体的认证策略都应继承此类。

### auth_type

```python
auth_type: AuthType
```

类属性，定义了该策略支持的认证类型。子类必须重写此属性。

### authenticate

```python
@abstractmethod
async def authenticate(auth_config: ToolAuthConfig, **kwargs) -> ToolAuthResult
```

抽象方法，执行具体的认证逻辑。

**参数**：

* **auth_config**(ToolAuthConfig)：工具认证配置。
* **\*kwargs**：额外的认证参数。

**返回**：

**ToolAuthResult**，认证结果。

## class SSLAuthStrategy

```python
class SSLAuthStrategy(AuthStrategy)
```

SSL认证策略，用于处理基于SSL证书的认证。

### auth_type

```python
auth_type = AuthType.SSL
```

类属性，定义该策略支持SSL认证。

### authenticate

```python
async def authenticate(auth_config: ToolAuthConfig, **kwargs) -> ToolAuthResult
```

执行SSL认证逻辑，创建SSL上下文。

**参数**：

* **auth_config**(ToolAuthConfig)：工具认证配置，包含SSL相关的配置参数。
* **\*kwargs**：额外的认证参数。

**返回**：

**ToolAuthResult**，包含SSL连接器的认证结果。

## class HeaderQueryAuthStrategy

```python
class HeaderQueryAuthStrategy(AuthStrategy)
```

自定义Header和Query参数认证策略，用于添加自定义的请求头和查询参数。

### auth_type

```python
auth_type = AuthType.HEADER_AND_QUERY
```

类属性，定义该策略支持Header和Query参数认证。

### authenticate

```python
async def authenticate(auth_config: ToolAuthConfig, **kwargs) -> ToolAuthResult
```

执行Header和Query参数认证逻辑，创建认证提供器。

**参数**：

* **auth_config**(ToolAuthConfig)：工具认证配置，包含自定义Header和Query参数的配置。
* **\*kwargs**：额外的认证参数。

**返回**：

**ToolAuthResult**，包含认证提供器的认证结果。

## class AuthStrategyRegistry

```python
class AuthStrategyRegistry
```

认证策略注册表，用于管理和执行各种认证策略。

### register

```python
@classmethod
def register(cls, strategy_class: Type[AuthStrategy])
```

注册一个认证策略类。

**参数**：

* **strategy_class**(Type[AuthStrategy])：要注册的认证策略类。

### execute_auth

```python
@classmethod
async def execute_auth(cls, auth_config: ToolAuthConfig, **kwargs) -> Optional[ToolAuthResult]
```

执行认证逻辑，根据认证配置选择合适的认证策略。

**参数**：

* **auth_config**(ToolAuthConfig)：工具认证配置。
* **\*kwargs**：额外的认证参数。

**返回**：

**Optional[ToolAuthResult]**，认证结果。如果认证类型不支持，返回失败的认证结果。

## class AuthHeaderAndQueryProvider

```python
class AuthHeaderAndQueryProvider(httpx.Auth)
```

自定义Header和Query参数认证提供器，继承自`httpx.Auth`，用于在HTTP请求中添加自定义的Header和Query参数。

**参数**：

* **auth_headers**(Dict[str, str])：要添加的自定义请求头。
* **auth_query_params**(Dict[str, str])：要添加的自定义查询参数。

### async_auth_flow

```python
async def async_auth_flow(self, request: httpx.Request) -> AsyncGenerator[httpx.Request, httpx.Response]
```

异步认证流方法，用于修改HTTP请求，添加自定义Header和Query参数。

**参数**：

* **request**(httpx.Request)：HTTP请求对象。

**返回**：

**AsyncGenerator[httpx.Request, httpx.Response]**，修改后的HTTP请求生成器。

## Unified Authentication Handler

```python
@framework.on(ToolCallEvents.TOOL_AUTH)
async def unified_auth_handler(auth_config: ToolAuthConfig, **kwargs)
```

统一的认证处理函数，通过事件系统监听`ToolCallEvents.TOOL_AUTH`事件，调用`AuthStrategyRegistry.execute_auth`执行认证逻辑。

**参数**：

* **auth_config**(ToolAuthConfig)：工具认证配置。
* **\*kwargs**：额外的认证参数。

**返回**：

**ToolAuthResult**，认证结果。

## 使用示例

### 1. SSL认证配置

```python
from openjiuwen.core.foundation.tool.auth.auth import ToolAuthConfig
from openjiuwen.core.foundation.tool.auth.auth_callback import AuthType, AuthStrategyRegistry

# 创建SSL认证配置
ssl_auth_config = ToolAuthConfig(
    auth_type=AuthType.SSL,
    config={
        "verify_switch_env": "RESTFUL_SSL_VERIFY",
        "ssl_cert_env": "RESTFUL_SSL_CERT"
    },
    tool_type="restful_api",
    tool_id="test_api"
)

# 执行认证
result = await AuthStrategyRegistry.execute_auth(ssl_auth_config)
```

### 2. Header和Query参数认证配置

```python
from openjiuwen.core.foundation.tool.auth.auth import ToolAuthConfig
from openjiuwen.core.foundation.tool.auth.auth_callback import AuthType, AuthStrategyRegistry

# 创建Header和Query参数认证配置
header_query_auth_config = ToolAuthConfig(
    auth_type=AuthType.HEADER_AND_QUERY,
    config={
        "auth_headers": {
            "Authorization": "Bearer token123",
            "X-API-Key": "api_key456"
        },
        "auth_query_params": {
            "version": "v1",
            "region": "cn-north-1"
        }
    },
    tool_type="restful_api",
    tool_id="test_api"
)

# 执行认证
result = await AuthStrategyRegistry.execute_auth(header_query_auth_config)
```

## 扩展新的认证策略

要扩展新的认证策略，需要：

1. 创建一个继承自`AuthStrategy`的新类
2. 定义`auth_type`类属性
3. 实现`authenticate`方法
4. 注册新的认证策略

```python
from openjiuwen.core.foundation.tool.auth.auth import ToolAuthConfig, ToolAuthResult
from openjiuwen.core.foundation.tool.auth.auth_callback import AuthStrategy, AuthType, AuthStrategyRegistry

# 创建新的认证策略类
class ApiKeyAuthStrategy(AuthStrategy):
    auth_type = AuthType("api_key")  # 假设已扩展AuthType枚举

    async def authenticate(self, auth_config: ToolAuthConfig, **kwargs) -> ToolAuthResult:
        # 实现API Key认证逻辑
        api_key = auth_config.config.get("api_key")
        if not api_key:
            return ToolAuthResult(
                success=False,
                auth_data={},
                message="API Key is required"
            )
        
        auth_provider = AuthHeaderAndQueryProvider(
            auth_headers={"X-API-Key": api_key},
            auth_query_params={}
        )
        
        return ToolAuthResult(
            success=True,
            auth_data={"auth_provider": auth_provider},
            message="API Key authentication configured"
        )

# 注册新的认证策略
AuthStrategyRegistry.register(ApiKeyAuthStrategy)
```