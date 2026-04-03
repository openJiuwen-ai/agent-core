# openjiuwen.core.foundation.tool.auth

The `openjiuwen.core.foundation.tool.auth` module provides authentication mechanisms for tools, supporting multiple authentication strategies (such as SSL, custom Header and Query parameters), and implementing flexible authentication extensions through the strategy pattern.

## class ToolAuthConfig

```python
@dataclass
class ToolAuthConfig
```

Tool authentication configuration data class, containing authentication-related configuration information.

* **auth_type**(str): Authentication type, such as "ssl", "header_and_query", etc.
* **config**(Dict[str, Any]): Authentication configuration parameters, which contain different configuration items based on different authentication types.
* **tool_type**(str): Tool type, such as "restful_api", "mcp", etc.
* **tool_id**(Optional[str]): Unique identifier of the tool. Default is None.

## class ToolAuthResult

```python
@dataclass
class ToolAuthResult
```

Tool authentication result data class, containing result information after authentication execution.

* **success**(bool): Whether the authentication was successful.
* **auth_data**(Dict[str, Any]): Authentication data, such as SSL context, authentication provider, etc., used for subsequent tool calls.
* **message**(str): Authentication message, default is an empty string.
* **error**(Optional[Exception]): Authentication error (if any). Default is None.

## enum AuthType

```python
class AuthType(Enum)
```

Authentication type enumeration, defining the supported authentication strategy types.

* **SSL** = "ssl": SSL certificate authentication.
* **HEADER_AND_QUERY** = "header_and_query": Custom Header and Query parameter authentication.

## class AuthStrategy

```python
class AuthStrategy(ABC)
```

Abstract base class for authentication strategies, all concrete authentication strategies should inherit from this class.

### auth_type

```python
auth_type: AuthType
```

Class attribute that defines the authentication type supported by this strategy. Subclasses must override this attribute.

### authenticate

```python
@abstractmethod
async def authenticate(auth_config: ToolAuthConfig, **kwargs) -> ToolAuthResult
```

Abstract method that executes specific authentication logic.

**Parameters**:

* **auth_config**(ToolAuthConfig): Tool authentication configuration.
* **\*kwargs**: Additional authentication parameters.

**Returns**:

**ToolAuthResult**, authentication result.

## class SSLAuthStrategy

```python
class SSLAuthStrategy(AuthStrategy)
```

SSL authentication strategy, used to handle SSL certificate-based authentication.

### auth_type

```python
auth_type = AuthType.SSL
```

Class attribute defining that this strategy supports SSL authentication.

### authenticate

```python
async def authenticate(auth_config: ToolAuthConfig, **kwargs) -> ToolAuthResult
```

Executes SSL authentication logic and creates an SSL context.

**Parameters**:

* **auth_config**(ToolAuthConfig): Tool authentication configuration, containing SSL-related configuration parameters.
* **\*kwargs**: Additional authentication parameters.

**Returns**:

**ToolAuthResult**, authentication result containing SSL connector.

## class HeaderQueryAuthStrategy

```python
class HeaderQueryAuthStrategy(AuthStrategy)
```

Custom Header and Query parameter authentication strategy, used to add custom request headers and query parameters.

### auth_type

```python
auth_type = AuthType.HEADER_AND_QUERY
```

Class attribute defining that this strategy supports Header and Query parameter authentication.

### authenticate

```python
async def authenticate(auth_config: ToolAuthConfig, **kwargs) -> ToolAuthResult
```

Executes Header and Query parameter authentication logic and creates an authentication provider.

**Parameters**:

* **auth_config**(ToolAuthConfig): Tool authentication configuration, containing custom Header and Query parameter configurations.
* **\*kwargs**: Additional authentication parameters.

**Returns**:

**ToolAuthResult**, authentication result containing authentication provider.

## class AuthStrategyRegistry

```python
class AuthStrategyRegistry
```

Authentication strategy registry, used to manage and execute various authentication strategies.

### register

```python
@classmethod
def register(cls, strategy_class: Type[AuthStrategy])
```

Registers an authentication strategy class.

**Parameters**:

* **strategy_class**(Type[AuthStrategy]): The authentication strategy class to be registered.

### execute_auth

```python
@classmethod
async def execute_auth(cls, auth_config: ToolAuthConfig, **kwargs) -> Optional[ToolAuthResult]
```

Executes authentication logic, selecting the appropriate authentication strategy based on the authentication configuration.

**Parameters**:

* **auth_config**(ToolAuthConfig): Tool authentication configuration.
* **\*kwargs**: Additional authentication parameters.

**Returns**:

**Optional[ToolAuthResult]**, authentication result. If the authentication type is not supported, returns a failed authentication result.

## class AuthHeaderAndQueryProvider

```python
class AuthHeaderAndQueryProvider(httpx.Auth)
```

Custom Header and Query parameter authentication provider, inheriting from `httpx.Auth`, used to add custom Header and Query parameters in HTTP requests.

**Parameters**:

* **auth_headers**(Dict[str, str]): Custom request headers to be added.
* **auth_query_params**(Dict[str, str]): Custom query parameters to be added.

### async_auth_flow

```python
async def async_auth_flow(self, request: httpx.Request) -> AsyncGenerator[httpx.Request, httpx.Response]
```

Asynchronous authentication flow method, used to modify HTTP requests and add custom Header and Query parameters.

**Parameters**:

* **request**(httpx.Request): HTTP request object.

**Returns**:

**AsyncGenerator[httpx.Request, httpx.Response]**, modified HTTP request generator.

## Unified Authentication Handler

```python
@framework.on(ToolCallEvents.TOOL_AUTH)
async def unified_auth_handler(auth_config: ToolAuthConfig, **kwargs)
```

Unified authentication handler function, listening to the `ToolCallEvents.TOOL_AUTH` event through the event system, and calling `AuthStrategyRegistry.execute_auth` to execute authentication logic.

**Parameters**:

* **auth_config**(ToolAuthConfig): Tool authentication configuration.
* **\*kwargs**: Additional authentication parameters.

**Returns**:

**ToolAuthResult**, authentication result.

## Usage Examples

### 1. SSL Authentication Configuration

```python
from openjiuwen.core.foundation.tool.auth.auth import ToolAuthConfig
from openjiuwen.core.foundation.tool.auth.auth_callback import AuthType, AuthStrategyRegistry

# Create SSL authentication configuration
ssl_auth_config = ToolAuthConfig(
    auth_type=AuthType.SSL,
    config={
        "verify_switch_env": "RESTFUL_SSL_VERIFY",
        "ssl_cert_env": "RESTFUL_SSL_CERT"
    },
    tool_type="restful_api",
    tool_id="test_api"
)

# Execute authentication
result = await AuthStrategyRegistry.execute_auth(ssl_auth_config)
```

### 2. Header and Query Parameter Authentication Configuration

```python
from openjiuwen.core.foundation.tool.auth.auth import ToolAuthConfig
from openjiuwen.core.foundation.tool.auth.auth_callback import AuthType, AuthStrategyRegistry

# Create Header and Query parameter authentication configuration
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

# Execute authentication
result = await AuthStrategyRegistry.execute_auth(header_query_auth_config)
```

## Extending New Authentication Strategies

To extend a new authentication strategy, you need to:

1. Create a new class that inherits from `AuthStrategy`
2. Define the `auth_type` class attribute
3. Implement the `authenticate` method
4. Register the new authentication strategy

```python
from openjiuwen.core.foundation.tool.auth.auth import ToolAuthConfig, ToolAuthResult
from openjiuwen.core.foundation.tool.auth.auth_callback import AuthStrategy, AuthType, AuthStrategyRegistry

# Create new authentication strategy class
class ApiKeyAuthStrategy(AuthStrategy):
    auth_type = AuthType("api_key")  # Assuming AuthType enum has been extended

    async def authenticate(self, auth_config: ToolAuthConfig, **kwargs) -> ToolAuthResult:
        # Implement API Key authentication logic
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

# Register new authentication strategy
AuthStrategyRegistry.register(ApiKeyAuthStrategy)
```