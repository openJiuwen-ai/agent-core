# openjiuwen.core.sys_operation.gateway

## class SandboxEndpoint

```python
class SandboxEndpoint(BaseModel)
```

Sandbox endpoint information, containing base URL and identifier.

**Parameters**:

* **base_url**(str): Sandbox service base URL.
* **sandbox_id**(str, optional): Sandbox instance unique identifier. Default: `None`.

## class GatewayResponse

```python
class GatewayResponse(BaseModel)
```

Gateway response containing status code, message, and data.

**Parameters**:

* **code**(int): Status code, `0` indicates success.
* **message**(str): Response message.
* **data**(Any, optional): Response data. Default: `None`.

## class SandboxGateway

```python
class SandboxGateway(config: Optional[GatewayConfig] = None)
```

Sandbox gateway singleton, responsible for managing sandbox lifecycle including creation, pause, resume, and deletion.

> **Note**: Users typically interact with [SandboxGatewayClient](./gateway_client.md) rather than SandboxGateway directly.

**Parameters**:

* **config**(GatewayConfig, optional): Gateway configuration. Default: `None`.

### classmethod get_instance

```python
classmethod get_instance(config: Optional[GatewayConfig] = None) -> SandboxGateway
```

Get SandboxGateway singleton instance.

**Parameters**:

* **config**(GatewayConfig, optional): Gateway configuration. Default: `None`.

**Returns**:

**SandboxGateway**, singleton instance.

### async handle_request

```python
async handle_request(
    config: SandboxGatewayConfig,
    request: GatewayInvokeRequest
) -> GatewayResponse
```

Handle full-link request: parse endpoint → select Provider → invoke method → return result.

**Parameters**:

* **config**([SandboxGatewayConfig](../sandbox_config.md#class-sandboxgatewayconfig)): Sandbox gateway configuration.
* **request**(GatewayInvokeRequest): Gateway invoke request.

**Returns**:

**[GatewayResponse](#class-gatewayresponse)**, invoke result.

### async handle_stream_request

```python
async handle_stream_request(
    config: SandboxGatewayConfig,
    request: GatewayInvokeRequest
) -> AsyncIterator
```

Handle full-link stream request: parse endpoint → select Provider → invoke stream method → return async iterator.

**Parameters**:

* **config**([SandboxGatewayConfig](../sandbox_config.md#class-sandboxgatewayconfig)): Sandbox gateway configuration.
* **request**(GatewayInvokeRequest): Gateway invoke request.

**Returns**:

**AsyncIterator**, stream invoke result.

### async get_sandbox

```python
async get_sandbox(request: SandboxCreateRequest) -> GatewayResponse
```

Get or create sandbox endpoint.

**Parameters**:

* **request**([SandboxCreateRequest](../sandbox_config.md#class-sandboxcreaterequest)): Sandbox creation request.

**Returns**:

**[GatewayResponse](#class-gatewayresponse)**, response containing SandboxEndpoint.

### async release_sandbox

```python
async release_sandbox(isolation_key: str, on_stop: str = "delete") -> GatewayResponse
```

Release sandbox resource, execute deletion, pausing, or keeping based on strategy.

**Parameters**:

* **isolation_key**(str): Sandbox isolation key.
* **on_stop**(Literal["delete", "pause", "keep"], optional): Sandbox stop behavior strategy. Default: `"delete"`.
  * `"delete"`: Delete sandbox
  * `"pause"`: Pause sandbox
  * `"keep"`: Keep sandbox running

**Returns**:

**[GatewayResponse](#class-gatewayresponse)**, operation result.

### async pause_sandbox

```python
async pause_sandbox(record: SandboxRecord)
```

Pause sandbox.

**Parameters**:

* **record**([SandboxRecord](./sandbox_store.md#class-sandboxrecord)): Sandbox record.

### async delete_sandbox

```python
async delete_sandbox(record: SandboxRecord)
```

Delete sandbox.

**Parameters**:

* **record**([SandboxRecord](./sandbox_store.md#class-sandboxrecord)): Sandbox record.
