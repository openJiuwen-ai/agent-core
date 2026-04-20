# openjiuwen.core.sys_operation.sandbox.sandbox_mixin

## class SandboxGatewayClientMixin

```python
class SandboxGatewayClientMixin
```

为沙箱操作提供网关客户端管理和 invoke/invoke_stream 方法的 Mixin。

### _init_client_context

```python
_init_client_context(run_config: SandboxRunConfig, op_type: str)
```

初始化客户端上下文。

**参数**：

* **run_config**([SandboxRunConfig](./run_config.md#class-sandboxrunconfig))：沙箱运行时配置。
* **op_type**(str)：操作类型。

### _get_resolved_isolation_key

```python
_get_resolved_isolation_key() -> str
```

使用当前会话 ID 解析隔离键模板。

**返回**：

**str**，已解析的隔离键。

### async _get_gateway_client

```python
async _get_gateway_client() -> SandboxGatewayClient
```

获取沙箱网关客户端。

**返回**：

**[SandboxGatewayClient](./gateway/gateway_client.md#class-sandboxgatewayclient)**，沙箱网关客户端实例。

### async invoke

```python
async invoke(method: str, **params) -> Any
```

通过网关全链路路由调用 Provider 方法。

**参数**：

* **method**(str)：方法名称。
* **params**：方法参数。

**返回**：

**Any**，调用结果。

### async invoke_stream

```python
async invoke_stream(method: str, **params) -> AsyncIterator
```

通过网关全链路路由调用流式 Provider 方法。

**参数**：

* **method**(str)：方法名称。
* **params**：方法参数。

**返回**：

**AsyncIterator**，流式调用结果。

---

## class BaseSandboxMixin

```python
class BaseSandboxMixin(SandboxGatewayClientMixin)
```

沙箱操作的 Mixin。初始化后提供 invoke() 和 invoke_stream() 方法。

### _init_sandbox_context

```python
_init_sandbox_context(run_config: SandboxRunConfig, op_type: str)
```

初始化沙箱上下文。

**参数**：

* **run_config**([SandboxRunConfig](./run_config.md#class-sandboxrunconfig))：沙箱运行时配置。
* **op_type**(str)：操作类型。
