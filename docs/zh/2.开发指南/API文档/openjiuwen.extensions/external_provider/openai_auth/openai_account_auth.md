# openjiuwen.extensions.external_provider.openai_auth.openai_account_auth

OpenAI 账户 OAuth 凭据的存储、登录与刷新逻辑，实现 `ExternalAuthProvider` 协议。

**常量**：

- **OPENAI_ACCOUNT_PROVIDER** = `"OpenAIAccount"`：Provider 名称，与 `ModelClientConfig.client_provider` 及 `external_provider` 注册表中使用的名称一致。
- **DEFAULT_OPENAI_ACCOUNT_BASE_URL** = `"https://chatgpt.com/backend-api/codex"`：默认的 OpenAI 账户后端地址，可通过环境变量 `OPENJIUWEN_OPENAI_ACCOUNT_BASE_URL` 覆盖。

---

## class openjiuwen.extensions.external_provider.openai_auth.openai_account_auth.OpenAIAccountTokens

```python
class openjiuwen.extensions.external_provider.openai_auth.openai_account_auth.OpenAIAccountTokens(access_token: str, refresh_token: str, id_token: Optional[str] = None, expires_at: Optional[float] = None, token_type: Optional[str] = None, scope: Optional[str] = None, last_refresh: Optional[float] = None)
```

不可变（`frozen`）数据类，表示持久化在凭据存储中的 OAuth 令牌。

**参数**：

- **access_token**(str)：访问令牌。
- **refresh_token**(str)：刷新令牌。
- **id_token**(Optional[str]，可选)：ID 令牌。默认值：`None`。
- **expires_at**(Optional[float]，可选)：访问令牌过期时间戳（秒）。默认值：`None`。
- **token_type**(Optional[str]，可选)：令牌类型。默认值：`None`。
- **scope**(Optional[str]，可选)：授权范围。默认值：`None`。
- **last_refresh**(Optional[float]，可选)：上次刷新时间戳（秒）。默认值：`None`。

### classmethod from_mapping

```python
classmethod from_mapping(payload: dict[str, Any], *, previous: Optional[OpenAIAccountTokens] = None, now: Optional[float] = None) -> OpenAIAccountTokens
```

将令牌端点响应或凭据存储中的原始字典规范化为 `OpenAIAccountTokens`。缺少 `access_token` 或 `refresh_token` 时抛出 `OpenAIAccountAuthError`（`relogin_required=True`）。

### to_mapping

```python
to_mapping() -> dict[str, Any]
```

序列化为可写入凭据存储的字典。

### is_expiring

```python
is_expiring(*, now: float, skew_seconds: int = OPENAI_ACCOUNT_ACCESS_TOKEN_REFRESH_SKEW_SECONDS) -> bool
```

判断访问令牌是否已过期或即将在 `skew_seconds` 秒内过期。

---

## class openjiuwen.extensions.external_provider.openai_auth.openai_account_auth.OpenAIAccountDeviceCode

```python
class openjiuwen.extensions.external_provider.openai_auth.openai_account_auth.OpenAIAccountDeviceCode(user_code: str, device_auth_id: str, verification_uri: str = OPENAI_ACCOUNT_DEVICE_AUTH_URL, interval: int = 5, expires_in: Optional[int] = None)
```

不可变数据类，表示一次设备码登录请求返回的用户验证码信息。

- **user_code**(str)：需要用户在浏览器中输入的验证码。
- **device_auth_id**(str)：设备鉴权会话 ID。
- **verification_uri**(str)：用户完成登录的验证地址。
- **interval**(int)：轮询间隔（秒）。默认值：`5`。
- **expires_in**(Optional[int]，可选)：验证码有效期（秒）。默认值：`None`。

---

## class openjiuwen.extensions.external_provider.openai_auth.openai_account_auth.OpenAIAccountDeviceAuthorization

```python
class openjiuwen.extensions.external_provider.openai_auth.openai_account_auth.OpenAIAccountDeviceAuthorization(authorization_code: str, code_verifier: str)
```

不可变数据类，表示用户完成设备码登录后获得的授权码（PKCE 流程）。

---

## class openjiuwen.extensions.external_provider.openai_auth.openai_account_auth.OpenAIAccountAuthStatus

```python
class openjiuwen.extensions.external_provider.openai_auth.openai_account_auth.OpenAIAccountAuthStatus(authenticated: bool, auth_path: Path, has_refresh_token: bool = False, expires_at: Optional[float] = None, needs_refresh: bool = False, error: Optional[str] = None)
```

不可变数据类，表示当前的鉴权状态，由 `OpenAIAccountAuthManager.status()` 返回。

- **authenticated**(bool)：是否已保存有效凭据。
- **auth_path**(Path)：凭据存储文件路径。
- **has_refresh_token**(bool)：是否存在刷新令牌。默认值：`False`。
- **expires_at**(Optional[float]，可选)：访问令牌过期时间戳。默认值：`None`。
- **needs_refresh**(bool)：访问令牌是否即将过期，需要刷新。默认值：`False`。
- **error**(Optional[str]，可选)：未认证时的错误码。默认值：`None`。

---

## class openjiuwen.extensions.external_provider.openai_auth.openai_account_auth.OpenAIAccountAuthError

```python
class openjiuwen.extensions.external_provider.openai_auth.openai_account_auth.OpenAIAccountAuthError(message: str, *, code: str, relogin_required: bool = False, status_code: Optional[int] = None)
```

OpenAI 账户 OAuth 鉴权相关的类型化异常。

**参数**：

- **message**(str)：错误信息。
- **code**(str)：错误码，例如 `openai_account_auth_missing`、`openai_account_auth_rate_limited`。
- **relogin_required**(bool，可选)：是否需要用户重新登录才能恢复。默认值：`False`。
- **status_code**(Optional[int]，可选)：关联的 HTTP 状态码。默认值：`None`。

---

## class openjiuwen.extensions.external_provider.openai_auth.openai_account_auth.OpenAIAccountAuthManager

```python
class openjiuwen.extensions.external_provider.openai_auth.openai_account_auth.OpenAIAccountAuthManager(*, auth_path: Optional[str | Path] = None, base_url: Optional[str] = None, refresh_skew_seconds: int = OPENAI_ACCOUNT_ACCESS_TOKEN_REFRESH_SKEW_SECONDS, refresh_timeout_seconds: float = 20.0, now: Optional[Callable[[], float]] = None)
```

在 Jiuwen 的私有凭据存储中管理 OpenAI 账户 OAuth 凭据，实现 `ExternalAuthProvider` 协议。

**参数**：

- **auth_path**(Optional[str | Path]，可选)：凭据存储文件路径。默认值：由 `default_openai_account_auth_path()` 决定。
- **base_url**(Optional[str]，可选)：OpenAI 账户后端地址。默认值：环境变量 `OPENJIUWEN_OPENAI_ACCOUNT_BASE_URL` 或 `DEFAULT_OPENAI_ACCOUNT_BASE_URL`。
- **refresh_skew_seconds**(int，可选)：访问令牌过期前提前刷新的秒数。默认值：`120`。
- **refresh_timeout_seconds**(float，可选)：刷新请求的 HTTP 超时时间（秒）。默认值：`20.0`。
- **now**(Optional[Callable[[], float]]，可选)：用于获取当前时间的可调用对象，主要用于测试。默认值：`time.time`。

### status

```python
status() -> OpenAIAccountAuthStatus
```

返回当前鉴权状态；未找到有效凭据时返回 `authenticated=False` 并携带错误码。

### load_tokens

```python
load_tokens() -> OpenAIAccountTokens
```

从凭据存储中加载令牌。未保存凭据时抛出 `OpenAIAccountAuthError`（`code="openai_account_auth_missing"`）。

### save_tokens

```python
save_tokens(tokens: OpenAIAccountTokens) -> None
```

将令牌写入凭据存储。

### logout

```python
logout() -> bool
```

移除已保存的 OpenAI 账户凭据。

**返回**：

**bool**：是否存在凭据并被移除。

### resolve_access_token

```python
resolve_access_token(*, force_refresh: bool = False) -> str
```

返回可用的访问令牌；如果令牌即将过期或 `force_refresh=True`，会先调用 `refresh_tokens` 刷新。

### refresh_tokens

```python
refresh_tokens(*, force: bool = False) -> OpenAIAccountTokens
```

刷新并持久化 OAuth 令牌。未保存凭据时抛出 `OpenAIAccountAuthError`。

### staticmethod start_device_login

```python
staticmethod start_device_login(*, timeout_seconds: float = 15.0, max_attempts: int = 4, sleep: Callable[[float], None] = time.sleep) -> OpenAIAccountDeviceCode
```

发起设备码登录并返回用户需要输入的验证码信息。请求被限流（HTTP 429）时会按指数退避自动重试，最多 `max_attempts` 次。

### poll_device_login

```python
poll_device_login(device_code: OpenAIAccountDeviceCode, *, timeout_seconds: float = 15.0) -> Optional[OpenAIAccountTokens]
```

执行一次设备码登录轮询；登录完成时兑换令牌并写入凭据存储，返回 `OpenAIAccountTokens`；仍在等待用户操作时返回 `None`。

### login_with_device_code

```python
login_with_device_code(*, on_device_code: Optional[Callable[[OpenAIAccountDeviceCode], None]] = None, timeout_seconds: float = 15.0, max_wait_seconds: int = 15 * 60, sleep: Callable[[float], None] = time.sleep, monotonic: Callable[[], float] = time.monotonic) -> OpenAIAccountTokens
```

执行完整的设备码登录流程：发起登录、（可选）回调展示验证码、轮询等待用户完成登录、兑换令牌并保存。

**参数**：

- **on_device_code**(Optional[Callable[[OpenAIAccountDeviceCode], None]]，可选)：在获取到验证码后调用，通常用于向用户展示 `verification_uri` 与 `user_code`。默认值：`None`。
- **timeout_seconds**(float，可选)：单次 HTTP 请求超时时间。默认值：`15.0`。
- **max_wait_seconds**(int，可选)：等待用户完成登录的最长时间（秒）。默认值：`900`（15 分钟）。

**异常**：

- **OpenAIAccountAuthError**：超时（`code="openai_account_device_code_timeout"`）或请求失败时抛出。

**样例**：

```python
from openjiuwen.extensions.external_provider.openai_auth import OpenAIAccountAuthManager

manager = OpenAIAccountAuthManager()


def on_device_code(device_code):
    print(f"打开：{device_code.verification_uri}，输入验证码：{device_code.user_code}")


tokens = manager.login_with_device_code(on_device_code=on_device_code)
print(manager.status())
```

---

## default_openai_account_auth_path

```python
default_openai_account_auth_path() -> Path
```

返回默认的凭据存储路径：优先使用环境变量 `OPENJIUWEN_AUTH_FILE`，否则使用 `${OPENJIUWEN_HOME:-~/.openjiuwen}/auth.json`。

---

## poll_openai_account_device_authorization_once

```python
poll_openai_account_device_authorization_once(device_code: OpenAIAccountDeviceCode, *, timeout_seconds: float = 15.0) -> Optional[OpenAIAccountDeviceAuthorization]
```

执行一次设备码授权轮询。用户尚未完成登录时返回 `None`；被限流时抛出 `OpenAIAccountAuthError`（`code="openai_account_auth_rate_limited"`）。
