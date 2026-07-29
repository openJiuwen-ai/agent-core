# openjiuwen.extensions.external_provider.registry

Factory registry for external providers, maintaining two kinds of factories: auth providers (`ExternalAuthProvider`) and model catalog providers (`ProviderModelCatalog`). The built-in `OpenAIAccount` provider is registered automatically on first lookup.

## class openjiuwen.extensions.external_provider.registry.ExternalProviderRegistryError

```python
class openjiuwen.extensions.external_provider.registry.ExternalProviderRegistryError(ValueError)
```

Raised when an external provider is missing or registered incorrectly.

---

## register_auth_provider

```python
register_auth_provider(provider: str, factory: Callable[..., ExternalAuthProvider], *, replace: bool = False) -> None
```

Register an auth provider factory.

**Parameters**:

- **provider** (str): Provider name, matched case-insensitively.
- **factory** (Callable[..., ExternalAuthProvider]): Callable that creates an `ExternalAuthProvider` instance.
- **replace** (bool, optional): Whether to allow overwriting an already-registered provider with the same name. Default value: `False` (re-registration raises `ExternalProviderRegistryError`).

---

## register_model_catalog

```python
register_model_catalog(provider: str, factory: Callable[..., ProviderModelCatalog], *, replace: bool = False) -> None
```

Register a model catalog provider factory. Parameters have the same meaning as `register_auth_provider`.

---

## get_auth_provider_factory

```python
get_auth_provider_factory(provider: str) -> Callable[..., ExternalAuthProvider]
```

Return the auth provider factory for a provider name; built-in providers are registered automatically on first call.

**Raises**:

- **ExternalProviderRegistryError**: Raised when the provider is not registered; the error message lists the currently available providers.

---

## get_model_catalog_factory

```python
get_model_catalog_factory(provider: str) -> Callable[..., ProviderModelCatalog]
```

Return the model catalog provider factory for a provider name; behaves like `get_auth_provider_factory`.

---

## create_auth_provider

```python
create_auth_provider(provider: str, **kwargs: Any) -> ExternalAuthProvider
```

Create a new auth provider instance by name. `kwargs` are forwarded to the corresponding factory.

---

## create_model_catalog

```python
create_model_catalog(provider: str, **kwargs: Any) -> ProviderModelCatalog
```

Create a new model catalog instance by name. `kwargs` are forwarded to the corresponding factory.

---

## list_auth_providers

```python
list_auth_providers() -> list[str]
```

Return the registered auth provider names, sorted alphabetically; built-in providers are registered automatically on first call.

---

## list_model_catalogs

```python
list_model_catalogs() -> list[str]
```

Return the registered model catalog provider names, sorted alphabetically.

**Example**:

```python
from openjiuwen.extensions.external_provider import (
    create_auth_provider,
    list_auth_providers,
    register_auth_provider,
)

# See the built-in auth providers
print(list_auth_providers())  # ['OpenAIAccount']

# Create the built-in OpenAIAccount auth provider
auth_provider = create_auth_provider("OpenAIAccount")

# Register a custom provider
register_auth_provider("MyProvider", MyAuthProviderFactory)
```
