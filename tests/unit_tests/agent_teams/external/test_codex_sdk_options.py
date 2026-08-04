# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for optional Codex SDK loading."""

from __future__ import annotations

import builtins
import importlib
import sys
from types import ModuleType
from types import SimpleNamespace

import pytest

from openjiuwen.core.common.exception.errors import BaseError


class _FakeCodexConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


_FAKE_SDK = SimpleNamespace(CodexConfig=_FakeCodexConfig)


def test_codex_options_import_does_not_eagerly_import_sdk(monkeypatch):
    """Importing Jiuwen's Codex options must not require the optional SDK."""
    monkeypatch.delitem(sys.modules, "openai_codex", raising=False)
    options = importlib.import_module("openjiuwen.agent_teams.external.cli_agent.codex.options")
    importlib.reload(options)
    assert "openai_codex" not in sys.modules


def test_load_codex_sdk_returns_imported_module(monkeypatch):
    """The SDK is imported only when the loader is called."""
    sdk = ModuleType("openai_codex")
    monkeypatch.setitem(sys.modules, "openai_codex", sdk)
    from openjiuwen.agent_teams.external.cli_agent.codex.options import load_codex_sdk

    assert load_codex_sdk() is sdk


def test_load_codex_sdk_reports_missing_optional_dependency(monkeypatch):
    """A missing SDK produces a Codex-specific configuration error."""
    real_import = builtins.__import__

    def missing_codex(name, *args, **kwargs):
        if name == "openai_codex":
            raise ImportError("openai_codex is unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "openai_codex", raising=False)
    monkeypatch.setattr(builtins, "__import__", missing_codex)
    from openjiuwen.agent_teams.external.cli_agent.codex.options import load_codex_sdk

    with pytest.raises(BaseError, match="openai-codex"):
        load_codex_sdk()


def test_build_codex_config_uses_sdk_config_and_mcp_overrides():
    from openjiuwen.agent_teams.external.cli_agent.codex.options import build_codex_config

    config = build_codex_config(
        cwd="/workspace",
        env={"TEAM": "one"},
        inject_mcp=True,
        mcp_server_name="openjiuwen-team",
        mcp_server_command=("openjiuwen-team-mcp", "--stdio"),
        mcp_default_tools_approval_mode="approve",
        member_name="developer",
        codex_bin=None,
        sdk=_FAKE_SDK,
    )

    assert config.kwargs["cwd"] == "/workspace"
    assert config.kwargs["env"] == {"TEAM": "one"}
    assert config.kwargs["codex_bin"] is None
    assert config.kwargs["client_name"] == "openjiuwen_agent_team"
    assert 'mcp_servers.openjiuwen_team.command="openjiuwen-team-mcp"' in config.kwargs["config_overrides"]
    assert 'mcp_servers.openjiuwen_team.args=["--stdio"]' in config.kwargs["config_overrides"]
    assert 'mcp_servers.openjiuwen_team.default_tools_approval_mode="approve"' in config.kwargs["config_overrides"]


def test_build_codex_config_uses_custom_binary_without_rebuilding_app_server_argv():
    from openjiuwen.agent_teams.external.cli_agent.codex.options import build_codex_config

    config = build_codex_config(
        cwd=None,
        env={},
        inject_mcp=True,
        mcp_server_name="team",
        mcp_server_command=("team-mcp",),
        mcp_default_tools_approval_mode=None,
        member_name="developer",
        codex_bin="/opt/codex",
        sdk=_FAKE_SDK,
    )

    assert config.kwargs["codex_bin"] == "/opt/codex"
    assert 'mcp_servers.team.command="team-mcp"' in config.kwargs["config_overrides"]
    assert not any("default_tools_approval_mode" in item for item in config.kwargs["config_overrides"])
    assert "launch_args_override" not in config.kwargs


def test_build_codex_config_routes_native_otel_traces_to_loopback_receiver():
    from openjiuwen.agent_teams.external.cli_agent.codex.options import build_codex_config

    config = build_codex_config(
        cwd="/workspace",
        env={},
        inject_mcp=False,
        mcp_server_name="team",
        mcp_server_command=(),
        mcp_default_tools_approval_mode=None,
        member_name="developer",
        codex_bin=None,
        native_otel_trace_endpoint="http://127.0.0.1:4318/v1/traces",
        sdk=_FAKE_SDK,
    )

    overrides = config.kwargs["config_overrides"]
    assert 'otel.environment="openjiuwen"' in overrides
    assert (
        'otel.trace_exporter={ otlp-http = { endpoint = "http://127.0.0.1:4318/v1/traces", protocol = "binary" } }'
    ) in overrides
    assert "otel.exporter=none" in overrides
    assert "otel.metrics_exporter=none" in overrides
    assert "otel.log_user_prompt=false" in overrides
    assert config.kwargs["env"]["OTEL_BSP_SCHEDULE_DELAY"] == "100"


def test_build_codex_config_preserves_explicit_otel_trace_export_delay():
    from openjiuwen.agent_teams.external.cli_agent.codex.options import build_codex_config

    config = build_codex_config(
        cwd="/workspace",
        env={"OTEL_BSP_SCHEDULE_DELAY": "250"},
        inject_mcp=False,
        mcp_server_name="team",
        mcp_server_command=(),
        mcp_default_tools_approval_mode=None,
        member_name="developer",
        codex_bin=None,
        native_otel_trace_endpoint="http://127.0.0.1:4318/v1/traces",
        sdk=_FAKE_SDK,
    )

    assert config.kwargs["env"]["OTEL_BSP_SCHEDULE_DELAY"] == "250"


def test_build_codex_config_enables_private_rollout_trace_root():
    from openjiuwen.agent_teams.external.cli_agent.codex.options import build_codex_config

    config = build_codex_config(
        cwd="/workspace",
        env={"TEAM": "one"},
        inject_mcp=False,
        mcp_server_name="team",
        mcp_server_command=(),
        mcp_default_tools_approval_mode=None,
        member_name="developer",
        codex_bin=None,
        rollout_trace_root="/tmp/codex-rollout",
        sdk=_FAKE_SDK,
    )

    assert config.kwargs["env"] == {
        "TEAM": "one",
        "CODEX_ROLLOUT_TRACE_ROOT": "/tmp/codex-rollout",
    }


def test_build_codex_config_keeps_trace_and_mcp_overrides_together():
    from openjiuwen.agent_teams.external.cli_agent.codex.options import build_codex_config

    config = build_codex_config(
        cwd="/workspace",
        env={},
        inject_mcp=True,
        mcp_server_name="team",
        mcp_server_command=("team-mcp", "--stdio"),
        mcp_default_tools_approval_mode=None,
        member_name="developer",
        codex_bin=None,
        native_otel_trace_endpoint="http://127.0.0.1:4318/v1/traces",
        sdk=_FAKE_SDK,
    )

    overrides = config.kwargs["config_overrides"]
    assert any(item.startswith("otel.trace_exporter=") for item in overrides)
    assert 'mcp_servers.team.command="team-mcp"' in overrides
    assert 'mcp_servers.team.args=["--stdio"]' in overrides


def test_build_codex_thread_options_leave_approval_and_sandbox_unset():
    from openjiuwen.agent_teams.external.cli_agent.codex.options import build_codex_thread_options

    options = build_codex_thread_options(
        cwd="/workspace",
        system_prompt="You are the developer.",
    )

    assert options == {
        "ephemeral": False,
        "config": {"model_reasoning_summary": "detailed"},
        "cwd": "/workspace",
        "developer_instructions": "You are the developer.",
    }
    assert "approval_mode" not in options
    assert "sandbox" not in options


def test_build_codex_thread_options_can_explicitly_bypass_safety_boundaries():
    from openjiuwen.agent_teams.external.cli_agent.codex.options import build_codex_thread_options

    sdk = SimpleNamespace(
        ApprovalMode=SimpleNamespace(deny_all="deny-all"),
        Sandbox=SimpleNamespace(full_access="full-access"),
    )
    options = build_codex_thread_options(
        cwd="/workspace",
        system_prompt=None,
        bypass_approvals_and_sandbox=True,
        sdk=sdk,
    )

    assert options["approval_mode"] == "deny-all"
    assert options["sandbox"] == "full-access"


def test_build_codex_config_requires_mcp_command():
    from openjiuwen.agent_teams.external.cli_agent.codex.options import build_codex_config

    with pytest.raises(BaseError, match="non-empty mcp_server_command"):
        build_codex_config(
            cwd=None,
            env={},
            inject_mcp=True,
            mcp_server_name="team",
            mcp_server_command=(),
            mcp_default_tools_approval_mode=None,
            member_name="developer",
            codex_bin=None,
            sdk=_FAKE_SDK,
        )
