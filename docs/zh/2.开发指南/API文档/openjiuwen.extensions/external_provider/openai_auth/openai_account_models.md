# openjiuwen.extensions.external_provider.openai_auth.openai_account_models

OpenAI 账户后端模型列表的获取与本地缓存，实现 `ProviderModelCatalog` 协议。

**常量**：

- **DEFAULT_OPENAI_ACCOUNT_MODELS**：内置的兜底模型 ID 列表（例如 `gpt-5.5`、`gpt-5.4`、`gpt-5-codex` 等），在在线接口和本地缓存均不可用时使用。

---

## class openjiuwen.extensions.external_provider.openai_auth.openai_account_models.OpenAIAccountModelListError

```python
class openjiuwen.extensions.external_provider.openai_auth.openai_account_models.OpenAIAccountModelListError(message: str, *, status_code: Optional[int] = None)
```

当无法通过在线接口获取 OpenAI 账户模型列表时抛出。

---

## class openjiuwen.extensions.external_provider.openai_auth.openai_account_models.OpenAIAccountModelCatalog

```python
class openjiuwen.extensions.external_provider.openai_auth.openai_account_models.OpenAIAccountModelCatalog(*, base_url: str = DEFAULT_OPENAI_ACCOUNT_BASE_URL, cache_path: Optional[str | Path] = None, timeout_seconds: float = 10.0, client_version: str = OPENAI_ACCOUNT_MODELS_CLIENT_VERSION, transport: Optional[httpx.BaseTransport] = None, verify: Union[bool, str, Any] = True, proxy: Optional[str] = None, max_retries: int = 0, now: Optional[Callable[[], float]] = None)
```

获取并缓存 OpenAI 账户后端的模型列表。内部使用同步的 `httpx` 客户端；如果在异步上下文中调用且事件循环阻塞是问题，调用方应自行使用 `asyncio.to_thread` 包装。

**参数**：

- **base_url**(str，可选)：OpenAI 账户后端地址。默认值：`DEFAULT_OPENAI_ACCOUNT_BASE_URL`。
- **cache_path**(Optional[str | Path]，可选)：本地缓存文件路径。默认值：由 `default_openai_account_models_cache_path()` 决定。
- **timeout_seconds**(float，可选)：请求超时时间（秒）。默认值：`10.0`。
- **client_version**(str，可选)：请求携带的客户端版本号。默认值：`"1.0.0"`。
- **transport**(Optional[httpx.BaseTransport]，可选)：自定义 HTTP 传输层，主要用于测试。默认值：`None`。
- **verify**(Union[bool, str, Any]，可选)：SSL 验证配置。默认值：`True`。
- **proxy**(Optional[str]，可选)：代理地址。默认值：`None`。
- **max_retries**(int，可选)：连接失败时的最大重试次数。默认值：`0`。

### list_model_ids

```python
list_model_ids(*, auth_manager: Any = None, access_token: Optional[str] = None, force_refresh: bool = False) -> list[str]
```

返回可用的 OpenAI 账户模型 ID 列表，按「在线接口 → 本地缓存 → 内置列表」的顺序依次回退。

**参数**：

- **auth_manager**(Any，可选)：提供 `resolve_access_token()` 的鉴权管理器（例如 `OpenAIAccountAuthManager`），在未直接传入 `access_token` 时使用。默认值：`None`。
- **access_token**(Optional[str]，可选)：直接指定访问令牌，优先于 `auth_manager`。默认值：`None`。
- **force_refresh**(bool，可选)：是否强制刷新访问令牌后再请求。默认值：`False`。

**返回**：

**list[str]**：可用模型 ID 列表。

### fetch_models

```python
fetch_models(*, access_token: str) -> tuple[dict[str, Any], list[str]]
```

直接调用在线接口获取模型列表，不使用缓存兜底。失败时抛出 `OpenAIAccountModelListError`。

**返回**：

**tuple[dict[str, Any], list[str]]**：原始响应载荷与解析出的模型 ID 列表。

### read_cache_model_ids

```python
read_cache_model_ids() -> list[str]
```

从本地缓存文件读取模型 ID 列表；缓存不存在或解析失败时返回空列表。

### write_cache

```python
write_cache(*, payload: dict[str, Any], model_ids: list[str]) -> None
```

将模型列表写入本地缓存文件（原子写入）。

**样例**：

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
