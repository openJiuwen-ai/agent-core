# openjiuwen.core.sys_operation.sandbox_config

## class ContainerScope

```python
class ContainerScope(str, Enum)
```

Enumeration class defining container creation granularity.

**Values**:

* **SYSTEM**: System level, all requests share the same container.
* **SESSION**: Session level, requests with the same `session_id` share the same container.
* **CUSTOM**: Custom level, use `custom_id` directly as container identifier.

## class SandboxIsolationConfig

```python
class SandboxIsolationConfig(BaseModel)
```

Sandbox isolation configuration.

**Parameters**:

* **custom_id**(str, optional): Core identity override. If set, replaces auto-generated `session_id` or `context_id`. Default: `None`.
* **container_scope**([ContainerScope](#class-containerscope), optional): Container granularity template. Default: `ContainerScope.SESSION`.
* **prefix**(str, optional): Namespace prefix for further isolation within the same scope. Default: `None`.

## class SandboxLauncherConfig

```python
class SandboxLauncherConfig(BaseModel)
```

Base class for sandbox Launcher configuration.

**Parameters**:

* **launcher_type**(str): Launcher type.
* **gateway_url**(str, optional): Remote sandbox gateway service endpoint. Default: `""`.
* **sandbox_type**(str, optional): Sandbox provider type, e.g. `"aio"`, `"e2b"`, `"mock"`. Default: `"mock"`.
* **on_stop**(Literal["delete", "pause", "keep"], optional): Sandbox stop behavior strategy. Default: `"delete"`.
  * `"delete"`: Destroy sandbox
  * `"pause"`: Pause sandbox, can be resumed on next start
  * `"keep"`: Keep sandbox running
* **idle_ttl_seconds**(int, optional): Idle timeout in seconds, auto-evict sandbox after timeout. Default: `None`.
* **extra_params**(Dict[str, Any], optional): Arbitrary parameters passed to Launcher. Default: `{}`.

## class PreDeployLauncherConfig

```python
class PreDeployLauncherConfig(SandboxLauncherConfig)
```

Pre-deploy Launcher configuration for connecting to existing sandbox services.

**Parameters**:

* **launcher_type**(Literal["pre_deploy"]): Launcher type, fixed as `"pre_deploy"`.
* **sandbox_type**(str, optional): Sandbox provider type. Default: `"aio"`.
* **base_url**(str): Sandbox service base URL (`http://` or `ws://`).
* **on_stop**(Literal["delete", "pause", "keep"], optional): Sandbox stop behavior strategy. Default: `"delete"`.
* **idle_ttl_seconds**(int, optional): Idle timeout in seconds. Default: `None`.

## class SandboxGatewayConfig

```python
class SandboxGatewayConfig(BaseModel)
```

Sandbox gateway configuration.

**Parameters**:

* **isolation**([SandboxIsolationConfig](#class-sandboxisolationconfig), optional): Sandbox container isolation and naming strategy. Default: `SandboxIsolationConfig()`.
* **launcher_config**(Union[[PreDeployLauncherConfig](#class-predeploylauncherconfig), [SandboxLauncherConfig](#class-sandboxlauncherconfig)], optional): How to obtain or connect to sandbox runtime. Default: `None`.
* **timeout_seconds**(int, optional): Unified timeout in seconds, including request and readiness check. Default: `30`.
* **auth_headers**(Dict[str, str], optional): Authentication HTTP headers. Default: `{}`.
* **auth_query_params**(Dict[str, str], optional): Authentication query parameters. Default: `{}`.

## class GatewayStoreConfig

```python
class GatewayStoreConfig(BaseModel)
```

Gateway store configuration.

**Parameters**:

* **type**(str, optional): Store type, currently only supports `"memory"`. Default: `"memory"`.
* **redis_url**(str, optional): Redis URL. Default: `None`.

## class GatewayConfig

```python
class GatewayConfig(BaseModel)
```

Gateway configuration.

**Parameters**:

* **store**([GatewayStoreConfig](#class-gatewaystoreconfig), optional): Store configuration. Default: `GatewayStoreConfig()`.

## class SandboxCreateRequest

```python
class SandboxCreateRequest(BaseModel)
```

Sandbox creation request.

**Parameters**:

* **isolation_key**(str, optional): Sandbox isolation key. Default: `None`.
* **config**([SandboxGatewayConfig](#class-sandboxgatewayconfig)): Sandbox gateway configuration.

## class GatewayInvokeRequest

```python
class GatewayInvokeRequest(BaseModel)
```

Gateway invoke request for full-link routing.

**Parameters**:

* **op_type**(str): Operation type, `"fs"` / `"shell"` / `"code"`.
* **method**(str): Method name, e.g. `"read_file"`, `"execute_cmd"`.
* **params**(Dict[str, Any], optional): Method parameters. Default: `{}`.
* **isolation_key**(str, optional): Sandbox isolation key. Default: `None`.

## class LocalWorkConfig

```python
class LocalWorkConfig(BaseModel)
```

Local work configuration for LOCAL mode security boundaries and command restrictions.

**Parameters**:

* **shell_allowlist**(List[str], optional): Allowed command prefix list. If `None`, all commands are allowed (unsafe). Default: `["echo", "rg", "ls", ...]`.
* **sandbox_root**(List[str], optional): Filesystem operation security boundary. When `restrict_to_sandbox` is `True`, denies access to paths outside this list. Default: `None`.
* **restrict_to_sandbox**(bool, optional): Whether to restrict filesystem operations within `sandbox_root`. Default: `False`.
* **dangerous_patterns**(List[str], optional): Dangerous command regex pattern list. If `None`, uses built-in default patterns. Default: `None`.
