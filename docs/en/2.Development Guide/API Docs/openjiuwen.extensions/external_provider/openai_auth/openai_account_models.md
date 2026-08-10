# openjiuwen.extensions.external_provider.openai_auth.openai_account_models

Fetching and local caching of the OpenAI account backend model list, implementing the `ProviderModelCatalog` protocol.

**Constants**:

- **DEFAULT_OPENAI_ACCOUNT_MODELS**: Built-in fallback list of model IDs (e.g. `gpt-5.5`, `gpt-5.4`, `gpt-5-codex`, etc.), used when both the live endpoint and the local cache are unavailable.

---

## class openjiuwen.extensions.external_provider.openai_auth.openai_account_models.OpenAIAccountModelListError

```python
class openjiuwen.extensions.external_provider.openai_auth.openai_account_models.OpenAIAccountModelListError(message: str, *, status_code: Optional[int] = None)
```

Raised when the OpenAI account model list cannot be fetched from the live endpoint.

---

## class openjiuwen.extensions.external_provider.openai_auth.openai_account_models.OpenAIAccountModelCatalog

```python
class openjiuwen.extensions.external_provider.openai_auth.openai_account_models.OpenAIAccountModelCatalog(*, base_url: str = DEFAULT_OPENAI_ACCOUNT_BASE_URL, cache_path: Optional[str | Path] = None, timeout_seconds: float = 10.0, client_version: str = OPENAI_ACCOUNT_MODELS_CLIENT_VERSION, transport: Optional[httpx.BaseTransport] = None, verify: Union[bool, str, Any] = True, proxy: Optional[str] = None, max_retries: int = 0, now: Optional[Callable[[], float]] = None)
```

Fetches and caches the OpenAI account backend model list. Uses a synchronous `httpx` client internally; callers in an async context that care about blocking the event loop should wrap discovery with `asyncio.to_thread`.

**Parameters**:

- **base_url** (str, optional): OpenAI account backend address. Default value: `DEFAULT_OPENAI_ACCOUNT_BASE_URL`.
- **cache_path** (Optional[str | Path], optional): Local cache file path. Default value: determined by `default_openai_account_models_cache_path()`.
- **timeout_seconds** (float, optional): Request timeout (seconds). Default value: `10.0`.
- **client_version** (str, optional): Client version sent with requests. Default value: `"1.0.0"`.
- **transport** (Optional[httpx.BaseTransport], optional): Custom HTTP transport, mainly for testing. Default value: `None`.
- **verify** (Union[bool, str, Any], optional): SSL verification setting. Default value: `True`.
- **proxy** (Optional[str], optional): Proxy address. Default value: `None`.
- **max_retries** (int, optional): Maximum retries on connection failure. Default value: `0`.

### list_model_ids

```python
list_model_ids(*, auth_manager: Any = None, access_token: Optional[str] = None, force_refresh: bool = False) -> list[str]
```

Return the available OpenAI account model IDs, falling back in order: live endpoint → local cache → built-in list.

**Parameters**:

- **auth_manager** (Any, optional): An auth manager exposing `resolve_access_token()` (e.g. `OpenAIAccountAuthManager`), used when `access_token` is not passed directly. Default value: `None`.
- **access_token** (Optional[str], optional): Access token to use directly, takes precedence over `auth_manager`. Default value: `None`.
- **force_refresh** (bool, optional): Whether to force-refresh the access token before requesting. Default value: `False`.

**Returns**:

**list[str]**: List of available model IDs.

### fetch_models

```python
fetch_models(*, access_token: str) -> tuple[dict[str, Any], list[str]]
```

Fetch the model list directly from the live endpoint, without falling back to the cache. Raises `OpenAIAccountModelListError` on failure.

**Returns**:

**tuple[dict[str, Any], list[str]]**: The raw response payload and the parsed model ID list.

### read_cache_model_ids

```python
read_cache_model_ids() -> list[str]
```

Read the model ID list from the local cache file; returns an empty list if the cache does not exist or fails to parse.

### write_cache

```python
write_cache(*, payload: dict[str, Any], model_ids: list[str]) -> None
```

Write the model list to the local cache file (atomic write).

**Example**:

```python
from openjiuwen.extensions.external_provider.openai_auth import (
    OpenAIAccountAuthManager,
    OpenAIAccountModelCatalog,
)

auth_manager = OpenAIAccountAuthManager()
catalog = OpenAIAccountModelCatalog()

model_ids = catalog.list_model_ids(auth_manager=auth_manager)
print(model_ids)
```
