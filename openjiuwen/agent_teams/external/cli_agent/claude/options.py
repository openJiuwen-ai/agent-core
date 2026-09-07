# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Build Claude Agent SDK options for team-member runtimes."""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error

if TYPE_CHECKING:
    from openjiuwen.agent_teams.schema.team import ExternalCliModelConfig

    from claude_agent_sdk import ClaudeAgentOptions


_CLAUDE_ENV_STRIP_PREFIXES = ("CLAUDECODE", "CLAUDE_CODE_")
_ANTHROPIC_AUTH_TOKEN_ENV = "ANTHROPIC_AUTH_TOKEN"
_ANTHROPIC_BASE_URL_ENV = "ANTHROPIC_BASE_URL"
# Claude Code's native OTel export: spans (beta) need both the master switch
# and the enhanced-telemetry beta flag; the CLI fails silently on exporter
# errors, so a short export interval keeps spans flowing before turn end.
_CLAUDE_OTEL_EXPORT_INTERVAL_MS = "1000"
_OTEL_RESOURCE_ATTRIBUTES_ENV = "OTEL_RESOURCE_ATTRIBUTES"


def claude_otel_env(
    endpoint: str,
    *,
    source_id: str | None = None,
    resource_attributes: str | None = None,
) -> dict[str, str]:
    """Build the env vars pointing Claude Code's OTel export at ``endpoint``.

    Claude Code's OTLP exporter only speaks gRPC — with ``http/protobuf`` it
    silently connects and never sends data (verified against CLI 2.1.206 and
    2.1.259) — so the protocol is pinned to grpc and ``endpoint`` must be the
    shared receiver's gRPC listener. Raw API body log events
    (``OTEL_LOG_RAW_API_BODIES=1``) carry the full Messages API
    request/response JSON to that receiver; the bridge redacts content before
    anything lands in our spans, so the export stays inside our observability
    pipeline.
    """
    result = {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA": "1",
        "OTEL_TRACES_EXPORTER": "otlp",
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc",
        "OTEL_EXPORTER_OTLP_ENDPOINT": endpoint,
        "OTEL_TRACES_EXPORT_INTERVAL": _CLAUDE_OTEL_EXPORT_INTERVAL_MS,
        "OTEL_LOGS_EXPORT_INTERVAL": _CLAUDE_OTEL_EXPORT_INTERVAL_MS,
        "OTEL_LOG_RAW_API_BODIES": "1",
        # The gRPC client honors http_proxy/https_proxy and would route the
        # loopback export through the user's proxy, which may not forward
        # 127.0.0.1 traffic. Exempt loopback instead of clearing the proxy
        # vars: business traffic (the model API) may legitimately need them.
        "no_proxy": "127.0.0.1,localhost",
        "NO_PROXY": "127.0.0.1,localhost",
    }
    if source_id:
        from openjiuwen.agent_teams.observability.shared_otlp import OTEL_RESOURCE_SOURCE_ID

        existing = [
            item
            for item in str(resource_attributes or "").split(",")
            if item and not item.startswith(f"{OTEL_RESOURCE_SOURCE_ID}=")
        ]
        existing.append(f"{OTEL_RESOURCE_SOURCE_ID}={source_id}")
        result[_OTEL_RESOURCE_ATTRIBUTES_ENV] = ",".join(existing)
    return result


def load_claude_sdk() -> Any:
    """Import the Claude Agent SDK only when a Claude member is used."""
    try:
        import claude_agent_sdk
    except ImportError as exc:
        raise_error(
            StatusCode.AGENT_TEAM_CONFIG_INVALID,
            reason="claude external CLI members require the claude-agent-sdk dependency",
            cause=exc,
        )
        raise AssertionError("raise_error should have raised") from exc
    return claude_agent_sdk


def build_claude_options(
    *,
    cwd: str | None,
    add_dirs: tuple[str, ...],
    env: dict[str, str],
    cli_path: str | None,
    system_prompt: str | None,
    team_session_id: str | None,
    member_name: str,
    resume_external_backend: bool,
    external_model_config: "ExternalCliModelConfig | None" = None,
    otel_trace_endpoint: str | None = None,
    otel_source_id: str | None = None,
) -> "ClaudeAgentOptions":
    """Build SDK options matching the previous Claude CLI member behavior."""
    sdk = load_claude_sdk()
    claude_session_id = build_claude_session_id(
        team_session_id=team_session_id,
        member_name=member_name,
    )
    session_id = None if resume_external_backend else claude_session_id
    resume = claude_session_id if resume_external_backend else None
    model = None
    settings = None
    process_env = dict(env)
    flag_env: dict[str, str] = {}
    if otel_trace_endpoint:
        # Native Claude Code spans (claude_code.llm_request etc.) exported to
        # the shared loopback receiver. ``env`` is merged on top of the
        # inherited environment by the Python SDK.
        process_env.update(
            claude_otel_env(
                otel_trace_endpoint,
                source_id=otel_source_id,
                resource_attributes=process_env.get(_OTEL_RESOURCE_ATTRIBUTES_ENV),
            ),
        )
        resource_identity = process_env.get(_OTEL_RESOURCE_ATTRIBUTES_ENV)
        if otel_source_id and resource_identity:
            flag_env[_OTEL_RESOURCE_ATTRIBUTES_ENV] = resource_identity
    if external_model_config is not None:
        model = external_model_config.model
        # Inject the external endpoint into the flag-settings layer (the CLI
        # ``--settings`` source) instead of process env. The CLI applies
        # ``~/.claude/settings.json`` (user settings) after the process env,
        # which would shadow any env-var injection; the ``--settings`` source
        # sits above user/project/local settings and wins.
        if external_model_config.api_base:
            flag_env[_ANTHROPIC_BASE_URL_ENV] = external_model_config.api_base
        if external_model_config.api_key:
            flag_env[_ANTHROPIC_AUTH_TOKEN_ENV] = external_model_config.api_key
    if flag_env:
        settings = json.dumps({"env": flag_env})
    return sdk.ClaudeAgentOptions(
        add_dirs=list(add_dirs),
        cli_path=cli_path,
        cwd=cwd,
        env=process_env,
        mcp_servers=None,
        model=model,
        permission_mode="bypassPermissions",
        resume=resume,
        session_id=session_id,
        settings=settings,
        system_prompt={"type": "preset", "append": system_prompt or ""},
    )


def build_claude_session_id(*, team_session_id: str | None, member_name: str) -> str | None:
    """Build a stable Claude UUID from the team session and member identity."""
    if not team_session_id:
        return None
    seed = json.dumps([team_session_id, member_name], ensure_ascii=False, separators=(",", ":"))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


def delete_claude_session(
    *,
    team_session_id: str,
    member_name: str,
    cwd: str | None,
) -> bool:
    """Delete the Claude SDK session derived for a team member."""
    claude_session_id = build_claude_session_id(
        team_session_id=team_session_id,
        member_name=member_name,
    )
    if claude_session_id is None:
        return False
    sdk = load_claude_sdk()
    sdk.delete_session(claude_session_id, directory=cwd)
    return True


def strip_parent_claude_env(environ: dict[str, str]) -> dict[str, str]:
    """Remove parent Claude session markers before launching a child Claude."""
    return {
        key: value
        for key, value in environ.items()
        if not any(key.startswith(prefix) for prefix in _CLAUDE_ENV_STRIP_PREFIXES)
    }


__all__ = [
    "build_claude_options",
    "claude_otel_env",
    "build_claude_session_id",
    "delete_claude_session",
    "load_claude_sdk",
    "strip_parent_claude_env",
]
