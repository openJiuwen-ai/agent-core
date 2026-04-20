# openjiuwen.core.sys_operation.gateway

## class SandboxEndpoint

```python
class SandboxEndpoint(BaseModel)
```

沙箱端点信息，包含沙箱的基础 URL 和标识符。

**参数**：

* **base_url**(str)：沙箱服务基础 URL。
* **sandbox_id**(str, 可选)：沙箱实例的唯一标识符。默认值：`None`。

## class GatewayResponse

```python
class GatewayResponse(BaseModel)
```

网关响应，包含状态码、消息和数据。

**参数**：

* **code**(int)：状态码，`0` 表示成功。
* **message**(str)：响应消息。
* **data**(Any, 可选)：响应数据。默认值：`None`。

## class SandboxGateway

```python
class SandboxGateway(config: Optional[GatewayConfig] = None)
```

沙箱网关单例，负责管理沙箱的生命周期，包括创建、暂停、恢复、删除等操作。

> **说明**：用户通常不直接操作 SandboxGateway，而是通过 [SandboxGatewayClient](./gateway_client.md) 进行操作。

**参数**：

* **config**(GatewayConfig, 可选)：网关配置。默认值：`None`。

### classmethod get_instance

```python
classmethod get_instance(config: Optional[GatewayConfig] = None) -> SandboxGateway
```

获取 SandboxGateway 单例实例。

**参数**：

* **config**(GatewayConfig, 可选)：网关配置。默认值：`None`。

**返回**：

**SandboxGateway**，单例实例。

### async handle_request

```python
async handle_request(
    config: SandboxGatewayConfig,
    request: GatewayInvokeRequest
) -> GatewayResponse
```

处理全链路请求：解析端点 → 选择 Provider → 调用方法 → 返回结果。

**参数**：

* **config**([SandboxGatewayConfig](../sandbox_config.md#class-sandboxgatewayconfig))：沙箱网关配置。
* **request**(GatewayInvokeRequest)：网关调用请求。

**返回**：

**[GatewayResponse](#class-gatewayresponse)**，调用结果。

### async handle_stream_request

```python
async handle_stream_request(
    config: SandboxGatewayConfig,
    request: GatewayInvokeRequest
) -> AsyncIterator
```

处理全链路流式请求：解析端点 → 选择 Provider → 调用流式方法 → 返回异步迭代器。

**参数**：

* **config**([SandboxGatewayConfig](../sandbox_config.md#class-sandboxgatewayconfig))：沙箱网关配置。
* **request**(GatewayInvokeRequest)：网关调用请求。

**返回**：

**AsyncIterator**，流式调用结果。

### async get_sandbox

```python
async get_sandbox(request: SandboxCreateRequest) -> GatewayResponse
```

获取或创建沙箱端点。

**参数**：

* **request**([SandboxCreateRequest](../sandbox_config.md#class-sandboxcreaterequest))：沙箱创建请求。

**返回**：

**[GatewayResponse](#class-gatewayresponse)**，包含 SandboxEndpoint 的响应。

### async release_sandbox

```python
async release_sandbox(isolation_key: str, on_stop: str = "delete") -> GatewayResponse
```

释放沙箱资源，根据策略执行删除、暂停或保持运行。

**参数**：

* **isolation_key**(str)：沙箱隔离键。
* **on_stop**(Literal["delete", "pause", "keep"], 可选)：沙箱停止时的行为策略。默认值：`"delete"`。
  * `"delete"`：删除沙箱
  * `"pause"`：暂停沙箱
  * `"keep"`：保持沙箱运行

**返回**：

**[GatewayResponse](#class-gatewayresponse)**，操作结果。

### async pause_sandbox

```python
async pause_sandbox(record: SandboxRecord)
```

暂停沙箱。

**参数**：

* **record**(SandboxRecord)：沙箱记录。

### async delete_sandbox

```python
async delete_sandbox(record: SandboxRecord)
```

删除沙箱。

**参数**：

* **record**(SandboxRecord)：沙箱记录。
