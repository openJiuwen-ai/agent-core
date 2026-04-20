# openjiuwen.core.sys_operation.sandbox_registry

## class SandboxRegistry

```python
class SandboxRegistry
```

沙箱 Launcher 和 Provider 的注册中心，用于注册和创建自定义的 Launcher 和 Provider。

### classmethod register_launcher

```python
classmethod register_launcher(name: str, launcher_cls: Type) -> None
```

注册 Launcher 类。

**参数**：

* **name**(str)：Launcher 类型名称。
* **launcher_cls**(Type)：Launcher 类。

### classmethod get_launcher_cls

```python
classmethod get_launcher_cls(name: str) -> Optional[Type]
```

获取已注册的 Launcher 类。

**参数**：

* **name**(str)：Launcher 类型名称。

**返回**：

**Optional[Type]**，Launcher 类，如果未注册则返回 `None`。

### classmethod unregister_launcher

```python
classmethod unregister_launcher(name: str) -> None
```

注销已注册的 Launcher 类。

**参数**：

* **name**(str)：Launcher 类型名称。

### classmethod create_launcher

```python
classmethod create_launcher(launcher_type: str) -> SandboxLauncher
```

创建 Launcher 实例。

**参数**：

* **launcher_type**(str)：Launcher 类型名称。

**返回**：

**SandboxLauncher**，Launcher 实例。

**异常**：

* **ValueError**：如果 Launcher 类型未注册。

### classmethod register_provider

```python
classmethod register_provider(sandbox_type: str, operation_type: str, provider_cls: Type) -> None
```

注册 Provider 类。

**参数**：

* **sandbox_type**(str)：沙箱类型名称。
* **operation_type**(str)：操作类型（`"fs"`、`"shell"`、`"code"`）。
* **provider_cls**(Type)：Provider 类。

### classmethod get_provider_cls

```python
classmethod get_provider_cls(sandbox_type: str, operation_type: str) -> Optional[Type]
```

获取已注册的 Provider 类。

**参数**：

* **sandbox_type**(str)：沙箱类型名称。
* **operation_type**(str)：操作类型（`"fs"`、`"shell"`、`"code"`）。

**返回**：

**Optional[Type]**，Provider 类，如果未注册则返回 `None`。

### classmethod unregister_provider

```python
classmethod unregister_provider(sandbox_type: str, operation_type: str) -> None
```

注销已注册的 Provider 类。

**参数**：

* **sandbox_type**(str)：沙箱类型名称。
* **operation_type**(str)：操作类型（`"fs"`、`"shell"`、`"code"`）。

### classmethod create_provider

```python
classmethod create_provider(
    sandbox_type: str,
    operation_type: str,
    endpoint: SandboxEndpoint,
    config: Optional[SandboxGatewayConfig] = None
) -> Any
```

创建 Provider 实例。

**参数**：

* **sandbox_type**(str)：沙箱类型名称。
* **operation_type**(str)：操作类型（`"fs"`、`"shell"`、`"code"`）。
* **endpoint**([SandboxEndpoint](./gateway/gateway.md#class-sandboxendpoint))：沙箱端点信息。
* **config**([SandboxGatewayConfig](./sandbox_config.md#class-sandboxgatewayconfig), 可选)：沙箱网关配置。

**返回**：

**Any**，Provider 实例。

**异常**：

* **NotImplementedError**：如果沙箱类型不支持该操作类型。
