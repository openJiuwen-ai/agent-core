# openjiuwen.core.sys_operation.sandbox.sandbox_mixin

## class SandboxGatewayClientMixin

```python
class SandboxGatewayClientMixin
```

Mixin providing gateway client management and invoke/invoke_stream methods for sandbox operations.

### _init_client_context

```python
_init_client_context(run_config: SandboxRunConfig, op_type: str)
```

Initialize client context.

**Parameters**:

* **run_config**([SandboxRunConfig](./run_config.md#class-sandboxrunconfig)): Sandbox runtime configuration.
* **op_type**(str): Operation type.

### _get_resolved_isolation_key

```python
_get_resolved_isolation_key() -> str
```

Resolve the isolation key template with current session ID.

**Returns**:

**str**, resolved isolation key.

### async _get_gateway_client

```python
async _get_gateway_client() -> SandboxGatewayClient
```

Get sandbox gateway client.

**Returns**:

**[SandboxGatewayClient](./gateway/gateway_client.md#class-sandboxgatewayclient)**, sandbox gateway client instance.

### async invoke

```python
async invoke(method: str, **params) -> Any
```

Invoke a provider method through the gateway full-chain routing.

**Parameters**:

* **method**(str): Method name.
* **params**: Method parameters.

**Returns**:

**Any**, invoke result.

### async invoke_stream

```python
async invoke_stream(method: str, **params) -> AsyncIterator
```

Invoke a streaming provider method through the gateway full-chain routing.

**Parameters**:

* **method**(str): Method name.
* **params**: Method parameters.

**Returns**:

**AsyncIterator**, stream invoke result.

---

## class BaseSandboxMixin

```python
class BaseSandboxMixin(SandboxGatewayClientMixin)
```

Mixin for sandbox operations. Provides invoke() and invoke_stream() methods after initialization.

### _init_sandbox_context

```python
_init_sandbox_context(run_config: SandboxRunConfig, op_type: str)
```

Initialize sandbox context.

**Parameters**:

* **run_config**([SandboxRunConfig](./run_config.md#class-sandboxrunconfig)): Sandbox runtime configuration.
* **op_type**(str): Operation type.
