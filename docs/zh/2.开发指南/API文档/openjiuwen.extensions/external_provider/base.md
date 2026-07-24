# openjiuwen.extensions.external_provider.base

## class openjiuwen.extensions.external_provider.base.ExternalAuthProvider

```python
class openjiuwen.extensions.external_provider.base.ExternalAuthProvider(Protocol)
```

外部账户 Provider 的通用鉴权协议（`typing.Protocol`，`runtime_checkable`）。各 Provider 的登录流程各不相同，该协议只约定凭据建立后可共享的稳定操作。

### status

```python
status() -> Any
```

返回 Provider 特定的鉴权状态对象。

**返回**：

Provider 自定义的鉴权状态对象，例如 `OpenAIAccountAuthStatus`。

### logout

```python
logout() -> bool
```

如果存在已保存的凭据，则移除。

**返回**：

**bool**：是否移除了凭据。

### resolve_access_token

```python
resolve_access_token(*, force_refresh: bool = False) -> str
```

返回可用的访问令牌，必要时自动刷新。

**参数**：

- **force_refresh**(bool，可选)：是否强制刷新令牌，即使当前令牌尚未过期。默认值：`False`。

**返回**：

**str**：可用的访问令牌。

---

## class openjiuwen.extensions.external_provider.base.ProviderModelCatalog

```python
class openjiuwen.extensions.external_provider.base.ProviderModelCatalog(Protocol)
```

外部 Provider 的通用模型发现协议（`typing.Protocol`，`runtime_checkable`）。

### list_model_ids

```python
list_model_ids(**kwargs: Any) -> list[str]
```

返回该 Provider 当前可用的模型 ID 列表。

**参数**：

- **kwargs**：由具体实现定义的额外参数，例如鉴权管理器、是否强制刷新等。

**返回**：

**list[str]**：可用模型 ID 列表。
