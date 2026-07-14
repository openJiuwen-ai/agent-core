# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""OAuth token storage helpers for the OpenAI account provider."""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
from filelock import FileLock


OPENAI_ACCOUNT_PROVIDER = "OpenAIAccount"
DEFAULT_OPENAI_ACCOUNT_BASE_URL = "https://chatgpt.com/backend-api/codex"
OPENAI_ACCOUNT_AUTH_ISSUER = "https://auth.openai.com"
OPENAI_ACCOUNT_DEVICE_AUTH_URL = f"{OPENAI_ACCOUNT_AUTH_ISSUER}/codex/device"
OPENAI_ACCOUNT_DEVICE_USER_CODE_URL = f"{OPENAI_ACCOUNT_AUTH_ISSUER}/api/accounts/deviceauth/usercode"
OPENAI_ACCOUNT_DEVICE_TOKEN_URL = f"{OPENAI_ACCOUNT_AUTH_ISSUER}/api/accounts/deviceauth/token"
OPENAI_ACCOUNT_DEVICE_CALLBACK_URL = f"{OPENAI_ACCOUNT_AUTH_ISSUER}/deviceauth/callback"
OPENAI_ACCOUNT_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OPENAI_ACCOUNT_OAUTH_TOKEN_URL = f"{OPENAI_ACCOUNT_AUTH_ISSUER}/oauth/token"
OPENAI_ACCOUNT_ACCESS_TOKEN_REFRESH_SKEW_SECONDS = 120
OPENAI_ACCOUNT_RATE_LIMITED_CODE = "openai_account_auth_rate_limited"


@dataclass(frozen=True, slots=True)
class OpenAIAccountTokens:
    """OpenAI account OAuth tokens persisted in Jiuwen's auth store."""

    access_token: str
    refresh_token: str
    id_token: Optional[str] = None
    expires_at: Optional[float] = None
    token_type: Optional[str] = None
    scope: Optional[str] = None
    last_refresh: Optional[float] = None

    @classmethod
    def from_mapping(
            cls,
            payload: dict[str, Any],
            *,
            previous: Optional["OpenAIAccountTokens"] = None,
            now: Optional[float] = None,
    ) -> "OpenAIAccountTokens":
        """Normalize token endpoint or auth-store payloads."""
        now = time.time() if now is None else now
        access_token = str(payload.get("access_token") or (previous.access_token if previous else "")).strip()
        refresh_token = str(payload.get("refresh_token") or (previous.refresh_token if previous else "")).strip()
        if not access_token:
            raise OpenAIAccountAuthError(
                "OpenAI account OAuth payload is missing access_token.",
                code="openai_account_auth_missing_access_token",
                relogin_required=True,
            )
        if not refresh_token:
            raise OpenAIAccountAuthError(
                "OpenAI account OAuth payload is missing refresh_token.",
                code="openai_account_auth_missing_refresh_token",
                relogin_required=True,
            )

        expires_at = _coerce_timestamp(payload.get("expires_at"))
        expires_in = _coerce_float(payload.get("expires_in"))
        if expires_at is None and expires_in is not None:
            expires_at = now + max(expires_in, 0)
        if expires_at is None and previous is not None:
            expires_at = previous.expires_at
        if expires_at is None:
            expires_at = _jwt_exp(access_token)

        return cls(
            access_token=access_token,
            refresh_token=refresh_token,
            id_token=_optional_str(payload.get("id_token") or (previous.id_token if previous else None)),
            expires_at=expires_at,
            token_type=_optional_str(payload.get("token_type") or (previous.token_type if previous else None)),
            scope=_optional_str(payload.get("scope") or (previous.scope if previous else None)),
            last_refresh=_coerce_timestamp(payload.get("last_refresh")) or now,
        )

    def to_mapping(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
        }
        if self.id_token:
            data["id_token"] = self.id_token
        if self.expires_at is not None:
            data["expires_at"] = self.expires_at
        if self.token_type:
            data["token_type"] = self.token_type
        if self.scope:
            data["scope"] = self.scope
        if self.last_refresh is not None:
            data["last_refresh"] = self.last_refresh
        return data

    def is_expiring(self, *, now: float, skew_seconds: int = OPENAI_ACCOUNT_ACCESS_TOKEN_REFRESH_SKEW_SECONDS) -> bool:
        expires_at = self.expires_at if self.expires_at is not None else _jwt_exp(self.access_token)
        if expires_at is None:
            return False
        return expires_at <= now + skew_seconds


@dataclass(frozen=True, slots=True)
class OpenAIAccountDeviceCode:
    user_code: str
    device_auth_id: str
    verification_uri: str = OPENAI_ACCOUNT_DEVICE_AUTH_URL
    interval: int = 5
    expires_in: Optional[int] = None


@dataclass(frozen=True, slots=True)
class OpenAIAccountDeviceAuthorization:
    authorization_code: str
    code_verifier: str


@dataclass(frozen=True, slots=True)
class OpenAIAccountAuthStatus:
    authenticated: bool
    auth_path: Path
    has_refresh_token: bool = False
    expires_at: Optional[float] = None
    needs_refresh: bool = False
    error: Optional[str] = None


class OpenAIAccountAuthError(Exception):
    """Typed auth error used by the OpenAI account OAuth manager."""

    def __init__(
            self,
            message: str,
            *,
            code: str,
            relogin_required: bool = False,
            status_code: Optional[int] = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.relogin_required = relogin_required
        self.status_code = status_code

    def __str__(self) -> str:
        return self.message


class OpenAIAccountAuthManager:
    """Manage OpenAI account OAuth credentials in Jiuwen's private auth store."""

    def __init__(
            self,
            *,
            auth_path: Optional[str | Path] = None,
            base_url: Optional[str] = None,
            refresh_skew_seconds: int = OPENAI_ACCOUNT_ACCESS_TOKEN_REFRESH_SKEW_SECONDS,
            refresh_timeout_seconds: float = 20.0,
            now: Optional[Callable[[], float]] = None,
    ):
        self.auth_path = Path(auth_path).expanduser() if auth_path else default_openai_account_auth_path()
        self.base_url = (
            base_url
            or os.getenv("OPENJIUWEN_OPENAI_ACCOUNT_BASE_URL")
            or DEFAULT_OPENAI_ACCOUNT_BASE_URL
        ).rstrip("/")
        self.refresh_skew_seconds = refresh_skew_seconds
        self.refresh_timeout_seconds = refresh_timeout_seconds
        self._now = now or time.time

    def status(self) -> OpenAIAccountAuthStatus:
        try:
            tokens = self.load_tokens()
        except OpenAIAccountAuthError as exc:
            return OpenAIAccountAuthStatus(
                authenticated=False,
                auth_path=self.auth_path,
                error=exc.code,
            )
        now = self._now()
        return OpenAIAccountAuthStatus(
            authenticated=True,
            auth_path=self.auth_path,
            has_refresh_token=bool(tokens.refresh_token),
            expires_at=tokens.expires_at,
            needs_refresh=tokens.is_expiring(now=now, skew_seconds=self.refresh_skew_seconds),
        )

    def load_tokens(self) -> OpenAIAccountTokens:
        with self._file_lock():
            store = self._load_store_unlocked()
            state = _provider_state(store)
            tokens = state.get("tokens") if isinstance(state, dict) else None
            if not isinstance(tokens, dict):
                raise OpenAIAccountAuthError(
                    "No OpenAI account credentials stored.",
                    code="openai_account_auth_missing",
                    relogin_required=True,
                )
            return OpenAIAccountTokens.from_mapping(tokens, now=self._now())

    def save_tokens(self, tokens: OpenAIAccountTokens) -> None:
        with self._file_lock():
            store = self._load_store_unlocked()
            providers = store.setdefault("providers", {})
            providers[OPENAI_ACCOUNT_PROVIDER] = {
                "tokens": tokens.to_mapping(),
                "auth_mode": "openai_account",
                "base_url": self.base_url,
                "last_refresh": tokens.last_refresh or self._now(),
            }
            self._save_store_unlocked(store)

    def logout(self) -> bool:
        with self._file_lock():
            store = self._load_store_unlocked()
            providers = store.get("providers")
            if not isinstance(providers, dict) or OPENAI_ACCOUNT_PROVIDER not in providers:
                return False
            providers.pop(OPENAI_ACCOUNT_PROVIDER, None)
            self._save_store_unlocked(store)
            return True

    def resolve_access_token(self, *, force_refresh: bool = False) -> str:
        tokens = self.load_tokens()
        if not force_refresh and not tokens.is_expiring(now=self._now(), skew_seconds=self.refresh_skew_seconds):
            return tokens.access_token
        return self.refresh_tokens(force=force_refresh).access_token

    def refresh_tokens(self, *, force: bool = False) -> OpenAIAccountTokens:
        with self._file_lock():
            store = self._load_store_unlocked()
            state = _provider_state(store)
            tokens_payload = state.get("tokens") if isinstance(state, dict) else None
            if not isinstance(tokens_payload, dict):
                raise OpenAIAccountAuthError(
                    "No OpenAI account credentials stored.",
                    code="openai_account_auth_missing",
                    relogin_required=True,
                )
            current = OpenAIAccountTokens.from_mapping(tokens_payload, now=self._now())
            if not force and not current.is_expiring(now=self._now(), skew_seconds=self.refresh_skew_seconds):
                return current
            refreshed = refresh_openai_account_oauth(
                current.access_token,
                current.refresh_token,
                timeout_seconds=self.refresh_timeout_seconds,
                now=self._now,
            )
            providers = store.setdefault("providers", {})
            providers[OPENAI_ACCOUNT_PROVIDER] = {
                "tokens": refreshed.to_mapping(),
                "auth_mode": "openai_account",
                "base_url": self.base_url,
                "last_refresh": refreshed.last_refresh or self._now(),
            }
            self._save_store_unlocked(store)
            return refreshed

    @staticmethod
    def start_device_login(
            *,
            timeout_seconds: float = 15.0,
            max_attempts: int = 4,
            sleep: Callable[[float], None] = time.sleep,
    ) -> OpenAIAccountDeviceCode:
        """Start device login and return the user-facing code details."""
        return request_openai_account_device_code(
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            sleep=sleep,
        )

    def poll_device_login(
            self,
            device_code: OpenAIAccountDeviceCode,
            *,
            timeout_seconds: float = 15.0,
    ) -> Optional[OpenAIAccountTokens]:
        """Run one device-login poll and save tokens when login is complete."""
        authorization = poll_openai_account_device_authorization_once(
            device_code,
            timeout_seconds=timeout_seconds,
        )
        if authorization is None:
            return None
        tokens = exchange_openai_account_device_authorization(
            authorization,
            timeout_seconds=timeout_seconds,
            now=self._now,
        )
        self.save_tokens(tokens)
        return tokens

    def login_with_device_code(
            self,
            *,
            on_device_code: Optional[Callable[[OpenAIAccountDeviceCode], None]] = None,
            timeout_seconds: float = 15.0,
            max_wait_seconds: int = 15 * 60,
            sleep: Callable[[float], None] = time.sleep,
            monotonic: Callable[[], float] = time.monotonic,
    ) -> OpenAIAccountTokens:
        tokens = login_openai_account_oauth(
            on_device_code=on_device_code,
            timeout_seconds=timeout_seconds,
            max_wait_seconds=max_wait_seconds,
            sleep=sleep,
            monotonic=monotonic,
            now=self._now,
        )
        self.save_tokens(tokens)
        return tokens

    def _file_lock(self) -> FileLock:
        self.auth_path.parent.mkdir(parents=True, exist_ok=True)
        return FileLock(str(self.auth_path) + ".lock")

    def _load_store_unlocked(self) -> dict[str, Any]:
        if not self.auth_path.exists():
            return {}
        try:
            payload = json.loads(self.auth_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise OpenAIAccountAuthError(
                f"Failed to read OpenAI account auth store: {exc}",
                code="openai_account_auth_store_read_failed",
            ) from exc
        except json.JSONDecodeError as exc:
            raise OpenAIAccountAuthError(
                "OpenAI account auth store is not valid JSON.",
                code="openai_account_auth_invalid_store",
                relogin_required=True,
            ) from exc
        if not isinstance(payload, dict):
            raise OpenAIAccountAuthError(
                "OpenAI account auth store must be a JSON object.",
                code="openai_account_auth_invalid_store",
                relogin_required=True,
            )
        return payload

    def _save_store_unlocked(self, store: dict[str, Any]) -> None:
        self.auth_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.auth_path.with_name(f"{self.auth_path.name}.tmp")
        tmp_path.write_text(json.dumps(store, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, self.auth_path)
        os.chmod(self.auth_path, 0o600)


def default_openai_account_auth_path() -> Path:
    """Return the default auth store path used by the OpenAI account provider."""
    auth_file = os.getenv("OPENJIUWEN_AUTH_FILE")
    if auth_file:
        return Path(auth_file).expanduser()
    home = (
        Path(os.getenv("OPENJIUWEN_HOME", "")).expanduser()
        if os.getenv("OPENJIUWEN_HOME")
        else Path.home() / ".openjiuwen"
    )
    return home / "auth.json"


def login_openai_account_oauth(
        *,
        on_device_code: Optional[Callable[[OpenAIAccountDeviceCode], None]] = None,
        timeout_seconds: float = 15.0,
        max_wait_seconds: int = 15 * 60,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        now: Optional[Callable[[], float]] = None,
) -> OpenAIAccountTokens:
    """Run the OpenAI account device login flow and return OAuth tokens."""
    device_code = request_openai_account_device_code(timeout_seconds=timeout_seconds)
    if on_device_code:
        on_device_code(device_code)
    authorization = poll_openai_account_device_authorization(
        device_code,
        timeout_seconds=timeout_seconds,
        max_wait_seconds=max_wait_seconds,
        sleep=sleep,
        monotonic=monotonic,
    )
    return exchange_openai_account_device_authorization(
        authorization,
        timeout_seconds=timeout_seconds,
        now=now,
    )


def request_openai_account_device_code(
        *,
        timeout_seconds: float = 15.0,
        max_attempts: int = 4,
        sleep: Callable[[float], None] = time.sleep,
) -> OpenAIAccountDeviceCode:
    """Request a user code for OpenAI account device login."""
    response = None
    attempts = max(1, max_attempts)
    for attempt in range(1, attempts + 1):
        try:
            with httpx.Client(timeout=httpx.Timeout(max(5.0, float(timeout_seconds)))) as client:
                response = client.post(
                    OPENAI_ACCOUNT_DEVICE_USER_CODE_URL,
                    json={"client_id": OPENAI_ACCOUNT_OAUTH_CLIENT_ID},
                    headers={"Content-Type": "application/json"},
                )
        except httpx.HTTPError as exc:
            _raise_network_failed("device code request", "openai_account_device_code_network_error", exc)
        if response.status_code != 429:
            break
        if attempt < attempts:
            retry_after = _parse_retry_after_seconds(getattr(response, "headers", None))
            delay = retry_after if retry_after is not None else 2 ** attempt
            sleep(max(1, min(int(delay), 60)))

    if response is not None and response.status_code == 429:
        _raise_rate_limited(response, operation="device code request")
    if response is None or response.status_code != 200:
        status_code = response.status_code if response is not None else None
        raise OpenAIAccountAuthError(
            f"OpenAI account device code request failed with status {status_code or 'unknown'}.",
            code="openai_account_device_code_request_failed",
            status_code=status_code,
        )

    try:
        payload = response.json()
    except Exception as exc:
        raise OpenAIAccountAuthError(
            "OpenAI account device code response returned invalid JSON.",
            code="openai_account_device_code_invalid_json",
            relogin_required=True,
            status_code=response.status_code,
        ) from exc
    if not isinstance(payload, dict):
        raise OpenAIAccountAuthError(
            "OpenAI account device code response must be a JSON object.",
            code="openai_account_device_code_invalid_json",
            relogin_required=True,
            status_code=response.status_code,
        )

    user_code = _optional_str(payload.get("user_code"))
    device_auth_id = _optional_str(payload.get("device_auth_id"))
    if not user_code or not device_auth_id:
        raise OpenAIAccountAuthError(
            "OpenAI account device code response is missing required fields.",
            code="openai_account_device_code_incomplete",
            relogin_required=True,
            status_code=response.status_code,
        )
    interval = int(_coerce_float(payload.get("interval")) or 5)
    expires_in = _coerce_float(payload.get("expires_in"))
    return OpenAIAccountDeviceCode(
        user_code=user_code,
        device_auth_id=device_auth_id,
        interval=max(3, interval),
        expires_in=int(expires_in) if expires_in is not None else None,
    )


def poll_openai_account_device_authorization_once(
        device_code: OpenAIAccountDeviceCode,
        *,
        timeout_seconds: float = 15.0,
) -> Optional[OpenAIAccountDeviceAuthorization]:
    """Poll once for a completed device login, returning None while pending."""
    timeout = httpx.Timeout(max(5.0, float(timeout_seconds)))
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(
                OPENAI_ACCOUNT_DEVICE_TOKEN_URL,
                json={
                    "device_auth_id": device_code.device_auth_id,
                    "user_code": device_code.user_code,
                },
                headers={"Content-Type": "application/json"},
            )
    except httpx.HTTPError as exc:
        _raise_network_failed("device auth polling", "openai_account_device_code_poll_network_error", exc)
    if response.status_code in {403, 404}:
        return None
    if response.status_code == 429:
        _raise_rate_limited(response, operation="device auth polling")
    if response.status_code != 200:
        raise OpenAIAccountAuthError(
            f"OpenAI account device auth polling failed with status {response.status_code}.",
            code="openai_account_device_code_poll_failed",
            status_code=response.status_code,
        )
    return _authorization_from_response(response)


def poll_openai_account_device_authorization(
        device_code: OpenAIAccountDeviceCode,
        *,
        timeout_seconds: float = 15.0,
        max_wait_seconds: int = 15 * 60,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
) -> OpenAIAccountDeviceAuthorization:
    """Poll until the user completes device login and an authorization code is available."""
    start = monotonic()
    while monotonic() - start < max_wait_seconds:
        sleep(device_code.interval)
        authorization = poll_openai_account_device_authorization_once(
            device_code,
            timeout_seconds=timeout_seconds,
        )
        if authorization is None:
            continue
        return authorization

    raise OpenAIAccountAuthError(
        "OpenAI account device login timed out.",
        code="openai_account_device_code_timeout",
        relogin_required=True,
    )


def exchange_openai_account_device_authorization(
        authorization: OpenAIAccountDeviceAuthorization,
        *,
        timeout_seconds: float = 15.0,
        now: Optional[Callable[[], float]] = None,
) -> OpenAIAccountTokens:
    """Exchange device authorization code for OpenAI account OAuth tokens."""
    try:
        with httpx.Client(timeout=httpx.Timeout(max(5.0, float(timeout_seconds)))) as client:
            response = client.post(
                OPENAI_ACCOUNT_OAUTH_TOKEN_URL,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "grant_type": "authorization_code",
                    "code": authorization.authorization_code,
                    "redirect_uri": OPENAI_ACCOUNT_DEVICE_CALLBACK_URL,
                    "client_id": OPENAI_ACCOUNT_OAUTH_CLIENT_ID,
                    "code_verifier": authorization.code_verifier,
                },
            )
    except httpx.HTTPError as exc:
        _raise_network_failed("token exchange", "openai_account_token_exchange_network_error", exc)

    if response.status_code == 429:
        _raise_rate_limited(response, operation="token exchange")
    if response.status_code != 200:
        _raise_token_exchange_failed(response)

    try:
        payload = response.json()
    except Exception as exc:
        raise OpenAIAccountAuthError(
            "OpenAI account token exchange returned invalid JSON.",
            code="openai_account_token_exchange_invalid_json",
            relogin_required=True,
            status_code=response.status_code,
        ) from exc
    if not isinstance(payload, dict):
        raise OpenAIAccountAuthError(
            "OpenAI account token exchange response must be a JSON object.",
            code="openai_account_token_exchange_invalid_json",
            relogin_required=True,
            status_code=response.status_code,
        )
    return OpenAIAccountTokens.from_mapping(payload, now=_call_now(now))


def refresh_openai_account_oauth(
        access_token: str,
        refresh_token: str,
        *,
        timeout_seconds: float = 20.0,
        now: Optional[Callable[[], float]] = None,
) -> OpenAIAccountTokens:
    """Refresh OpenAI account OAuth tokens without reading or writing auth state."""
    del access_token
    refresh_token = str(refresh_token or "").strip()
    if not refresh_token:
        raise OpenAIAccountAuthError(
            "OpenAI account auth is missing refresh_token.",
            code="openai_account_auth_missing_refresh_token",
            relogin_required=True,
        )

    timeout = httpx.Timeout(max(5.0, float(timeout_seconds)))
    try:
        with httpx.Client(timeout=timeout, headers={"Accept": "application/json"}) as client:
            response = client.post(
                OPENAI_ACCOUNT_OAUTH_TOKEN_URL,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": OPENAI_ACCOUNT_OAUTH_CLIENT_ID,
                },
            )
    except httpx.HTTPError as exc:
        _raise_network_failed("token refresh", "openai_account_auth_refresh_network_error", exc)

    if response.status_code == 429:
        _raise_rate_limited(response, operation="token refresh")
    if response.status_code != 200:
        _raise_refresh_failed(response)

    try:
        payload = response.json()
    except Exception as exc:
        raise OpenAIAccountAuthError(
            "OpenAI account token refresh returned invalid JSON.",
            code="openai_account_auth_refresh_invalid_json",
            relogin_required=True,
            status_code=response.status_code,
        ) from exc
    if not isinstance(payload, dict):
        raise OpenAIAccountAuthError(
            "OpenAI account token refresh response must be a JSON object.",
            code="openai_account_auth_refresh_invalid_json",
            relogin_required=True,
            status_code=response.status_code,
        )

    refreshed_access = payload.get("access_token")
    if not isinstance(refreshed_access, str) or not refreshed_access.strip():
        raise OpenAIAccountAuthError(
            "OpenAI account token refresh response was missing access_token.",
            code="openai_account_auth_refresh_missing_access_token",
            relogin_required=True,
            status_code=response.status_code,
        )

    refreshed_payload = dict(payload)
    refreshed_payload["access_token"] = refreshed_access.strip()
    next_refresh = payload.get("refresh_token")
    refreshed_payload["refresh_token"] = (
        next_refresh.strip()
        if isinstance(next_refresh, str) and next_refresh.strip()
        else refresh_token
    )
    refreshed_at = _call_now(now)
    refreshed_payload["last_refresh"] = refreshed_at
    return OpenAIAccountTokens.from_mapping(refreshed_payload, now=refreshed_at)


def _provider_state(store: dict[str, Any]) -> dict[str, Any]:
    providers = store.get("providers")
    if not isinstance(providers, dict):
        return {}
    state = providers.get(OPENAI_ACCOUNT_PROVIDER)
    return state if isinstance(state, dict) else {}


def _authorization_from_response(response: httpx.Response) -> OpenAIAccountDeviceAuthorization:
    try:
        payload = response.json()
    except Exception as exc:
        raise OpenAIAccountAuthError(
            "OpenAI account device auth polling returned invalid JSON.",
            code="openai_account_device_code_poll_invalid_json",
            relogin_required=True,
            status_code=response.status_code,
        ) from exc
    if not isinstance(payload, dict):
        raise OpenAIAccountAuthError(
            "OpenAI account device auth polling response must be a JSON object.",
            code="openai_account_device_code_poll_invalid_json",
            relogin_required=True,
            status_code=response.status_code,
        )
    authorization_code = _optional_str(payload.get("authorization_code"))
    code_verifier = _optional_str(payload.get("code_verifier"))
    if not authorization_code or not code_verifier:
        raise OpenAIAccountAuthError(
            "OpenAI account device auth response is missing authorization data.",
            code="openai_account_device_code_incomplete_exchange",
            relogin_required=True,
            status_code=response.status_code,
        )
    return OpenAIAccountDeviceAuthorization(
        authorization_code=authorization_code,
        code_verifier=code_verifier,
    )


def _raise_network_failed(operation: str, code: str, exc: httpx.HTTPError) -> None:
    raise OpenAIAccountAuthError(
        f"OpenAI account {operation} failed: {exc}",
        code=code,
        relogin_required=False,
    ) from exc


def _raise_rate_limited(response: httpx.Response, *, operation: str = "OAuth request") -> None:
    retry_after = _parse_retry_after_seconds(getattr(response, "headers", None))
    if retry_after is not None:
        message = (
            f"OpenAI account {operation} is rate limited; retry after {retry_after}s. "
            "Credentials are still valid."
        )
    else:
        message = f"OpenAI account {operation} is rate limited. Credentials are still valid."
    raise OpenAIAccountAuthError(
        message,
        code=OPENAI_ACCOUNT_RATE_LIMITED_CODE,
        relogin_required=False,
        status_code=response.status_code,
    )


def _raise_refresh_failed(response: httpx.Response) -> None:
    _raise_oauth_failed(
        response,
        default_code="openai_account_auth_refresh_failed",
        operation="token refresh",
    )


def _raise_token_exchange_failed(response: httpx.Response) -> None:
    _raise_oauth_failed(
        response,
        default_code="openai_account_token_exchange_failed",
        operation="token exchange",
    )


def _raise_oauth_failed(response: httpx.Response, *, default_code: str, operation: str) -> None:
    code = default_code
    message = f"OpenAI account {operation} failed with status {response.status_code}."
    relogin_required = False
    try:
        payload = response.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            parsed_code = error.get("code") or error.get("type")
            parsed_message = error.get("message")
            if isinstance(parsed_code, str) and parsed_code.strip():
                code = parsed_code.strip()
            if isinstance(parsed_message, str) and parsed_message.strip():
                message = f"OpenAI account {operation} failed: {parsed_message.strip()}"
        elif isinstance(error, str) and error.strip():
            code = error.strip()
            parsed_message = payload.get("error_description") or payload.get("message")
            if isinstance(parsed_message, str) and parsed_message.strip():
                message = f"OpenAI account {operation} failed: {parsed_message.strip()}"

    if code in {"invalid_grant", "invalid_token", "invalid_request", "refresh_token_reused"}:
        relogin_required = True
    if response.status_code in {401, 403}:
        relogin_required = True

    raise OpenAIAccountAuthError(
        message,
        code=code,
        relogin_required=relogin_required,
        status_code=response.status_code,
    )


def _parse_retry_after_seconds(headers: Any) -> Optional[int]:
    if headers is None:
        return None
    try:
        value = headers.get("retry-after") or headers.get("Retry-After")
    except AttributeError:
        return None
    if value is None:
        return None
    try:
        seconds = int(str(value).strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def _call_now(now: Optional[Callable[[], float]]) -> float:
    return time.time() if now is None else now()


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _coerce_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_timestamp(value: Any) -> Optional[float]:
    timestamp = _coerce_float(value)
    if timestamp is None:
        return None
    return timestamp if timestamp > 0 else None


def _jwt_exp(token: str) -> Optional[float]:
    parts = token.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("utf-8"))
        data = json.loads(decoded.decode("utf-8"))
    except ValueError:
        return None
    exp = data.get("exp") if isinstance(data, dict) else None
    return _coerce_timestamp(exp)
