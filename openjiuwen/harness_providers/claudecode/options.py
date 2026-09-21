# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Claude Agent SDK loading and option-building helpers."""

from __future__ import annotations

import json
import os
import uuid
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Callable, Mapping

from openjiuwen.harness_protocol import (
    HarnessError,
    McpServerConfig,
    McpTransport,
    ModelOption,
    UnsupportedHarnessCapabilityError,
)
from openjiuwen.harness_providers.claudecode.config import ClaudeCodeHarnessConfig, ClaudeModelConfig
from openjiuwen.harness_providers.jsonsafe import to_json_safe
from openjiuwen.harness_providers.telemetry.otlp_receiver import OTEL_RESOURCE_SOURCE_ID

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeAgentOptions

_CLAUDE_ENV_STRIP_PREFIXES = ("CLAUDECODE", "CLAUDE_CODE_")
_ANTHROPIC_AUTH_TOKEN_ENV = "ANTHROPIC_AUTH_TOKEN"
_ANTHROPIC_BASE_URL_ENV = "ANTHROPIC_BASE_URL"
_CLAUDE_OTEL_EXPORT_INTERVAL_MS = "1000"
_OTEL_RESOURCE_ATTRIBUTES_ENV = "OTEL_RESOURCE_ATTRIBUTES"
# Catalog value of the model the CLI picks when none is configured.
_CLAUDE_DEFAULT_MODEL_VALUE = "default"
# Vendor catalog fields kept on ``ModelOption.extensions``.
_CLAUDE_MODEL_EXTENSION_KEYS = frozenset(
    {"resolvedModel", "supportsEffort", "supportsAdaptiveThinking", "supportsFastMode", "supportsAutoMode"}
)


def load_claude_sdk() -> Any:
    """Import the Claude Agent SDK only when a Claude harness is used."""
    try:
        import claude_agent_sdk
    except ImportError as exc:
        raise HarnessError(
            "claude-agent-sdk is required for the Claude Code harness; install the optional SDK"
        ) from exc
    return claude_agent_sdk


def build_claude_subprocess_transport(options: Any, prompt: Any) -> Any:
    """Build the SDK's own subprocess transport for ``options``.

    The SDK builds this transport itself unless one is supplied; the provider
    builds it here so it can be wrapped before the SDK ever sees it.

    Args:
        options: The SDK options the transport turns into a CLI command.
        prompt: The streaming prompt the transport writes on connect; an empty
            stream leaves the session open for ``query()`` to write into.
    """
    load_claude_sdk()
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    return SubprocessCLITransport(prompt=prompt, options=options)


def build_claude_session_id(*, host_session_id: str | None, agent_name: str) -> str | None:
    """Build a stable Claude UUID from the host session and agent identity."""
    if not host_session_id:
        return None
    seed = json.dumps([host_session_id, agent_name], ensure_ascii=False, separators=(",", ":"))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


def delete_claude_session(*, host_session_id: str, agent_name: str, cwd: str | None) -> bool:
    """Delete the Claude SDK session derived for an agent."""
    claude_session_id = build_claude_session_id(host_session_id=host_session_id, agent_name=agent_name)
    if claude_session_id is None:
        return False
    sdk = load_claude_sdk()
    sdk.delete_session(claude_session_id, directory=cwd)
    return True


def strip_parent_claude_env(environ: Mapping[str, str]) -> dict[str, str]:
    """Remove parent Claude session markers before launching a child Claude."""
    return {
        key: value
        for key, value in environ.items()
        if not any(key.startswith(prefix) for prefix in _CLAUDE_ENV_STRIP_PREFIXES)
    }


def build_process_env(config: ClaudeCodeHarnessConfig, context_env: Mapping[str, str]) -> dict[str, str]:
    """Merge the inherited process env, provider env and per-agent context env."""
    env: dict[str, str] = {}
    if config.inherit_process_env:
        env.update(strip_parent_claude_env(dict(os.environ)))
    env.update(config.env)
    env.update(context_env)
    return env


def model_settings(
    model: ClaudeModelConfig | None,
    settings_env: Mapping[str, str] = MappingProxyType({}),
) -> str | None:
    """Render the CLI ``--settings`` JSON injecting an external endpoint.

    The CLI applies ``~/.claude/settings.json`` after the process env, which
    would shadow any env-var injection; the ``--settings`` source sits above
    user/project/local settings and wins.

    Args:
        model: External endpoint to inject, when one is configured.
        settings_env: Extra env that must win over user settings too.

    Returns:
        The ``--settings`` JSON, or ``None`` when nothing needs injecting.
    """
    flag_env: dict[str, str] = dict(settings_env)
    if model is not None:
        if model.api_base:
            flag_env[_ANTHROPIC_BASE_URL_ENV] = model.api_base
        if model.api_key:
            flag_env[_ANTHROPIC_AUTH_TOKEN_ENV] = model.api_key
    if not flag_env:
        return None
    return json.dumps({"env": flag_env})


def claude_request_log_env(
    *,
    endpoint: str,
    body_dir: str,
    source_id: str,
    resource_attributes: str = "",
) -> dict[str, str]:
    """Build the env making Claude Code log each model request to a receiver.

    Claude Code's raw API body events (``OTEL_LOG_RAW_API_BODIES``) carry the
    full Messages API request and the assembled response of every model call.
    ``file:<dir>`` mode writes the bodies untruncated to ``body_dir`` and puts
    a ``body_ref`` pointer on the event; inline mode truncates at 60 KB, which
    cuts every long conversation. Claude Code's OTLP exporter only speaks gRPC
    — with ``http/protobuf`` it silently connects and never sends data — so the
    protocol is pinned to grpc and ``endpoint`` must be the receiver's gRPC
    listener.

    Args:
        endpoint: gRPC OTLP endpoint of the receiver collecting the events.
        body_dir: Directory the CLI writes request and response bodies into.
        source_id: Identity stamped on the resource so a receiver shared by
            several processes can tell this one apart.
        resource_attributes: ``OTEL_RESOURCE_ATTRIBUTES`` the process already
            carries; kept, with any stale source identity replaced.

    Returns:
        The env vars enabling the export.
    """
    kept = [
        item
        for item in resource_attributes.split(",")
        if item and not item.startswith(f"{OTEL_RESOURCE_SOURCE_ID}=")
    ]
    kept.append(f"{OTEL_RESOURCE_SOURCE_ID}={source_id}")
    return {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        # The enhanced beta adds the per-request ``claude_code.llm_request``
        # span, the only place the CLI states time-to-first-token, attempts
        # and the request id that joins a response body to its request.
        "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA": "1",
        "OTEL_TRACES_EXPORTER": "otlp",
        "OTEL_TRACES_EXPORT_INTERVAL": _CLAUDE_OTEL_EXPORT_INTERVAL_MS,
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc",
        "OTEL_EXPORTER_OTLP_ENDPOINT": endpoint,
        # The CLI fails silently on exporter errors, so a short export interval
        # keeps events flowing while the turn still runs.
        "OTEL_LOGS_EXPORT_INTERVAL": _CLAUDE_OTEL_EXPORT_INTERVAL_MS,
        "OTEL_LOG_RAW_API_BODIES": f"file:{body_dir}",
        _OTEL_RESOURCE_ATTRIBUTES_ENV: ",".join(kept),
        # The gRPC client honors http_proxy/https_proxy and would route the
        # loopback export through the user's proxy, which may not forward
        # 127.0.0.1 traffic. Exempt loopback instead of clearing the proxy
        # vars: business traffic (the model API) may legitimately need them.
        "no_proxy": "127.0.0.1,localhost",
        "NO_PROXY": "127.0.0.1,localhost",
    }


def claude_request_log_settings_env(env: Mapping[str, str]) -> dict[str, str]:
    """Return the part of the request-log env that must also win over user settings.

    The CLI applies user settings after the process env, so the resource
    identity a receiver filters on is injected through ``--settings`` as well.
    """
    value = env.get(_OTEL_RESOURCE_ATTRIBUTES_ENV)
    return {_OTEL_RESOURCE_ATTRIBUTES_ENV: value} if value else {}


def claude_model_options(server_info: Mapping[str, Any] | None) -> tuple[ModelOption, ...]:
    """Map the CLI ``initialize`` model catalog to protocol model options.

    The CLI lists its login's models on the ``initialize`` response (entries
    like ``{"value": "sonnet", "supportedEffortLevels": [...]}``); the entry
    whose ``value`` is ``"default"`` is the model the CLI uses when none is set.

    Args:
        server_info: ``ClaudeSDKClient.get_server_info()`` of a connected client.

    Returns:
        One option per catalog entry, in CLI order.
    """
    raw = server_info.get("models") if server_info else None
    if not isinstance(raw, list):
        return ()
    result: list[ModelOption] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        value = entry.get("value")
        if not isinstance(value, str) or not value:
            continue
        efforts = entry.get("supportedEffortLevels")
        extensions = {key: to_json_safe(item) for key, item in entry.items() if key in _CLAUDE_MODEL_EXTENSION_KEYS}
        result.append(
            ModelOption(
                model_id=value,
                display_name=str(entry.get("displayName") or ""),
                description=str(entry.get("description") or ""),
                efforts=tuple(str(item) for item in efforts if item) if isinstance(efforts, list) else (),
                is_default=value == _CLAUDE_DEFAULT_MODEL_VALUE,
                extensions=extensions,
            )
        )
    return tuple(result)


async def apply_claude_flag_settings(client: Any, settings: Mapping[str, Any]) -> bool:
    """Push flag settings into a live CLI session through ``apply_flag_settings``.

    The SDK wraps ``set_model`` but not effort; the CLI control protocol's
    ``apply_flag_settings`` request hot-applies ``effortLevel`` without a
    restart. The SDK exposes no public method for it, so the private query
    seam stays isolated here.

    Args:
        client: A connected ``ClaudeSDKClient``.
        settings: Flag settings to apply, e.g. ``{"effortLevel": "low"}``.

    Returns:
        False when this SDK build does not expose the control channel; the
        caller then falls back to reconnecting with the new options.
    """
    query = getattr(client, "_query", None)
    send = getattr(query, "_send_control_request", None)
    if not callable(send):
        return False
    await send({"subtype": "apply_flag_settings", "settings": dict(settings)})
    return True


def mcp_servers_to_sdk(servers: tuple[McpServerConfig, ...]) -> dict[str, Any]:
    """Translate protocol MCP server configs into Claude SDK server configs."""
    result: dict[str, Any] = {}
    for server in servers:
        if server.transport is McpTransport.STDIO:
            binary, *args = server.command
            entry: dict[str, Any] = {"type": "stdio", "command": binary, "args": list(args)}
            if server.env:
                entry["env"] = dict(server.env)
            result[server.name] = entry
        elif server.transport is McpTransport.HTTP:
            entry = {"type": "http", "url": server.url}
            if server.headers:
                entry["headers"] = dict(server.headers)
            result[server.name] = entry
        elif server.transport is McpTransport.IN_PROCESS:
            result[server.name] = server.instance
        else:  # pragma: no cover - enum is closed
            raise UnsupportedHarnessCapabilityError(f"unsupported MCP transport {server.transport}")
    return result


def build_claude_options(
    *,
    sdk: Any,
    config: ClaudeCodeHarnessConfig,
    model: ClaudeModelConfig | None,
    cwd: str | None,
    env: Mapping[str, str],
    system_prompt: str,
    session_id: str | None,
    resume: str | None,
    mcp_servers: dict[str, Any],
    can_use_tool: Callable[..., Any] | None,
    stderr: Callable[[str], None] | None,
    settings_env: Mapping[str, str] = MappingProxyType({}),
) -> "ClaudeAgentOptions":
    """Build ``ClaudeAgentOptions`` for one harness session.

    ``settings_env`` is merged over ``config.settings_env`` into the
    ``--settings`` env, for env the harness itself must pin above user settings.
    """
    if config.system_prompt_mode == "replace" and system_prompt:
        prompt_option: Any = system_prompt
    else:
        prompt_option = {"type": "preset", "append": system_prompt or ""}
    settings = config.settings
    endpoint_settings = model_settings(model, {**config.settings_env, **settings_env})
    if endpoint_settings is not None:
        settings = endpoint_settings
    return sdk.ClaudeAgentOptions(
        skills="all" if config.skills else None,
        setting_sources=["user", "project", "local"] if config.skills else None,
        add_dirs=list(config.add_dirs),
        cli_path=config.cli_path,
        cwd=cwd,
        env=dict(env),
        mcp_servers=mcp_servers,
        model=model.model if model is not None else None,
        effort=model.effort if model is not None else None,
        permission_mode=config.permission_mode,
        resume=resume,
        session_id=session_id,
        settings=settings,
        system_prompt=prompt_option,
        include_partial_messages=config.include_partial_messages,
        max_turns=config.max_turns,
        max_buffer_size=config.max_buffer_size,
        can_use_tool=can_use_tool,
        stderr=stderr,
    )


__all__ = [
    "apply_claude_flag_settings",
    "claude_model_options",
    "claude_request_log_env",
    "claude_request_log_settings_env",
    "build_claude_options",
    "build_claude_session_id",
    "build_process_env",
    "delete_claude_session",
    "load_claude_sdk",
    "mcp_servers_to_sdk",
    "model_settings",
    "strip_parent_claude_env",
]
