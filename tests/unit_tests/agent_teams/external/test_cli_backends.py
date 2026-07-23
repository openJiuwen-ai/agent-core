# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for external CLI backend registry."""

import pytest
from pydantic import ValidationError

from openjiuwen.agent_teams.external.cli_agent.backends import available_backends, backend_for, is_known_backend
from openjiuwen.agent_teams.schema.team import ExternalCliAgentSpec


def test_available_backends_includes_sdk_and_adapter_kinds():
    """Known SDK backends and adapter-backed CLIs are exposed together."""
    names = set(available_backends())
    assert {"claude", "codex", "gemini", "openclaw", "hermes", "generic"} <= names


def test_claude_backend_metadata_marks_sdk_prompt_injection():
    """Claude is a SDK backend, not a generic CLI adapter."""
    backend = backend_for("claude")
    assert backend is not None
    assert backend.kind == "sdk"
    assert not backend.supports_command_override
    assert backend.injects_system_prompt_via_arg


def test_codex_backend_metadata_marks_sdk_prompt_injection():
    """Codex is an SDK backend, not a generic CLI adapter."""
    backend = backend_for("codex")
    assert backend is not None
    assert backend.kind == "sdk"
    assert not backend.supports_command_override
    assert backend.injects_system_prompt_via_arg


def test_codex_static_config_uses_explicit_binary_not_full_command():
    config = ExternalCliAgentSpec(cli_agent="codex", codex_bin="/opt/codex")
    assert config.codex_bin == "/opt/codex"

    with pytest.raises(ValidationError, match="use codex_bin"):
        ExternalCliAgentSpec(cli_agent="codex", command=["codex", "app-server"])

    with pytest.raises(ValidationError, match="only valid"):
        ExternalCliAgentSpec(cli_agent="generic", codex_bin="/opt/codex")


def test_unknown_backend_returns_none():
    """Unknown backend names are rejected by registry helpers."""
    assert backend_for("not-a-real-cli") is None
    assert not is_known_backend("not-a-real-cli")
