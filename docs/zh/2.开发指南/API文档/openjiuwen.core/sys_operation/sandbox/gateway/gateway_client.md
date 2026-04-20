# openjiuwen.core.sys_operation.gateway_client

## class SandboxGatewayClient

```python
class SandboxGatewayClient(
    config: SandboxGatewayConfig,
    isolation_key: Optional[str],
    gateway: Optional[SandboxGateway] = None
)
```

沙箱网关客户端，是用户操作沙箱的主要接口。支持端点解析和全链路调用。

**参数**：

* **config**([SandboxGatewayConfig](../sandbox_config.md#class-sandboxgatewayconfig))：沙箱网关配置。
* **isolation_key**(str, 可选)：沙箱隔离键。默认值：`None`。
* **gateway**([SandboxGateway](./gateway.md#class-sandboxgateway), 可选)：沙箱网关实例。默认值：`None`。

### async invoke

```python
async invoke(op_type: str, method: str, **params) -> Any
```

通过网关全链路路由发送调用请求。

**参数**：

* **op_type**(str)：操作类型（`"fs"`、`"shell"`、`"code"`）。
* **method**(str)：方法名称。
* **params**：方法参数。

**返回**：

**Any**，调用结果。

### async invoke_stream

```python
async invoke_stream(op_type: str, method: str, **params) -> AsyncIterator
```

通过网关全链路路由发送流式调用请求。

**参数**：

* **op_type**(str)：操作类型（`"fs"`、`"shell"`、`"code"`）。
* **method**(str)：方法名称。
* **params**：方法参数。

**返回**：

**AsyncIterator**，流式调用结果。

### async get_endpoint

```python
async get_endpoint() -> SandboxEndpoint
```

获取沙箱端点。

**返回**：

**[SandboxEndpoint](./gateway.md#class-sandboxendpoint)**，沙箱端点信息。

### staticmethod async release

```python
staticmethod async release(isolation_key: str, on_stop: str = "delete") -> None
```

静态释放方法，通过隔离键通知网关回收资源。

**参数**：

* **isolation_key**(str)：沙箱隔离键。
* **on_stop**(Literal["delete", "pause", "keep"], 可选)：沙箱停止时的行为策略。默认值：`"delete"`。
  * `"delete"`：删除沙箱
  * `"pause"`：暂停沙箱
  * `"keep"`：保持沙箱运行
