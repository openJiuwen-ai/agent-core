# openjiuwen.core.sys_operation.sandbox_config

## class ContainerScope

```python
class ContainerScope(str, Enum)
```

定义容器创建粒度的枚举类，用于决定请求如何路由到容器实例。

**参数**：

* **SYSTEM**：系统级别，所有请求共享同一个容器。
* **SESSION**：会话级别，相同 `session_id` 的请求共享同一个容器。
* **CUSTOM**：自定义级别，使用 `custom_id` 直接作为容器标识。

## class SandboxIsolationConfig

```python
class SandboxIsolationConfig(BaseModel)
```

沙箱隔离配置，用于配置容器的隔离粒度和命名策略。

**参数**：

* **custom_id**(str, 可选)：核心身份覆盖。如果设置，将替换自动生成的 `session_id` 或 `context_id`。默认值：`None`。
* **container_scope**([ContainerScope](#class-containerscope), 可选)：容器粒度模板。默认值：`ContainerScope.SESSION`。
* **prefix**(str, 可选)：命名空间前缀，用于同一 scope 下进一步隔离。默认值：`None`。

**样例**：

```python
from openjiuwen.core.sys_operation.config import (
    SandboxIsolationConfig,
    ContainerScope,
)

# 系统级别隔离
isolation_config = SandboxIsolationConfig(
    container_scope=ContainerScope.SYSTEM
)

# 会话级别隔离，带前缀
isolation_config = SandboxIsolationConfig(
    container_scope=ContainerScope.SESSION,
    prefix="agent1_"
)

# 自定义级别隔离
isolation_config = SandboxIsolationConfig(
    container_scope=ContainerScope.CUSTOM,
    custom_id="my_custom_sandbox"
)
```

## class SandboxLauncherConfig

```python
class SandboxLauncherConfig(BaseModel)
```

沙箱 Launcher 配置的基类，定义了 Launcher 的基本参数。

**参数**：

* **launcher_type**(str)：Launcher 类型。
* **gateway_url**(str, 可选)：远程沙箱网关服务端点。默认值：`""`。
* **sandbox_type**(str, 可选)：沙箱提供者类型，如 `"aio"`、`"e2b"`、`"mock"`。默认值：`"mock"`。
* **on_stop**(Literal["delete", "pause", "keep"], 可选)：沙箱停止时的行为策略。默认值：`"delete"`。
  * `"delete"`：销毁沙箱
  * `"pause"`：暂停沙箱，下次启动时可恢复
  * `"keep"`：保持沙箱运行
* **idle_ttl_seconds**(int, 可选)：空闲超时时间（秒），超时后自动驱逐沙箱。默认值：`None`。
* **extra_params**(Dict[str, Any], 可选)：传递给 Launcher 的任意参数。默认值：`{}`。

## class PreDeployLauncherConfig

```python
class PreDeployLauncherConfig(SandboxLauncherConfig)
```

预部署 Launcher 配置，用于连接已存在的沙箱服务。当沙箱进程由外部管理（例如已在服务器上运行或由 sidecar 启动）时使用此配置。Launcher 仅返回提供的 `base_url`，不启动任何进程。

**参数**：

* **launcher_type**(Literal["pre_deploy"])：Launcher 类型，固定为 `"pre_deploy"`。
* **sandbox_type**(str, 可选)：沙箱提供者类型。默认值：`"aio"`。
* **base_url**(str)：沙箱服务基础 URL（`http://` 或 `ws://`）。
* **on_stop**(Literal["delete", "pause", "keep"], 可选)：沙箱停止时的行为策略。默认值：`"delete"`。
* **idle_ttl_seconds**(int, 可选)：空闲超时时间（秒）。默认值：`None`。

**样例**：

```python
from openjiuwen.core.sys_operation.config import PreDeployLauncherConfig

# 连接已启动的 AIO 沙箱
launcher_config = PreDeployLauncherConfig(
    base_url="http://localhost:8080",
    sandbox_type="aio",
    idle_ttl_seconds=600,
)
```

## class SandboxGatewayConfig

```python
class SandboxGatewayConfig(BaseModel)
```

沙箱网关配置，包含隔离配置、Launcher 配置、超时设置等。

**参数**：

* **isolation**([SandboxIsolationConfig](#class-sandboxisolationconfig), 可选)：沙箱容器的隔离和命名策略。默认值：`SandboxIsolationConfig()`。
* **launcher_config**(Union[[PreDeployLauncherConfig](#class-predeploylauncherconfig), [SandboxLauncherConfig](#class-sandboxlauncherconfig)], 可选)：如何获取或连接沙箱运行时。默认值：`None`。
* **timeout_seconds**(int, 可选)：统一超时时间（秒），包含请求和就绪检查。默认值：`30`。
* **auth_headers**(Dict[str, str], 可选)：认证 HTTP 头。默认值：`{}`。
* **auth_query_params**(Dict[str, str], 可选)：认证查询参数。默认值：`{}`。

**样例**：

```python
from openjiuwen.core.sys_operation.config import (
    SandboxGatewayConfig,
    SandboxIsolationConfig,
    PreDeployLauncherConfig,
    ContainerScope,
)

# 创建沙箱网关配置（系统级别，直连 AIO 沙箱）
gateway_config = SandboxGatewayConfig(
    isolation=SandboxIsolationConfig(
        container_scope=ContainerScope.SYSTEM
    ),
    launcher_config=PreDeployLauncherConfig(
        base_url="http://localhost:8080",
        sandbox_type="aio",
        idle_ttl_seconds=600,
    ),
    timeout_seconds=30,
)

# 创建沙箱网关配置（会话级别，带前缀）
gateway_config = SandboxGatewayConfig(
    isolation=SandboxIsolationConfig(
        container_scope=ContainerScope.SESSION,
        prefix="agent1_"
    ),
    launcher_config=PreDeployLauncherConfig(
        base_url="http://localhost:8080",
        sandbox_type="aio",
    ),
    timeout_seconds=30,
)
```

## class GatewayStoreConfig

```python
class GatewayStoreConfig(BaseModel)
```

网关存储配置。

**参数**：

* **type**(str, 可选)：存储类型，当前仅支持 `"memory"`。默认值：`"memory"`。
* **redis_url**(str, 可选)：Redis URL。默认值：`None`。

## class GatewayConfig

```python
class GatewayConfig(BaseModel)
```

网关配置。

**参数**：

* **store**([GatewayStoreConfig](#class-gatewaystoreconfig), 可选)：存储配置。默认值：`GatewayStoreConfig()`。

## class SandboxCreateRequest

```python
class SandboxCreateRequest(BaseModel)
```

沙箱创建请求。

**参数**：

* **isolation_key**(str, 可选)：沙箱隔离键。默认值：`None`。
* **config**([SandboxGatewayConfig](#class-sandboxgatewayconfig))：沙箱网关配置。

## class GatewayInvokeRequest

```python
class GatewayInvokeRequest(BaseModel)
```

网关调用请求，用于全链路路由。

**参数**：

* **op_type**(str)：操作类型，`"fs"` / `"shell"` / `"code"`。
* **method**(str)：方法名，如 `"read_file"`、`"execute_cmd"`。
* **params**(Dict[str, Any], 可选)：方法参数。默认值：`{}`。
* **isolation_key**(str, 可选)：沙箱隔离键。默认值：`None`。

## class LocalWorkConfig

```python
class LocalWorkConfig(BaseModel)
```

本地工作配置，用于 LOCAL 模式的安全边界和命令限制。

**参数**：

* **shell_allowlist**(List[str], 可选)：允许的命令前缀列表。如果为 `None`，则允许所有命令（不安全）。默认值：`["echo", "rg", "ls", ...]`。
* **sandbox_root**(List[str], 可选)：文件系统操作的安全边界。当 `restrict_to_sandbox` 为 `True` 时，拒绝访问此列表之外的路径。默认值：`None`。
* **restrict_to_sandbox**(bool, 可选)：是否限制文件系统操作在 `sandbox_root` 内。默认值：`False`。
* **dangerous_patterns**(List[str], 可选)：危险命令的正则模式列表。如果为 `None`，使用内置默认模式。默认值：`None`。
