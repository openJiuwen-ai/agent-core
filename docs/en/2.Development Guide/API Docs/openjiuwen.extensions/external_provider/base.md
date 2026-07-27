# openjiuwen.extensions.external_provider.base

## class openjiuwen.extensions.external_provider.base.ExternalAuthProvider

```python
class openjiuwen.extensions.external_provider.base.ExternalAuthProvider(Protocol)
```

Common auth protocol for external account providers (`typing.Protocol`, `runtime_checkable`). Provider login flows remain provider-specific; this protocol only covers the stable operations callers can share once credentials exist.

### status

```python
status() -> Any
```

Return provider-specific auth status.

**Returns**:

A provider-defined auth status object, e.g. `OpenAIAccountAuthStatus`.

### logout

```python
logout() -> bool
```

Remove provider credentials if present.

**Returns**:

**bool**: Whether credentials were removed.

### resolve_access_token

```python
resolve_access_token(*, force_refresh: bool = False) -> str
```

Return a usable access token, refreshing it when needed.

**Parameters**:

- **force_refresh** (bool, optional): Whether to force a token refresh even if the current token has not expired. Default value: `False`.

**Returns**:

**str**: A usable access token.

---

## class openjiuwen.extensions.external_provider.base.ProviderModelCatalog

```python
class openjiuwen.extensions.external_provider.base.ProviderModelCatalog(Protocol)
```

Common model-discovery protocol for external providers (`typing.Protocol`, `runtime_checkable`).

### list_model_ids

```python
list_model_ids(**kwargs: Any) -> list[str]
```

Return available model IDs for this provider.

**Parameters**:

- **kwargs**: Additional implementation-defined parameters, such as an auth manager or a force-refresh flag.

**Returns**:

**list[str]**: List of available model IDs.
