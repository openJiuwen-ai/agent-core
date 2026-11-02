# openjiuwen.core.sys_operation.gateway_client

## class SandboxGatewayClient

```python
class SandboxGatewayClient(
    config: SandboxGatewayConfig,
    isolation_key: Optional[str],
    gateway: Optional[SandboxGateway] = None
)
```

Sandbox gateway client, the main interface for users to operate sandbox. Supports endpoint resolution and full-chain invocation.

**Parameters**:

* **config**([SandboxGatewayConfig](../sandbox_config.md#class-sandboxgatewayconfig)): Sandbox gateway configuration.
* **isolation_key**(str, optional): Sandbox isolation key. Default: `None`.
* **gateway**([SandboxGateway](./gateway.md#class-sandboxgateway), optional): Sandbox gateway instance. Default: `None`.

### async invoke

A host may supply a trusted async `runtime_guard(client, op_type, *, method, params)` in launcher `extra_params`.
Return `None` to continue in the sandbox, `LocalWorkConfig` to use its local backend, or raise to block.
The guard may use `await client.prepare(op_type, create=True)` to check lifecycle barriers and prepare
an instance before dispatch; `create=False` only checks barriers. Streaming uses the same guard.
Dispatched operations are never retried by the gateway. An optional synchronous
`runtime_error_handler(error)` observes dispatched errors without changing results.
Without these callbacks, existing routing is unchanged.

`runtime_guard` is a trusted host execution-mode authorization, independent of provider `fallback_policy`.
That policy controls provider-side fallback for individual failed commands. A host can keep
`fallback_policy="never"` to prevent command replay while using its own `auto_fallback` setting to
authorize local mode after preparation fails. The host must check its setting, policy barriers and
cleanup results before returning `LocalWorkConfig`; otherwise it must return `None` or raise.
Before local execution and on guard rejection, the gateway emits through the same
`set_fallback_audit_hook` as the JiuwenBox provider, with `policy="runtime_guard"`, `provider_policy`,
`op="<op_type>.<method>"`, `inline`, `action_taken` and `reason`. Audit failures never change results
or cause dispatched commands to be retried.

For JiuwenBox, `prepare()` polls `provisioning` until `ready`, bounded by the client read timeout
(30 seconds when unset). Expiry raises an error without recreating an in-progress instance or authorizing local execution.

```python
async invoke(op_type: str, method: str, **params) -> Any
```

Send invoke request through gateway full-chain routing.

**Parameters**:

* **op_type**(str): Operation type (`"fs"`, `"shell"`, `"code"`).
* **method**(str): Method name.
* **params**: Method parameters.

**Returns**:

**Any**, invoke result.

### async invoke_stream

```python
async invoke_stream(op_type: str, method: str, **params) -> AsyncIterator
```

Send stream invoke request through gateway full-chain routing.

**Parameters**:

* **op_type**(str): Operation type (`"fs"`, `"shell"`, `"code"`).
* **method**(str): Method name.
* **params**: Method parameters.

**Returns**:

**AsyncIterator**, stream invoke result.

### async get_endpoint

```python
async get_endpoint() -> SandboxEndpoint
```

Get sandbox endpoint.

**Returns**:

**[SandboxEndpoint](./gateway.md#class-sandboxendpoint)**, sandbox endpoint information.

### staticmethod async release

```python
staticmethod async release(isolation_key: str, on_stop: str = "delete") -> None
```

Static release method, notify gateway to reclaim resources via isolation key.

**Parameters**:

* **isolation_key**(str): Sandbox isolation key.
* **on_stop**(Literal["delete", "pause", "keep"], optional): Sandbox stop behavior strategy. Default: `"delete"`.
  * `"delete"`: Delete sandbox
  * `"pause"`: Pause sandbox
  * `"keep"`: Keep sandbox running
