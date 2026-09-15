# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Codex Python SDK loading and option-building helpers."""

from __future__ import annotations

import inspect
import json
import os
import re
from typing import Any, Mapping

from openjiuwen.core.common.logging import LazyLogger, LogManager
from openjiuwen.harness_protocol import HarnessError, McpServerConfig, McpTransport, UnsupportedHarnessCapabilityError
from openjiuwen.harness_providers.codex.config import CodexHarnessConfig, CodexModelConfig

logger = LazyLogger(lambda: LogManager.get_logger("harness_providers"))

CODEX_API_KEY_ENV = "OPENJIUWEN_CODEX_API_KEY"
# Codex feature flag exposing the experimental ``request_user_input`` tool in
# default mode; without it the model only has the tool in collaboration modes.
USER_INPUT_FEATURE_OVERRIDE = "features.default_mode_request_user_input=true"
# TOML bare-key pattern (A-Za-z0-9_-). Codex parses each --config value as
# TOML, and a dotted-path segment that is a bare key names a different table
# from the same string wrapped in quotes, so provider table keys must stay
# bare whenever the provider name allows it.
_BARE_KEY_RE = re.compile(r"[A-Za-z0-9_-]+")


def load_codex_sdk() -> Any:
    """Import the Codex SDK only when a Codex harness needs it."""
    try:
        import openai_codex
    except ImportError as exc:
        raise HarnessError("openai-codex is required for the Codex harness; install the optional SDK") from exc
    return openai_codex


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


def _toml_inline_table(values: Mapping[str, str]) -> str:
    """Render a string-to-string mapping as a TOML inline table."""
    items = ", ".join(f"{_dotted_table_key(key)} = {json.dumps(value)}" for key, value in values.items())
    return "{ " + items + " }"


def codex_model_config_overrides(model: CodexModelConfig) -> tuple[str, ...]:
    """Render Codex provider config overrides from the external model config."""
    if not model.provider:
        return ()
    provider = model.provider
    overrides = [f"model_provider={json.dumps(provider)}"]
    provider_key = _dotted_table_key(provider)
    overrides.append(f"model_providers.{provider_key}.name={json.dumps(provider)}")
    if model.api_base:
        overrides.append(f"model_providers.{provider_key}.base_url={json.dumps(model.api_base)}")
    if model.api_key:
        overrides.append(f"model_providers.{provider_key}.env_key={json.dumps(CODEX_API_KEY_ENV)}")
    # An external model targets a non-OpenAI endpoint, but codex's request
    # compression decision only checks the provider name and the ambient
    # ChatGPT login in ~/.codex/auth.json - not the effective base_url. With a
    # ChatGPT login present and a provider named "OpenAI", codex would
    # zstd-compress request bodies that the external endpoint cannot decode (it
    # reports "Failed to parse the request body as JSON"). Disable compression
    # for external endpoints only; members on the official endpoint keep it.
    overrides.append("features.enable_request_compression=false")
    return tuple(overrides)


def codex_mcp_config_overrides(
    server: McpServerConfig,
    *,
    env_passthrough: tuple[str, ...],
    startup_timeout_s: int,
    required: bool,
    default_tools_approval_mode: str | None,
) -> tuple[str, ...]:
    """Render ``mcp_servers.*`` entries for one protocol MCP server."""
    key = _dotted_table_key(server.name.replace("-", "_"))
    overrides: list[str] = []
    if server.transport is McpTransport.STDIO:
        binary, *args = server.command
        overrides.append(f"mcp_servers.{key}.command={json.dumps(binary)}")
        if args:
            overrides.append(f"mcp_servers.{key}.args={json.dumps(args)}")
        if server.env:
            overrides.append(f"mcp_servers.{key}.env={_toml_inline_table(server.env)}")
        if env_passthrough:
            overrides.append(f"mcp_servers.{key}.env_vars={json.dumps(list(env_passthrough))}")
    elif server.transport is McpTransport.HTTP:
        overrides.append(f"mcp_servers.{key}.url={json.dumps(server.url)}")
        if server.headers:
            overrides.append(f"mcp_servers.{key}.http_headers={_toml_inline_table(server.headers)}")
    else:
        raise UnsupportedHarnessCapabilityError("the Codex SDK cannot mount in-process MCP server instances")
    overrides.append(f"mcp_servers.{key}.startup_timeout_sec={startup_timeout_s}")
    overrides.append(f"mcp_servers.{key}.required={'true' if required else 'false'}")
    if default_tools_approval_mode is not None:
        overrides.append(f"mcp_servers.{key}.default_tools_approval_mode={json.dumps(default_tools_approval_mode)}")
    return tuple(overrides)


def build_process_env(config: CodexHarnessConfig, context_env: Mapping[str, str]) -> dict[str, str]:
    """Merge the inherited process env, provider env and per-agent context env."""
    env: dict[str, str] = {}
    if config.inherit_process_env:
        env.update(os.environ)
    env.update(config.env)
    env.update(context_env)
    return env


def build_codex_config(
    *,
    sdk: Any,
    config: CodexHarnessConfig,
    model: CodexModelConfig | None,
    cwd: str | None,
    env: Mapping[str, str],
    mcp_servers: tuple[McpServerConfig, ...],
    enable_user_input: bool = False,
) -> Any:
    """Build ``CodexConfig`` for one harness session.

    Args:
        enable_user_input: Give the model Codex's experimental
            ``request_user_input`` tool; it is off in the CLI's default mode,
            so a host that declares ``USER_INPUT`` must switch it on here.
    """
    process_env = dict(env)
    overrides: tuple[str, ...] = ()
    if enable_user_input:
        overrides += (USER_INPUT_FEATURE_OVERRIDE,)
    if model is not None:
        overrides += codex_model_config_overrides(model)
        if model.api_key:
            process_env[CODEX_API_KEY_ENV] = model.api_key
    for server in mcp_servers:
        overrides += codex_mcp_config_overrides(
            server,
            env_passthrough=config.mcp_env_passthrough,
            startup_timeout_s=config.mcp_startup_timeout_s,
            required=config.mcp_required,
            default_tools_approval_mode=config.mcp_default_tools_approval_mode,
        )
    overrides += config.config_overrides
    return sdk.CodexConfig(
        codex_bin=config.codex_bin,
        config_overrides=overrides,
        cwd=cwd,
        env=process_env,
        client_name=config.client_name,
        client_title=config.client_title,
        client_version="1",
    )


def build_thread_options(
    *,
    sdk: Any,
    config: CodexHarnessConfig,
    model: CodexModelConfig | None,
    cwd: str | None,
    system_prompt: str,
) -> dict[str, Any]:
    """Build thread start/resume options, including the reasoning summary."""
    options: dict[str, Any] = {"ephemeral": False, "config": dict(config.thread_config)}
    if cwd:
        options["cwd"] = cwd
    if system_prompt:
        options["developer_instructions"] = system_prompt
    if model is not None:
        if model.model:
            options["model"] = model.model
        if model.provider:
            options["model_provider"] = model.provider
    # An external model targets a non-OpenAI endpoint. Codex's auto-review
    # approval reviewer uses a built-in ``codex-auto-review`` model that cannot
    # be redirected to an external provider, so any auto-review call against an
    # external endpoint is guaranteed to fail. Bypass the reviewer whenever an
    # external model is configured: ``deny_all`` never asks for approval and
    # ``full_access`` lets tool calls run under the host's own policy.
    bypass = config.bypass_approvals_and_sandbox or model is not None
    if bypass:
        options["approval_mode"] = sdk.ApprovalMode.deny_all
        options["sandbox"] = sdk.Sandbox.full_access
    return options


async def append_developer_instructions(client: Any, sdk: Any, config: CodexHarnessConfig,
                                        *, cwd: str | None, system_prompt: str) -> str:
    """Read effective app-server configuration before appending host instructions.

    Read on every connection, including resume/fallback; never append to our
    own previously composed thread value. Failure is fatal, not a silent replace.
    """
    await client._ensure_initialized()
    result = await client._client.request(
        "config/read", {"cwd": cwd, "includeLayers": False},
        response_model=sdk.generated.v2_all.ConfigReadResponse,
    )
    existing = config.thread_config.get("developer_instructions", result.config.developer_instructions)
    if existing is not None and not isinstance(existing, str):
        raise ValueError("developer_instructions must be a string")
    return "\n\n".join(part for part in (existing, system_prompt) if part)


async def start_thread_with_raw_events(*, client: Any, sdk: Any, options: dict[str, Any]) -> Any:
    """Start a thread with App Server model-response notifications enabled.

    Newer SDKs may expose ``experimental_raw_events`` directly. The currently
    supported SDK can still send the App Server field through its low-level
    JSON-RPC client, so keep that compatibility code isolated here.
    """
    thread_start = client.thread_start
    signature = inspect.signature(thread_start)
    parameters = signature.parameters.values()
    accepts_kwargs = any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters)
    if "experimental_raw_events" in signature.parameters or accepts_kwargs:
        return await thread_start(experimental_raw_events=True, **options)

    ensure_initialized = getattr(client, "_ensure_initialized", None)
    low_level_client = getattr(client, "_client", None)
    async_thread_type = getattr(sdk, "AsyncThread", None)
    if not callable(ensure_initialized) or low_level_client is None or async_thread_type is None:
        logger.warning("[codex] SDK does not expose experimental raw events; falling back to thread_start")
        return await thread_start(**options)

    try:
        from openai_codex._approval_mode import _approval_mode_settings
        from openai_codex._sandbox import _sandbox_mode
        from openai_codex.generated.v2_all import ThreadStartParams

        wire_options = dict(options)
        approval_mode = wire_options.pop("approval_mode", sdk.ApprovalMode.auto_review)
        sandbox = wire_options.pop("sandbox", None)
        approval_policy, approvals_reviewer = _approval_mode_settings(approval_mode)
        params = ThreadStartParams(
            approval_policy=approval_policy,
            approvals_reviewer=approvals_reviewer,
            sandbox=_sandbox_mode(sandbox),
            **wire_options,
        )
        request = params.model_dump(by_alias=True, exclude_none=True, mode="json")
        request["experimentalRawEvents"] = True
        await ensure_initialized()
        started = await low_level_client.thread_start(request)
        return async_thread_type(client, started.thread.id)
    except (ImportError, AttributeError, TypeError, ValueError) as exc:
        logger.warning("[codex] raw-event compatibility path is unavailable (%s); using thread_start", exc)
        return await thread_start(**options)


__all__ = [
    "CODEX_API_KEY_ENV",
    "USER_INPUT_FEATURE_OVERRIDE",
    "build_codex_config",
    "build_process_env",
    "build_thread_options",
    "codex_mcp_config_overrides",
    "codex_model_config_overrides",
    "load_codex_sdk",
    "start_thread_with_raw_events",
]
