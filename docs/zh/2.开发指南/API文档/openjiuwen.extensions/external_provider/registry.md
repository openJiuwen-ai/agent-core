# openjiuwen.extensions.external_provider.registry

外部 Provider 的工厂注册表，分别维护鉴权 Provider（`ExternalAuthProvider`）和模型目录 Provider（`ProviderModelCatalog`）两类工厂。首次查找时会自动注册内置的 `OpenAIAccount` Provider。

## class openjiuwen.extensions.external_provider.registry.ExternalProviderRegistryError

```python
class openjiuwen.extensions.external_provider.registry.ExternalProviderRegistryError(ValueError)
```

当外部 Provider 缺失或注册不正确时抛出。

---

## register_auth_provider

```python
register_auth_provider(provider: str, factory: Callable[..., ExternalAuthProvider], *, replace: bool = False) -> None
```

注册一个鉴权 Provider 工厂。

**参数**：

- **provider**(str)：Provider 名称，按大小写不敏感方式匹配。
- **factory**(Callable[..., ExternalAuthProvider])：创建 `ExternalAuthProvider` 实例的可调用对象。
- **replace**(bool，可选)：是否允许覆盖已注册的同名 Provider。默认值：`False`（重复注册会抛出 `ExternalProviderRegistryError`）。

---

## register_model_catalog

```python
register_model_catalog(provider: str, factory: Callable[..., ProviderModelCatalog], *, replace: bool = False) -> None
```

注册一个模型目录 Provider 工厂。参数含义与 `register_auth_provider` 相同。

---

## get_auth_provider_factory

```python
get_auth_provider_factory(provider: str) -> Callable[..., ExternalAuthProvider]
```

获取指定 Provider 名称对应的鉴权 Provider 工厂；首次调用时会自动注册内置 Provider。

**异常**：

- **ExternalProviderRegistryError**：Provider 未注册时抛出，异常信息包含当前可用的 Provider 列表。

---

## get_model_catalog_factory

```python
get_model_catalog_factory(provider: str) -> Callable[..., ProviderModelCatalog]
```

获取指定 Provider 名称对应的模型目录 Provider 工厂，行为与 `get_auth_provider_factory` 类似。

---

## create_auth_provider

```python
create_auth_provider(provider: str, **kwargs: Any) -> ExternalAuthProvider
```

按名称创建一个新的鉴权 Provider 实例，`kwargs` 会透传给对应的工厂函数。

---

## create_model_catalog

```python
create_model_catalog(provider: str, **kwargs: Any) -> ProviderModelCatalog
```

按名称创建一个新的模型目录 Provider 实例，`kwargs` 会透传给对应的工厂函数。

---

## list_auth_providers

```python
list_auth_providers() -> list[str]
```

返回已注册的鉴权 Provider 名称列表（按字母顺序排序），首次调用时会自动注册内置 Provider。

---

## list_model_catalogs

```python
list_model_catalogs() -> list[str]
```

返回已注册的模型目录 Provider 名称列表（按字母顺序排序）。

**样例**：

```python
from openjiuwen.extensions.external_provider import (
    create_auth_provider,
    list_auth_providers,
    register_auth_provider,
)

# 查看内置支持的鉴权 Provider
print(list_auth_providers())  # ['OpenAIAccount']

# 创建内置的 OpenAIAccount 鉴权 Provider
auth_provider = create_auth_provider("OpenAIAccount")

# 注册自定义 Provider
register_auth_provider("MyProvider", MyAuthProviderFactory)
```
