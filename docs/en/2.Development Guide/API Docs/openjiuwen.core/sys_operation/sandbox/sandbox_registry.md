# openjiuwen.core.sys_operation.sandbox_registry

## class SandboxRegistry

```python
class SandboxRegistry
```

Registry for sandbox Launcher and Provider.

### classmethod register_launcher

```python
classmethod register_launcher(name: str, launcher_cls: Type) -> None
```

Register Launcher class.

**Parameters**:

* **name**(str): Launcher type name.
* **launcher_cls**(Type): Launcher class.

### classmethod get_launcher_cls

```python
classmethod get_launcher_cls(name: str) -> Optional[Type]
```

Get registered Launcher class.

**Parameters**:

* **name**(str): Launcher type name.

**Returns**:

**Optional[Type]**, Launcher class, returns `None` if not registered.

### classmethod unregister_launcher

```python
classmethod unregister_launcher(name: str) -> None
```

Unregister Launcher class.

**Parameters**:

* **name**(str): Launcher type name.

### classmethod create_launcher

```python
classmethod create_launcher(launcher_type: str) -> SandboxLauncher
```

Create Launcher instance.

**Parameters**:

* **launcher_type**(str): Launcher type name.

**Returns**:

**SandboxLauncher**, Launcher instance.

**Raises**:

* **ValueError**: If Launcher type is not registered.

### classmethod register_provider

```python
classmethod register_provider(sandbox_type: str, operation_type: str, provider_cls: Type) -> None
```

Register Provider class.

**Parameters**:

* **sandbox_type**(str): Sandbox type name.
* **operation_type**(str): Operation type (`"fs"`, `"shell"`, `"code"`).
* **provider_cls**(Type): Provider class.

### classmethod get_provider_cls

```python
classmethod get_provider_cls(sandbox_type: str, operation_type: str) -> Optional[Type]
```

Get registered Provider class.

**Parameters**:

* **sandbox_type**(str): Sandbox type name.
* **operation_type**(str): Operation type (`"fs"`, `"shell"`, `"code"`).

**Returns**:

**Optional[Type]**, Provider class, returns `None` if not registered.

### classmethod unregister_provider

```python
classmethod unregister_provider(sandbox_type: str, operation_type: str) -> None
```

Unregister Provider class.

**Parameters**:

* **sandbox_type**(str): Sandbox type name.
* **operation_type**(str): Operation type (`"fs"`, `"shell"`, `"code"`).

### classmethod create_provider

```python
classmethod create_provider(
    sandbox_type: str,
    operation_type: str,
    endpoint: SandboxEndpoint,
    config: Optional[SandboxGatewayConfig] = None
) -> Any
```

Create Provider instance.

**Parameters**:

* **sandbox_type**(str): Sandbox type name.
* **operation_type**(str): Operation type (`"fs"`, `"shell"`, `"code"`).
* **endpoint**([SandboxEndpoint](./gateway/gateway.md#class-sandboxendpoint)): Sandbox endpoint information.
* **config**([SandboxGatewayConfig](./sandbox_config.md#class-sandboxgatewayconfig), optional): Sandbox gateway configuration.

**Returns**:

**Any**, Provider instance.

**Raises**:

* **NotImplementedError**: If sandbox type does not support this operation type.
