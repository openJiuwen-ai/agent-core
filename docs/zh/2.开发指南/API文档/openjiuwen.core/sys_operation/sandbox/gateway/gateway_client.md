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

宿主可在 launcher 的 `extra_params` 中传入可信的异步回调 `runtime_guard(client, op_type, *, method, params)`。
回调返回 `None` 时继续沙箱执行，返回 `LocalWorkConfig` 时本次操作使用对应本地后端，异常则阻断。
回调可调用 `await client.prepare(op_type, create=True)` 在命令发送前检查生命周期阻断并准备实例；
`create=False` 只检查阻断。该机制同样作用于流式调用，网关不会重试已经发送的操作。
可选同步 `runtime_error_handler(error)` 仅记录发送后的错误，不改变执行结果；未提供回调时原行为不变。

`runtime_guard` 是可信宿主的执行模式授权入口，**不受 provider 的 `fallback_policy` 二次裁决**。
该策略只控制 provider 内部的单次命令失败兜底；例如宿主可保留 `fallback_policy="never"` 来禁止命令重放，
同时由自身的 `auto_fallback` 开关决定准备失败后是否切换本地模式。宿主必须先校验该开关、策略屏障及清理结果，
仅在允许本地执行时返回 `LocalWorkConfig`，否则返回 `None` 或抛错。
网关在本地操作开始前及 guard 拒绝时，均通过与 JiuwenBox provider 共用的 `set_fallback_audit_hook` 发出事件：
`policy="runtime_guard"`、`provider_policy`、`op="<op_type>.<method>"`、`inline`、`action_taken` 和 `reason`。
审计 hook 异常不改变执行结果；已发送的命令不会因此重试。

`prepare()` 对 JiuwenBox 的 `provisioning` 相位等待至 `ready`，等待期限取客户端读取超时（未设时 30 秒）；
超时返回错误，不把仍在创建的实例当作可重建对象，也不授权本地执行。

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
