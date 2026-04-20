# openjiuwen.core.sys_operation.sandbox.run_config

## class SandboxRunConfig

```python
@dataclass
class SandboxRunConfig
```

Runtime configuration passed to sandbox operations. Contains the original configuration for sandbox operations. All sandbox operations (fs/shell/code) created by the same SysOperation instance share the same SandboxRunConfig object.

**Parameters**:

* **config**([SandboxGatewayConfig](./sandbox_config.md#class-sandboxgatewayconfig)): Original sandbox gateway configuration containing scope, sandbox parameters, etc.
* **isolation_key_template**(str): Isolation key template with `{session_id}` placeholder. Use `resolve_isolation_key()` to get the actual key at invoke time.
