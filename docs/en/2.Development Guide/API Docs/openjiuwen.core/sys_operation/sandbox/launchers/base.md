# openjiuwen.core.sys_operation.sandbox.launchers.base

## class LaunchedSandbox

```python
@dataclass(frozen=True)
class LaunchedSandbox
```

Descriptor returned after sandbox launch, used by ContainerManager to identify running sandbox instances.

**Parameters**:

* **base_url**(str): HTTP base URL for the sandbox service (empty string for provider-managed sandboxes like E2B).
* **sandbox_id**(str, optional): Opaque identifier assigned by the runtime (Docker container id, E2B sandbox id, etc.). May be `None` for remote launchers where lifecycle is external. Default: `None`.
* **host_port**(int, optional): Host-side mapped port (Docker only). Default: `None`.

---

## class SandboxLauncher

```python
class SandboxLauncher
```

Base class for sandbox lifecycle management. Methods other than `launch` are no-ops in the base class; subclasses only override what their runtime supports.

### async launch

```python
async launch(
    config: SandboxLauncherConfig,
    timeout_seconds: int,
    isolation_key: Optional[str] = None,
) -> LaunchedSandbox
```

Start (or resume) a sandbox and return its descriptor.

**Parameters**:

* **config**([SandboxLauncherConfig](../sandbox_config.md#class-sandboxlauncherconfig)): Sandbox launcher configuration.
* **timeout_seconds**(int): Timeout in seconds.
* **isolation_key**(str, optional): Sandbox isolation key. Default: `None`.

**Returns**:

**[LaunchedSandbox](#class-launchedsandbox)**, sandbox descriptor.

### async pause

```python
async pause(sandbox_id: str) -> None
```

Suspend the sandbox to preserve state without consuming compute.

**Parameters**:

* **sandbox_id**(str): Sandbox identifier.

### async resume

```python
async resume(sandbox_id: str) -> None
```

Resume a previously paused sandbox.

**Parameters**:

* **sandbox_id**(str): Sandbox identifier.

### async delete

```python
async delete(sandbox_id: str) -> None
```

Permanently destroy the sandbox and release its resources.

**Parameters**:

* **sandbox_id**(str): Sandbox identifier.

### async check_status

```python
async check_status(sandbox_id: str) -> SandboxStatus
```

Check the current status of the sandbox.

**Parameters**:

* **sandbox_id**(str): Sandbox identifier.

**Returns**:

**[SandboxStatus](../gateway/sandbox_store.md#class-sandboxstatus)**, sandbox status.
