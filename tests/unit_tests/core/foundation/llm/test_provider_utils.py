# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for provider utility helpers."""

import json

import pytest

from openjiuwen.core.foundation.llm.utils import provider_utils
from openjiuwen.core.foundation.llm.utils.provider_utils import (
    OPENAI_ACCOUNT_PROVIDER,
    SettingsJsonError,
    is_openai_account_provider,
    load_settings_json,
    normalize_provider,
    save_settings_json,
)


def test_openai_account_provider_aliases_are_normalized():
    assert normalize_provider("openai-account") == OPENAI_ACCOUNT_PROVIDER
    assert normalize_provider("openai_account") == OPENAI_ACCOUNT_PROVIDER
    assert normalize_provider(" OpenAIAccount ") == OPENAI_ACCOUNT_PROVIDER
    assert is_openai_account_provider("openai-account")


def test_malformed_settings_json_logs_warning(monkeypatch, tmp_path):
    warnings = []
    monkeypatch.setattr(
        provider_utils.logger,
        "warning",
        lambda message, *args, **kwargs: warnings.append(message % args),
    )
    path = tmp_path / "settings.json"
    path.write_text("{invalid json")

    assert load_settings_json(path) == {}
    assert "Failed to load settings" in warnings[0]
    assert "falling back to empty settings" in warnings[0]


def test_strict_mode_raises_for_invalid_existing_settings(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{invalid json")

    with pytest.raises(SettingsJsonError):
        load_settings_json(path, strict=True)


def test_save_settings_refuses_to_overwrite_malformed_existing_file(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{invalid json")

    with pytest.raises(SettingsJsonError):
        save_settings_json({"apiKey": "new"}, path)

    assert path.read_text() == "{invalid json"


def test_save_settings_refuses_to_overwrite_non_object_existing_file(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('["old"]')

    with pytest.raises(SettingsJsonError):
        save_settings_json({"apiKey": "new"}, path)

    assert path.read_text() == '["old"]'


def test_save_settings_json_keeps_existing_file_when_atomic_replace_fails(monkeypatch, tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"apiKey": "old", "model": "gpt-4o"}')

    def fail_replace(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(provider_utils.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        save_settings_json({"apiKey": "new"}, path)

    assert json.loads(path.read_text()) == {"apiKey": "old", "model": "gpt-4o"}
    assert list(tmp_path.glob(".settings.json.*.tmp")) == []
