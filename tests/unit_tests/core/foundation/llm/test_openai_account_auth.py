# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for OpenAI account OAuth token storage.

Scope: local auth-store serialization, status, logout, and valid access-token
resolution, including refresh. Device login is added in a later layer.
"""

import base64
import json
import os
import stat

import pytest
import httpx

from openjiuwen.extensions.external_provider.openai_auth import openai_account_auth
from openjiuwen.extensions.external_provider.openai_auth.openai_account_auth import (
    OPENAI_ACCOUNT_PROVIDER,
    OPENAI_ACCOUNT_OAUTH_CLIENT_ID,
    OPENAI_ACCOUNT_OAUTH_TOKEN_URL,
    OPENAI_ACCOUNT_RATE_LIMITED_CODE,
    OPENAI_ACCOUNT_DEVICE_AUTH_URL,
    OPENAI_ACCOUNT_DEVICE_CALLBACK_URL,
    OPENAI_ACCOUNT_DEVICE_TOKEN_URL,
    OPENAI_ACCOUNT_DEVICE_USER_CODE_URL,
    OpenAIAccountAuthError,
    OpenAIAccountDeviceAuthorization,
    OpenAIAccountDeviceCode,
    OpenAIAccountAuthManager,
    OpenAIAccountTokens,
    default_openai_account_auth_path,
    exchange_openai_account_device_authorization,
    login_openai_account_oauth,
    poll_openai_account_device_authorization,
    poll_openai_account_device_authorization_once,
    request_openai_account_device_code,
    refresh_openai_account_oauth,
)


def _auth_manager(tmp_path, now=lambda: 1000.0):
    return OpenAIAccountAuthManager(auth_path=tmp_path / "auth.json", now=now)


def _jwt_with_exp(exp: int) -> str:
    payload = json.dumps({"exp": exp}).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("utf-8").rstrip("=")
    return f"header.{encoded}.signature"


class _FakeResponse:
    def __init__(self, status_code, payload=None, headers=None, json_error=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._payload


class _FakeHttpxClient:
    calls = []
    responses = []
    response = _FakeResponse(200, {})

    def __init__(self, *args, **kwargs):
        self.calls.append({"init_args": args, "init_kwargs": kwargs})

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url, *, headers=None, data=None, json=None):
        self.calls.append({"url": url, "headers": headers, "data": data, "json": json})
        if self.responses:
            return self.responses.pop(0)
        return self.response


def _patch_refresh_response(monkeypatch, response):
    _FakeHttpxClient.calls = []
    _FakeHttpxClient.responses = []
    _FakeHttpxClient.response = response
    monkeypatch.setattr(openai_account_auth.httpx, "Client", _FakeHttpxClient)


def _patch_httpx_responses(monkeypatch, responses):
    _FakeHttpxClient.calls = []
    _FakeHttpxClient.responses = list(responses)
    _FakeHttpxClient.response = responses[-1] if responses else _FakeResponse(200, {})
    monkeypatch.setattr(openai_account_auth.httpx, "Client", _FakeHttpxClient)


def test_default_openai_account_auth_path_uses_openjiuwen_home(monkeypatch, tmp_path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    assert default_openai_account_auth_path() == tmp_path / ".openjiuwen" / "auth.json"


def test_default_openai_account_auth_path_uses_env_overrides(monkeypatch, tmp_path):
    auth_file = tmp_path / "custom-auth.json"
    monkeypatch.setenv("OPENJIUWEN_AUTH_FILE", str(auth_file))

    assert default_openai_account_auth_path() == auth_file


def test_tokens_from_mapping_uses_expires_in():
    tokens = OpenAIAccountTokens.from_mapping(
        {"access_token": "access", "refresh_token": "refresh", "expires_in": 60},
        now=1000.0,
    )

    assert tokens.expires_at == 1060.0
    assert tokens.last_refresh == 1000.0


def test_tokens_from_mapping_reads_jwt_exp_when_expires_missing():
    tokens = OpenAIAccountTokens.from_mapping(
        {"access_token": _jwt_with_exp(2000), "refresh_token": "refresh"},
        now=1000.0,
    )

    assert tokens.expires_at == 2000.0


def test_status_reports_missing_auth_store(tmp_path):
    status = OpenAIAccountAuthManager(auth_path=tmp_path / "missing" / "auth.json").status()

    assert status.authenticated is False
    assert status.error == "openai_account_auth_missing"


def test_save_and_load_tokens_preserves_other_providers(tmp_path):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps({"providers": {"other": {"tokens": {"access_token": "x"}}}}),
        encoding="utf-8",
    )
    manager = OpenAIAccountAuthManager(auth_path=auth_path, now=lambda: 1000.0)

    manager.save_tokens(OpenAIAccountTokens(access_token="access", refresh_token="refresh", expires_at=2000.0))
    loaded = manager.load_tokens()
    store = json.loads(auth_path.read_text(encoding="utf-8"))

    assert loaded.access_token == "access"
    assert loaded.refresh_token == "refresh"
    assert store["providers"]["other"]["tokens"]["access_token"] == "x"
    assert store["providers"][OPENAI_ACCOUNT_PROVIDER]["auth_mode"] == "openai_account"


def test_save_tokens_creates_private_auth_file(tmp_path):
    auth_path = tmp_path / "nested" / "auth.json"
    manager = OpenAIAccountAuthManager(auth_path=auth_path, now=lambda: 1000.0)

    manager.save_tokens(OpenAIAccountTokens(access_token="access", refresh_token="refresh", expires_at=2000.0))

    assert auth_path.is_file()
    mode = stat.S_IMODE(auth_path.stat().st_mode)
    if os.name == "nt":
        assert mode & stat.S_IWRITE
    else:
        assert mode == 0o600


def test_resolve_access_token_returns_valid_token(tmp_path):
    manager = _auth_manager(tmp_path, now=lambda: 1000.0)
    manager.save_tokens(OpenAIAccountTokens(access_token="access", refresh_token="refresh", expires_at=2000.0))

    assert manager.resolve_access_token() == "access"


def test_request_device_code_posts_client_id(monkeypatch):
    _patch_refresh_response(
        monkeypatch,
        _FakeResponse(
            200,
            {
                "user_code": "ABCD-EFGH",
                "device_auth_id": "device-auth",
                "interval": 1,
                "expires_in": 900,
            },
        ),
    )

    device_code = request_openai_account_device_code()

    assert device_code == OpenAIAccountDeviceCode(
        user_code="ABCD-EFGH",
        device_auth_id="device-auth",
        verification_uri=OPENAI_ACCOUNT_DEVICE_AUTH_URL,
        interval=3,
        expires_in=900,
    )
    assert _FakeHttpxClient.calls[1]["url"] == OPENAI_ACCOUNT_DEVICE_USER_CODE_URL
    assert _FakeHttpxClient.calls[1]["json"] == {"client_id": OPENAI_ACCOUNT_OAUTH_CLIENT_ID}


def test_request_device_code_missing_fields_requires_relogin(monkeypatch):
    _patch_refresh_response(monkeypatch, _FakeResponse(200, {"user_code": "ABCD-EFGH"}))

    with pytest.raises(OpenAIAccountAuthError) as error:
        request_openai_account_device_code()

    assert error.value.code == "openai_account_device_code_incomplete"
    assert error.value.relogin_required is True


def test_poll_device_authorization_skips_pending_and_returns_authorization(monkeypatch):
    _patch_httpx_responses(
        monkeypatch,
        [
            _FakeResponse(403, {}),
            _FakeResponse(200, {"authorization_code": "auth-code", "code_verifier": "verifier"}),
        ],
    )
    sleeps = []
    device_code = OpenAIAccountDeviceCode(user_code="ABCD-EFGH", device_auth_id="device-auth", interval=3)

    authorization = poll_openai_account_device_authorization(
        device_code,
        sleep=sleeps.append,
        monotonic=lambda: 0.0,
    )

    assert authorization == OpenAIAccountDeviceAuthorization(
        authorization_code="auth-code",
        code_verifier="verifier",
    )
    assert sleeps == [3, 3]
    assert _FakeHttpxClient.calls[1]["url"] == OPENAI_ACCOUNT_DEVICE_TOKEN_URL
    assert _FakeHttpxClient.calls[1]["json"] == {
        "device_auth_id": "device-auth",
        "user_code": "ABCD-EFGH",
    }


def test_poll_device_authorization_once_returns_none_while_pending(monkeypatch):
    _patch_refresh_response(monkeypatch, _FakeResponse(404, {}))
    device_code = OpenAIAccountDeviceCode(user_code="ABCD-EFGH", device_auth_id="device-auth", interval=3)

    authorization = poll_openai_account_device_authorization_once(device_code)

    assert authorization is None
    assert _FakeHttpxClient.calls[1]["url"] == OPENAI_ACCOUNT_DEVICE_TOKEN_URL
    assert _FakeHttpxClient.calls[1]["json"] == {
        "device_auth_id": "device-auth",
        "user_code": "ABCD-EFGH",
    }


def test_poll_device_authorization_timeout(monkeypatch):
    _patch_refresh_response(monkeypatch, _FakeResponse(403, {}))
    ticks = iter([0.0, 1.0, 3.0])
    device_code = OpenAIAccountDeviceCode(user_code="ABCD-EFGH", device_auth_id="device-auth", interval=3)

    with pytest.raises(OpenAIAccountAuthError) as error:
        poll_openai_account_device_authorization(
            device_code,
            max_wait_seconds=2,
            sleep=lambda seconds: None,
            monotonic=lambda: next(ticks),
        )

    assert error.value.code == "openai_account_device_code_timeout"
    assert error.value.relogin_required is True


def test_exchange_device_authorization_posts_authorization_form(monkeypatch):
    _patch_refresh_response(
        monkeypatch,
        _FakeResponse(200, {"access_token": "access", "refresh_token": "refresh", "expires_in": 60}),
    )

    tokens = exchange_openai_account_device_authorization(
        OpenAIAccountDeviceAuthorization(authorization_code="auth-code", code_verifier="verifier"),
        now=lambda: 1000.0,
    )

    assert tokens.access_token == "access"
    assert tokens.refresh_token == "refresh"
    assert tokens.expires_at == 1060.0
    assert _FakeHttpxClient.calls[1]["url"] == OPENAI_ACCOUNT_OAUTH_TOKEN_URL
    assert _FakeHttpxClient.calls[1]["data"] == {
        "grant_type": "authorization_code",
        "code": "auth-code",
        "redirect_uri": OPENAI_ACCOUNT_DEVICE_CALLBACK_URL,
        "client_id": OPENAI_ACCOUNT_OAUTH_CLIENT_ID,
        "code_verifier": "verifier",
    }


def test_token_exchange_error_mentions_exchange(monkeypatch):
    _patch_refresh_response(
        monkeypatch,
        _FakeResponse(400, {"error": "invalid_grant", "error_description": "bad code"}),
    )

    with pytest.raises(OpenAIAccountAuthError) as error:
        exchange_openai_account_device_authorization(
            OpenAIAccountDeviceAuthorization(authorization_code="auth-code", code_verifier="verifier"),
        )

    assert error.value.code == "invalid_grant"
    assert error.value.relogin_required is True
    assert "token exchange failed" in str(error.value)


def test_login_openai_account_oauth_runs_full_device_flow(monkeypatch):
    _patch_httpx_responses(
        monkeypatch,
        [
            _FakeResponse(200, {"user_code": "ABCD-EFGH", "device_auth_id": "device-auth", "interval": 3}),
            _FakeResponse(200, {"authorization_code": "auth-code", "code_verifier": "verifier"}),
            _FakeResponse(200, {"access_token": "access", "refresh_token": "refresh", "expires_in": 60}),
        ],
    )
    seen_device_codes = []

    tokens = login_openai_account_oauth(
        on_device_code=seen_device_codes.append,
        sleep=lambda seconds: None,
        monotonic=lambda: 0.0,
        now=lambda: 1000.0,
    )

    assert tokens.access_token == "access"
    assert tokens.refresh_token == "refresh"
    assert seen_device_codes[0].user_code == "ABCD-EFGH"


def test_manager_login_with_device_code_saves_tokens(monkeypatch, tmp_path):
    _patch_httpx_responses(
        monkeypatch,
        [
            _FakeResponse(200, {"user_code": "ABCD-EFGH", "device_auth_id": "device-auth", "interval": 3}),
            _FakeResponse(200, {"authorization_code": "auth-code", "code_verifier": "verifier"}),
            _FakeResponse(200, {"access_token": "access", "refresh_token": "refresh", "expires_in": 60}),
        ],
    )
    manager = _auth_manager(tmp_path, now=lambda: 1000.0)

    tokens = manager.login_with_device_code(sleep=lambda seconds: None, monotonic=lambda: 0.0)
    loaded = manager.load_tokens()

    assert tokens.access_token == "access"
    assert loaded.access_token == "access"
    assert loaded.refresh_token == "refresh"
    assert loaded.expires_at == 1060.0


def test_manager_start_device_login_returns_device_code(monkeypatch, tmp_path):
    _patch_refresh_response(
        monkeypatch,
        _FakeResponse(200, {"user_code": "ABCD-EFGH", "device_auth_id": "device-auth", "interval": 3}),
    )
    manager = _auth_manager(tmp_path, now=lambda: 1000.0)

    device_code = manager.start_device_login(timeout_seconds=1.5, max_attempts=1)

    assert device_code.user_code == "ABCD-EFGH"
    assert device_code.device_auth_id == "device-auth"
    assert _FakeHttpxClient.calls[1]["url"] == OPENAI_ACCOUNT_DEVICE_USER_CODE_URL


def test_manager_poll_device_login_returns_none_while_pending(monkeypatch, tmp_path):
    _patch_refresh_response(monkeypatch, _FakeResponse(403, {}))
    manager = _auth_manager(tmp_path, now=lambda: 1000.0)
    device_code = OpenAIAccountDeviceCode(user_code="ABCD-EFGH", device_auth_id="device-auth", interval=3)

    tokens = manager.poll_device_login(device_code)

    assert tokens is None
    assert manager.status().authenticated is False


def test_manager_poll_device_login_exchanges_and_saves_tokens(monkeypatch, tmp_path):
    _patch_httpx_responses(
        monkeypatch,
        [
            _FakeResponse(200, {"authorization_code": "auth-code", "code_verifier": "verifier"}),
            _FakeResponse(200, {"access_token": "access", "refresh_token": "refresh", "expires_in": 60}),
        ],
    )
    manager = _auth_manager(tmp_path, now=lambda: 1000.0)
    device_code = OpenAIAccountDeviceCode(user_code="ABCD-EFGH", device_auth_id="device-auth", interval=3)

    tokens = manager.poll_device_login(device_code)
    loaded = manager.load_tokens()

    assert tokens is not None
    assert tokens.access_token == "access"
    assert loaded.access_token == "access"
    assert loaded.refresh_token == "refresh"
    assert loaded.expires_at == 1060.0


def test_refresh_oauth_posts_refresh_token_and_keeps_existing_refresh_token(monkeypatch):
    _patch_refresh_response(
        monkeypatch,
        _FakeResponse(200, {"access_token": "new-access", "expires_in": 60}),
    )

    tokens = refresh_openai_account_oauth("old-access", "refresh", now=lambda: 1000.0)

    assert tokens.access_token == "new-access"
    assert tokens.refresh_token == "refresh"
    assert tokens.expires_at == 1060.0
    assert tokens.last_refresh == 1000.0
    assert _FakeHttpxClient.calls[1]["url"] == OPENAI_ACCOUNT_OAUTH_TOKEN_URL
    assert _FakeHttpxClient.calls[1]["data"] == {
        "grant_type": "refresh_token",
        "refresh_token": "refresh",
        "client_id": OPENAI_ACCOUNT_OAUTH_CLIENT_ID,
    }


def test_refresh_oauth_uses_rotated_refresh_token(monkeypatch):
    _patch_refresh_response(
        monkeypatch,
        _FakeResponse(200, {"access_token": "new-access", "refresh_token": "new-refresh"}),
    )

    tokens = refresh_openai_account_oauth("old-access", "refresh", now=lambda: 1000.0)

    assert tokens.access_token == "new-access"
    assert tokens.refresh_token == "new-refresh"


def test_resolve_access_token_refreshes_and_saves_expiring_token(monkeypatch, tmp_path):
    _patch_refresh_response(
        monkeypatch,
        _FakeResponse(200, {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 600}),
    )
    manager = _auth_manager(tmp_path, now=lambda: 1000.0)
    manager.save_tokens(OpenAIAccountTokens(access_token="access", refresh_token="refresh", expires_at=1050.0))

    assert manager.resolve_access_token() == "new-access"
    loaded = manager.load_tokens()

    assert loaded.access_token == "new-access"
    assert loaded.refresh_token == "new-refresh"
    assert loaded.expires_at == 1600.0


def test_refresh_tokens_skips_http_when_token_is_already_valid(monkeypatch, tmp_path):
    _patch_refresh_response(
        monkeypatch,
        _FakeResponse(200, {"access_token": "unexpected-access"}),
    )
    manager = _auth_manager(tmp_path, now=lambda: 1000.0)
    manager.save_tokens(OpenAIAccountTokens(access_token="access", refresh_token="refresh", expires_at=2000.0))

    tokens = manager.refresh_tokens()

    assert tokens.access_token == "access"
    assert len(_FakeHttpxClient.calls) == 0


def test_refresh_oauth_invalid_grant_requires_relogin(monkeypatch):
    _patch_refresh_response(
        monkeypatch,
        _FakeResponse(400, {"error": "invalid_grant", "error_description": "bad refresh"}),
    )

    with pytest.raises(OpenAIAccountAuthError) as error:
        refresh_openai_account_oauth("old-access", "refresh", now=lambda: 1000.0)

    assert error.value.code == "invalid_grant"
    assert error.value.relogin_required is True
    assert error.value.status_code == 400


def test_refresh_oauth_rate_limit_does_not_require_relogin(monkeypatch):
    _patch_refresh_response(
        monkeypatch,
        _FakeResponse(429, {"error": "rate_limited"}, headers={"Retry-After": "7"}),
    )

    with pytest.raises(OpenAIAccountAuthError) as error:
        refresh_openai_account_oauth("old-access", "refresh", now=lambda: 1000.0)

    assert error.value.code == OPENAI_ACCOUNT_RATE_LIMITED_CODE
    assert error.value.relogin_required is False
    assert error.value.status_code == 429
    assert "7s" in str(error.value)


def test_refresh_oauth_invalid_json_requires_relogin(monkeypatch):
    _patch_refresh_response(
        monkeypatch,
        _FakeResponse(200, json_error=ValueError("not json")),
    )

    with pytest.raises(OpenAIAccountAuthError) as error:
        refresh_openai_account_oauth("old-access", "refresh", now=lambda: 1000.0)

    assert error.value.code == "openai_account_auth_refresh_invalid_json"
    assert error.value.relogin_required is True


def test_refresh_oauth_missing_access_token_requires_relogin(monkeypatch):
    _patch_refresh_response(
        monkeypatch,
        _FakeResponse(200, {"refresh_token": "new-refresh"}),
    )

    with pytest.raises(OpenAIAccountAuthError) as error:
        refresh_openai_account_oauth("old-access", "refresh", now=lambda: 1000.0)

    assert error.value.code == "openai_account_auth_refresh_missing_access_token"
    assert error.value.relogin_required is True


def test_request_device_code_wraps_network_errors(monkeypatch):
    class FailingClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, *args, **kwargs):
            raise httpx.ConnectError("network down")

    monkeypatch.setattr(openai_account_auth.httpx, "Client", FailingClient)

    with pytest.raises(OpenAIAccountAuthError) as error:
        request_openai_account_device_code(max_attempts=1)

    assert error.value.code == "openai_account_device_code_network_error"
    assert error.value.relogin_required is False


def test_logout_removes_only_openai_account_provider(tmp_path):
    manager = _auth_manager(tmp_path)
    manager.save_tokens(OpenAIAccountTokens(access_token="access", refresh_token="refresh", expires_at=2000.0))

    assert manager.logout() is True
    store = json.loads((tmp_path / "auth.json").read_text(encoding="utf-8"))

    assert OPENAI_ACCOUNT_PROVIDER not in store["providers"]
    assert manager.logout() is False
