# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Build Codex Python SDK options for external team members."""

from __future__ import annotations

import json
import re
from typing import Any

from openjiuwen.agent_teams.external.descriptor import TEAM_JOIN_ENV
from openjiuwen.agent_teams.schema.team import ExternalCliModelConfig
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error

_MCP_STARTUP_TIMEOUT_S = 120
_REASONING_SUMMARY = "detailed"
_OTEL_TRACE_EXPORT_DELAY_MS = 100
_CODEX_API_KEY_ENV = "OPENJIUWEN_CODEX_API_KEY"
# TOML bare-key pattern (A-Za-z0-9_-). Codex parses each --config value as
# TOML, and a dotted-path segment that is a bare key names a different table
# from the same string wrapped in quotes, so provider table keys must stay
# bare whenever the provider name allows it.
_BARE_KEY_RE = re.compile(r"[A-Za-z0-9_-]+")


def load_codex_sdk() -> Any:
    """Import the Codex SDK only when a Codex member needs it."""
    try:
        import openai_codex
    except ImportError as exc:
        raise_error(
            StatusCode.AGENT_TEAM_CONFIG_INVALID,
            reason="codex external CLI members require the openai-codex dependency",
            cause=exc,
        )
        raise AssertionError("raise_error should have raised") from exc
    return openai_codex


def build_codex_config(
    *,
    cwd: str | None,
    env: dict[str, str],
    inject_mcp: bool,
    mcp_server_name: str,
    mcp_server_command: tuple[str, ...],
    mcp_default_tools_approval_mode: str | None,
    member_name: str,
    codex_bin: str | None,
    external_model_config: ExternalCliModelConfig | None = None,
    native_otel_trace_endpoint: str | None = None,
    rollout_trace_root: str | None = None,
    sdk: Any | None = None,
) -> Any:
    """Build ``CodexConfig`` without importing the optional SDK eagerly."""
    sdk = sdk or load_codex_sdk()
    process_env = dict(env)
    if rollout_trace_root:
        process_env["CODEX_ROLLOUT_TRACE_ROOT"] = rollout_trace_root
    config_overrides: tuple[str, ...] = ()
    if external_model_config is not None:
        config_overrides += codex_model_config_overrides(external_model_config)
        if external_model_config.api_key:
            process_env[_CODEX_API_KEY_ENV] = external_model_config.api_key
    if native_otel_trace_endpoint:
        # Codex uses an OTel batch span processor. Keep its delivery interval
        # below Jiuwen's turn-finalization grace period so the native logical
        # sampling span arrives before the member turn is finalized.
        process_env.setdefault(
            "OTEL_BSP_SCHEDULE_DELAY",
            str(_OTEL_TRACE_EXPORT_DELAY_MS),
        )
        config_overrides += (
            'otel.environment="openjiuwen"',
            "otel.exporter=none",
            (
                "otel.trace_exporter={ otlp-http = { "
                f"endpoint = {json.dumps(native_otel_trace_endpoint)}, "
                'protocol = "binary" } }'
            ),
            "otel.metrics_exporter=none",
            "otel.log_user_prompt=false",
        )
    if inject_mcp:
        if not mcp_server_command:
            raise_error(
                StatusCode.AGENT_TEAM_CONFIG_INVALID,
                reason="Codex SDK MCP injection requires a non-empty mcp_server_command",
            )
            raise AssertionError  # pragma: no cover - raise_error always raises
        config_overrides += codex_mcp_config_overrides(
            server_name=mcp_server_name,
            server_command=mcp_server_command,
            default_tools_approval_mode=mcp_default_tools_approval_mode,
        )

    return sdk.CodexConfig(
        codex_bin=codex_bin,
        config_overrides=config_overrides,
        cwd=cwd,
        env=process_env,
        client_name="openjiuwen_agent_team",
        client_title=f"OpenJiuwen Team Member {member_name}",
        client_version="1",
    )


def build_codex_thread_options(
    *,
    cwd: str | None,
    system_prompt: str | None,
    external_model_config: ExternalCliModelConfig | None = None,
    bypass_approvals_and_sandbox: bool = False,
    sdk: Any | None = None,
) -> dict[str, Any]:
    """Build thread options, including an SDK-visible reasoning summary."""
    options: dict[str, Any] = {
        "ephemeral": False,
        "config": {"model_reasoning_summary": _REASONING_SUMMARY},
    }
    if cwd:
        options["cwd"] = cwd
    if system_prompt:
        options["developer_instructions"] = system_prompt
    if external_model_config is not None:
        if external_model_config.model:
            options["model"] = external_model_config.model
        if external_model_config.provider:
            options["model_provider"] = external_model_config.provider
    # An external model targets a non-OpenAI endpoint. Codex's auto-review
    # approval reviewer uses a built-in ``codex-auto-review`` model that cannot
    # be redirected to an external provider (verified: ``auto_review_model_override``
    # and related keys are rejected as unknown fields under ``--strict-config``),
    # so any auto-review call against an external endpoint is guaranteed to fail.
    # Bypass the reviewer whenever an external model is configured: switch to
    # ``deny_all`` (never ask for approval) plus ``full_access`` sandbox so tool
    # calls run under the framework's own permission engine instead of codex's.
    bypass = bypass_approvals_and_sandbox or external_model_config is not None
    if bypass:
        sdk = sdk or load_codex_sdk()
        options["approval_mode"] = sdk.ApprovalMode.deny_all
        options["sandbox"] = sdk.Sandbox.full_access
    return options


def _dotted_table_key(name: str) -> str:
    """Render a table key for a codex ``--config`` dotted-path segment.

    Codex parses the value portion of ``--config`` as TOML, and a dotted-path
    segment that is a bare key (``deepseek``) names a different table from the
    same string wrapped in quotes (``"deepseek"``). ``model_provider`` always
    references the provider by its TOML string value, so the matching
    ``model_providers.<key>.*`` segments must use a bare key whenever the
    provider name is bare-key-safe, and fall back to a quoted key otherwise.
    """
    if name and _BARE_KEY_RE.fullmatch(name):
        return name
    return json.dumps(name)


def codex_model_config_overrides(config: ExternalCliModelConfig) -> tuple[str, ...]:
    """Render Codex provider config overrides from the external model config."""
    if not config.provider:
        return ()
    provider = config.provider
    overrides = [f"model_provider={json.dumps(provider)}"]
    provider_key = _dotted_table_key(provider)
    overrides.append(f"model_providers.{provider_key}.name={json.dumps(provider)}")
    if config.api_base:
        overrides.append(f"model_providers.{provider_key}.base_url={json.dumps(config.api_base)}")
    if config.api_key:
        overrides.append(f"model_providers.{provider_key}.env_key={json.dumps(_CODEX_API_KEY_ENV)}")
    return tuple(overrides)


def codex_mcp_config_overrides(
    *,
    server_name: str,
    server_command: tuple[str, ...],
    default_tools_approval_mode: str | None = None,
) -> tuple[str, ...]:
    """Render ``mcp_servers.*`` entries for ``CodexConfig.config_overrides``."""
    if not server_command:
        return ()
    key = server_name.replace("-", "_")
    binary, *args = server_command
    overrides = [f"mcp_servers.{key}.command={json.dumps(binary)}"]
    if args:
        overrides.append(f"mcp_servers.{key}.args={json.dumps(args)}")
    overrides.extend(
        [
            f"mcp_servers.{key}.env_vars={json.dumps([TEAM_JOIN_ENV])}",
            f"mcp_servers.{key}.startup_timeout_sec={_MCP_STARTUP_TIMEOUT_S}",
            f"mcp_servers.{key}.required=true",
        ]
    )
    if default_tools_approval_mode is not None:
        overrides.append(f"mcp_servers.{key}.default_tools_approval_mode={json.dumps(default_tools_approval_mode)}")
    return tuple(overrides)


__all__ = [
    "build_codex_config",
    "build_codex_thread_options",
    "codex_model_config_overrides",
    "codex_mcp_config_overrides",
    "load_codex_sdk",
]
