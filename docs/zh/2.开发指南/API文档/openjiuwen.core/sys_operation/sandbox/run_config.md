# openjiuwen.core.sys_operation.sandbox.run_config

## class SandboxRunConfig

```python
@dataclass
class SandboxRunConfig
```

传递给沙箱操作的运行时配置，包含沙箱操作的原始配置。同一个 SysOperation 实例创建的所有沙箱操作（fs/shell/code）共享同一个 SandboxRunConfig 对象。

**参数**：

* **config**([SandboxGatewayConfig](./sandbox_config.md#class-sandboxgatewayconfig))：原始沙箱网关配置，包含作用域、沙箱参数等。
* **isolation_key_template**(str)：包含 `{session_id}` 占位符的隔离键模板。使用 `resolve_isolation_key()` 在调用时获取实际的键。
