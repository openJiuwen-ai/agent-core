# openjiuwen.extensions.external_provider.openai_auth.openai_account_auth

Storage, login, and refresh logic for OpenAI account OAuth credentials, implementing the `ExternalAuthProvider` protocol.

**Constants**:

- **OPENAI_ACCOUNT_PROVIDER** = `"OpenAIAccount"`: Provider name, matching the value used in `ModelClientConfig.client_provider` and the `external_provider` registry.
- **DEFAULT_OPENAI_ACCOUNT_BASE_URL** = `"https://chatgpt.com/backend-api/codex"`: Default OpenAI account backend address, overridable via the `OPENJIUWEN_OPENAI_ACCOUNT_BASE_URL` environment variable.

---

## class openjiuwen.extensions.external_provider.openai_auth.openai_account_auth.OpenAIAccountTokens

```python
class openjiuwen.extensions.external_provider.openai_auth.openai_account_auth.OpenAIAccountTokens(access_token: str, refresh_token: str, id_token: Optional[str] = None, expires_at: Optional[float] = None, token_type: Optional[str] = None, scope: Optional[str] = None, last_refresh: Optional[float] = None)
```

Immutable (`frozen`) data class representing the OAuth tokens persisted in the credential store.

**Parameters**:

- **access_token** (str): Access token.
- **refresh_token** (str): Refresh token.
- **id_token** (Optional[str], optional): ID token. Default value: `None`.
- **expires_at** (Optional[float], optional): Access token expiry timestamp (seconds). Default value: `None`.
- **token_type** (Optional[str], optional): Token type. Default value: `None`.
- **scope** (Optional[str], optional): Authorization scope. Default value: `None`.
- **last_refresh** (Optional[float], optional): Last refresh timestamp (seconds). Default value: `None`.

### classmethod from_mapping

```python
classmethod from_mapping(payload: dict[str, Any], *, previous: Optional[OpenAIAccountTokens] = None, now: Optional[float] = None) -> OpenAIAccountTokens
```

Normalize a raw dict from the token endpoint or the credential store into an `OpenAIAccountTokens`. Raises `OpenAIAccountAuthError` (`relogin_required=True`) when `access_token` or `refresh_token` is missing.

### to_mapping

```python
to_mapping() -> dict[str, Any]
```

Serialize to a dict suitable for writing to the credential store.

### is_expiring

```python
is_expiring(*, now: float, skew_seconds: int = OPENAI_ACCOUNT_ACCESS_TOKEN_REFRESH_SKEW_SECONDS) -> bool
```

Determine whether the access token has expired or will expire within `skew_seconds`.

---

## class openjiuwen.extensions.external_provider.openai_auth.openai_account_auth.OpenAIAccountDeviceCode

```python
class openjiuwen.extensions.external_provider.openai_auth.openai_account_auth.OpenAIAccountDeviceCode(user_code: str, device_auth_id: str, verification_uri: str = OPENAI_ACCOUNT_DEVICE_AUTH_URL, interval: int = 5, expires_in: Optional[int] = None)
```

Immutable data class representing the user-facing verification code returned by a device-code login request.

- **user_code** (str): Code the user must enter in the browser.
- **device_auth_id** (str): Device auth session ID.
- **verification_uri** (str): URL where the user completes sign-in.
- **interval** (int): Polling interval in seconds. Default value: `5`.
- **expires_in** (Optional[int], optional): Verification code validity period (seconds). Default value: `None`.

---

## class openjiuwen.extensions.external_provider.openai_auth.openai_account_auth.OpenAIAccountDeviceAuthorization

```python
class openjiuwen.extensions.external_provider.openai_auth.openai_account_auth.OpenAIAccountDeviceAuthorization(authorization_code: str, code_verifier: str)
```

Immutable data class representing the authorization code obtained once the user completes device-code login (PKCE flow).

---

## class openjiuwen.extensions.external_provider.openai_auth.openai_account_auth.OpenAIAccountAuthStatus

```python
class openjiuwen.extensions.external_provider.openai_auth.openai_account_auth.OpenAIAccountAuthStatus(authenticated: bool, auth_path: Path, has_refresh_token: bool = False, expires_at: Optional[float] = None, needs_refresh: bool = False, error: Optional[str] = None)
```

Immutable data class representing the current auth status, returned by `OpenAIAccountAuthManager.status()`.

- **authenticated** (bool): Whether valid credentials are stored.
- **auth_path** (Path): Path to the credential store file.
- **has_refresh_token** (bool): Whether a refresh token is present. Default value: `False`.
- **expires_at** (Optional[float], optional): Access token expiry timestamp. Default value: `None`.
- **needs_refresh** (bool): Whether the access token is close to expiring and needs a refresh. Default value: `False`.
- **error** (Optional[str], optional): Error code when not authenticated. Default value: `None`.

---

## class openjiuwen.extensions.external_provider.openai_auth.openai_account_auth.OpenAIAccountAuthError

```python
class openjiuwen.extensions.external_provider.openai_auth.openai_account_auth.OpenAIAccountAuthError(message: str, *, code: str, relogin_required: bool = False, status_code: Optional[int] = None)
```

Typed exception for OpenAI account OAuth auth failures.

**Parameters**:

- **message** (str): Error message.
- **code** (str): Error code, e.g. `openai_account_auth_missing`, `openai_account_auth_rate_limited`.
- **relogin_required** (bool, optional): Whether the user must sign in again to recover. Default value: `False`.
- **status_code** (Optional[int], optional): Associated HTTP status code. Default value: `None`.

---

## class openjiuwen.extensions.external_provider.openai_auth.openai_account_auth.OpenAIAccountAuthManager

```python
class openjiuwen.extensions.external_provider.openai_auth.openai_account_auth.OpenAIAccountAuthManager(*, auth_path: Optional[str | Path] = None, base_url: Optional[str] = None, refresh_skew_seconds: int = OPENAI_ACCOUNT_ACCESS_TOKEN_REFRESH_SKEW_SECONDS, refresh_timeout_seconds: float = 20.0, now: Optional[Callable[[], float]] = None)
```

Manages OpenAI account OAuth credentials in Jiuwen's private credential store, implementing the `ExternalAuthProvider` protocol.

**Parameters**:

- **auth_path** (Optional[str | Path], optional): Path to the credential store file. Default value: determined by `default_openai_account_auth_path()`.
- **base_url** (Optional[str], optional): OpenAI account backend address. Default value: the `OPENJIUWEN_OPENAI_ACCOUNT_BASE_URL` environment variable or `DEFAULT_OPENAI_ACCOUNT_BASE_URL`.
- **refresh_skew_seconds** (int, optional): Seconds before expiry to proactively refresh the access token. Default value: `120`.
- **refresh_timeout_seconds** (float, optional): HTTP timeout for refresh requests (seconds). Default value: `20.0`.
- **now** (Optional[Callable[[], float]], optional): Callable returning the current time, mainly for testing. Default value: `time.time`.

### status

```python
status() -> OpenAIAccountAuthStatus
```

Return the current auth status; returns `authenticated=False` with an error code when no valid credentials are found.

### load_tokens

```python
load_tokens() -> OpenAIAccountTokens
```

Load tokens from the credential store. Raises `OpenAIAccountAuthError` (`code="openai_account_auth_missing"`) when no credentials are stored.

### save_tokens

```python
save_tokens(tokens: OpenAIAccountTokens) -> None
```

Write tokens to the credential store.

### logout

```python
logout() -> bool
```

Remove stored OpenAI account credentials.

**Returns**:

**bool**: Whether credentials existed and were removed.

### resolve_access_token

```python
resolve_access_token(*, force_refresh: bool = False) -> str
```

Return a usable access token; calls `refresh_tokens` first if the token is close to expiring or `force_refresh=True`.

### refresh_tokens

```python
refresh_tokens(*, force: bool = False) -> OpenAIAccountTokens
```

Refresh and persist OAuth tokens. Raises `OpenAIAccountAuthError` when no credentials are stored.

### staticmethod start_device_login

```python
staticmethod start_device_login(*, timeout_seconds: float = 15.0, max_attempts: int = 4, sleep: Callable[[float], None] = time.sleep) -> OpenAIAccountDeviceCode
```

Start device-code login and return the user-facing verification code details. Requests rate-limited with HTTP 429 are retried automatically with exponential backoff, up to `max_attempts` times.

### poll_device_login

```python
poll_device_login(device_code: OpenAIAccountDeviceCode, *, timeout_seconds: float = 15.0) -> Optional[OpenAIAccountTokens]
```

Run a single device-login poll; when login completes, exchanges tokens and saves them, returning `OpenAIAccountTokens`. Returns `None` while still waiting for the user.

### login_with_device_code

```python
login_with_device_code(*, on_device_code: Optional[Callable[[OpenAIAccountDeviceCode], None]] = None, timeout_seconds: float = 15.0, max_wait_seconds: int = 15 * 60, sleep: Callable[[float], None] = time.sleep, monotonic: Callable[[], float] = time.monotonic) -> OpenAIAccountTokens
```

Run the full device-code login flow: start login, optionally invoke a callback to show the verification code, poll until the user completes sign-in, exchange tokens, and save them.

**Parameters**:

- **on_device_code** (Optional[Callable[[OpenAIAccountDeviceCode], None]], optional): Called once the verification code is available, typically to display `verification_uri` and `user_code` to the user. Default value: `None`.
- **timeout_seconds** (float, optional): Timeout for a single HTTP request. Default value: `15.0`.
- **max_wait_seconds** (int, optional): Maximum time to wait for the user to complete sign-in (seconds). Default value: `900` (15 minutes).

**Raises**:

- **OpenAIAccountAuthError**: Raised on timeout (`code="openai_account_device_code_timeout"`) or request failure.

**Example**:

```python
from openjiuwen.extensions.external_provider.openai_auth import OpenAIAccountAuthManager

manager = OpenAIAccountAuthManager()


def on_device_code(device_code):
    print(f"Open: {device_code.verification_uri}, enter code: {device_code.user_code}")


tokens = manager.login_with_device_code(on_device_code=on_device_code)
print(manager.status())
```

---

## default_openai_account_auth_path

```python
default_openai_account_auth_path() -> Path
```

Return the default credential store path: prefers the `OPENJIUWEN_AUTH_FILE` environment variable, otherwise `${OPENJIUWEN_HOME:-~/.openjiuwen}/auth.json`.

---

## poll_openai_account_device_authorization_once

```python
poll_openai_account_device_authorization_once(device_code: OpenAIAccountDeviceCode, *, timeout_seconds: float = 15.0) -> Optional[OpenAIAccountDeviceAuthorization]
```

Run a single device authorization poll. Returns `None` while the user has not completed sign-in yet; raises `OpenAIAccountAuthError` (`code="openai_account_auth_rate_limited"`) when rate-limited.
