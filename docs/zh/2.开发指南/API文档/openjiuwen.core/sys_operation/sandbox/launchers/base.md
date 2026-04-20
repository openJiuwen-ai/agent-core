# openjiuwen.core.sys_operation.sandbox.launchers.base

## class LaunchedSandbox

```python
@dataclass(frozen=True)
class LaunchedSandbox
```

沙箱启动后返回的描述符，用于 ContainerManager 标识运行中的沙箱实例。

**参数**：

* **base_url**(str)：沙箱服务 HTTP 基础 URL（对于 E2B 等 provider 管理的沙箱可为空字符串）。
* **sandbox_id**(str, 可选)：运行时分配的标识符（Docker 容器 id、E2B 沙箱 id 等）。对于生命周期由外部管理的远程启动器可为 `None`。默认值：`None`。
* **host_port**(int, 可选)：宿主机映射端口（仅 Docker 使用）。默认值：`None`。

---

## class SandboxLauncher

```python
class SandboxLauncher
```

沙箱生命周期管理的基类。除 `launch` 外的方法在基类中为空操作，子类只需覆盖其运行时支持的方法。

### async launch

```python
async launch(
    config: SandboxLauncherConfig,
    timeout_seconds: int,
    isolation_key: Optional[str] = None,
) -> LaunchedSandbox
```

启动（或恢复）沙箱并返回描述符。

**参数**：

* **config**([SandboxLauncherConfig](../sandbox_config.md#class-sandboxlauncherconfig))：沙箱启动器配置。
* **timeout_seconds**(int)：超时时间（秒）。
* **isolation_key**(str, 可选)：沙箱隔离键。默认值：`None`。

**返回**：

**[LaunchedSandbox](#class-launchedsandbox)**，沙箱描述符。

### async pause

```python
async pause(sandbox_id: str) -> None
```

暂停沙箱以保留状态但不消耗计算资源。

**参数**：

* **sandbox_id**(str)：沙箱标识符。

### async resume

```python
async resume(sandbox_id: str) -> None
```

恢复之前暂停的沙箱。

**参数**：

* **sandbox_id**(str)：沙箱标识符。

### async delete

```python
async delete(sandbox_id: str) -> None
```

永久销毁沙箱并释放资源。

**参数**：

* **sandbox_id**(str)：沙箱标识符。

### async check_status

```python
async check_status(sandbox_id: str) -> SandboxStatus
```

检查沙箱当前状态。

**参数**：

* **sandbox_id**(str)：沙箱标识符。

**返回**：

**[SandboxStatus](../gateway/sandbox_store.md#class-sandboxstatus)**，沙箱状态。
