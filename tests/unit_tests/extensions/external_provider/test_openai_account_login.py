# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the OpenAIAccount login test script."""

import json
from pathlib import Path

from openjiuwen.extensions.external_provider.openai_auth import openai_account_login
from openjiuwen.extensions.external_provider.openai_auth.openai_account_auth import (
    OPENAI_ACCOUNT_PROVIDER,
    OpenAIAccountAuthManager,
    OpenAIAccountAuthStatus,
    OpenAIAccountDeviceCode,
    OpenAIAccountTokens,
)


def _capture_login_logs(monkeypatch):
    info_messages = []
    error_messages = []
    monkeypatch.setattr(openai_account_login, "_log_info", lambda message="": info_messages.append(message))
    monkeypatch.setattr(openai_account_login, "_log_error", lambda message: error_messages.append(message))
    return info_messages, error_messages


def test_script_status_reports_missing_auth_store(monkeypatch, tmp_path):
    auth_path = tmp_path / "auth.json"
    info_messages, _ = _capture_login_logs(monkeypatch)

    result = openai_account_login.main(["status", "--auth-path", str(auth_path)])
    output = "\n".join(info_messages)

    assert result == 0
    assert "OpenAIAccount: not authenticated" in output
    assert "openai_account_auth_missing" in output


def test_script_status_json_reports_authenticated_store(monkeypatch, tmp_path):
    auth_path = tmp_path / "auth.json"
    info_messages, _ = _capture_login_logs(monkeypatch)
    manager = OpenAIAccountAuthManager(auth_path=auth_path, now=lambda: 1000.0)
    manager.save_tokens(
        OpenAIAccountTokens(
            access_token="access",
            refresh_token="refresh",
            expires_at=9999999999.0,
        )
    )

    result = openai_account_login.main(["status", "--auth-path", str(auth_path), "--json"])
    payload = json.loads(info_messages[0])

    assert result == 0
    assert payload["provider"] == "OpenAIAccount"
    assert payload["authenticated"] is True
    assert payload["has_refresh_token"] is True
    assert payload["needs_refresh"] is False
    assert payload["auth_path"] == str(auth_path)


def test_script_logout_removes_only_openai_account_provider(monkeypatch, tmp_path):
    auth_path = tmp_path / "auth.json"
    info_messages, _ = _capture_login_logs(monkeypatch)
    auth_path.write_text(
        json.dumps({
            "providers": {
                OPENAI_ACCOUNT_PROVIDER: {"tokens": {"access_token": "access", "refresh_token": "refresh"}},
                "other": {"tokens": {"access_token": "other"}},
            }
        }),
        encoding="utf-8",
    )

    result = openai_account_login.main(["logout", "--auth-path", str(auth_path)])
    output = "\n".join(info_messages)
    store = json.loads(auth_path.read_text(encoding="utf-8"))

    assert result == 0
    assert "credentials removed" in output
    assert OPENAI_ACCOUNT_PROVIDER not in store["providers"]
    assert store["providers"]["other"]["tokens"]["access_token"] == "other"


def test_script_reports_auth_store_filesystem_errors(monkeypatch, tmp_path):
    auth_path = tmp_path / "auth.json"
    _, error_messages = _capture_login_logs(monkeypatch)

    class FakeManager:
        def __init__(self, auth_path=None):
            self.auth_path = Path(auth_path)

        def logout(self):
            raise PermissionError("permission denied")

    monkeypatch.setattr(openai_account_login, "OpenAIAccountAuthManager", FakeManager)

    result = openai_account_login.main(["logout", "--auth-path", str(auth_path)])

    assert result == 1
    assert "failed to access OpenAIAccount auth store" in "\n".join(error_messages)
    assert "permission denied" in "\n".join(error_messages)


def test_script_login_displays_device_code_and_saves_through_manager(monkeypatch, tmp_path):
    auth_path = tmp_path / "auth.json"
    calls = {}
    info_messages, _ = _capture_login_logs(monkeypatch)

    class FakeManager:
        def __init__(self, auth_path=None):
            self.auth_path = Path(auth_path)

        def status(self):
            return OpenAIAccountAuthStatus(
                authenticated=False,
                auth_path=self.auth_path,
                error="openai_account_auth_missing",
            )

        def login_with_device_code(
                self,
                *,
                on_device_code=None,
                timeout_seconds=15.0,
                max_wait_seconds=15 * 60,
        ):
            calls["timeout_seconds"] = timeout_seconds
            calls["max_wait_seconds"] = max_wait_seconds
            if on_device_code:
                on_device_code(
                    OpenAIAccountDeviceCode(
                        user_code="ABCD-EFGH",
                        device_auth_id="device-auth",
                    )
                )
            return OpenAIAccountTokens(access_token="access", refresh_token="refresh")

    monkeypatch.setattr(openai_account_login, "OpenAIAccountAuthManager", FakeManager)

    result = openai_account_login.main(
        [
            "login",
            "--auth-path",
            str(auth_path),
            "--timeout",
            "1.5",
            "--max-wait",
            "9",
        ],
    )
    output = "\n".join(info_messages)

    assert result == 0
    assert "https://auth.openai.com/codex/device" in output
    assert "ABCD-EFGH" in output
    assert "Login successful." in output
    assert calls == {"timeout_seconds": 1.5, "max_wait_seconds": 9}
