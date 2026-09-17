# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Create protocol harnesses from an AgentTemplate manifest.

The manifest (``manifest.json`` with ``package_type=agent_template`` or an
in-memory ``AgentTemplateSpec``) is the harness-expert authored description
of one agent: identity, model, persona prompt sections, MCP servers and the
portable skills and DeepAgent-only extension points (tools, rails, sub-agents).  The
factory maps it onto one of the built-in providers selected by name.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Mapping

from openjiuwen.harness.resources.extension_loader import find_agent_template_manifest, load_agent_template_package
from openjiuwen.harness.resources.extension_resolver import render_agent_template_system_prompt
from openjiuwen.harness.schema.extension_spec import AgentTemplateSpec, McpServerSpec
from openjiuwen.harness_protocol import (
    HarnessCheckpoint,
    HarnessCheckpointSink,
    HarnessContext,
    HarnessInteractionHandler,
    HarnessProtocol,
    HarnessProvider,
    HostCapability,
    McpServerConfig,
    McpTransport,
    ResumePolicy,
)

HarnessProviderName = Literal["native", "native_v2", "claudecode", "codex", "dsh"]
PROVIDER_NAMES: tuple[HarnessProviderName, ...] = ("native", "native_v2", "claudecode", "codex", "dsh")
# Manifest sections only the in-process DeepAgent can materialize.
_DEEP_AGENT_ONLY_SECTIONS = ("tools", "rails", "subagents")


def resolve_provider(provider: str) -> HarnessProvider:
    """Return the provider SPI implementation registered under ``provider``."""

    if provider == "native":
        from openjiuwen.harness_providers.native import NativeHarnessProvider

        return NativeHarnessProvider()
    if provider == "native_v2":
        from openjiuwen.agent_teams.harness.protocol_adapter import NativeV2HarnessProvider

        return NativeV2HarnessProvider()
    if provider == "claudecode":
        from openjiuwen.harness_providers.claudecode import ClaudeCodeHarnessProvider

        return ClaudeCodeHarnessProvider()
    if provider == "codex":
        from openjiuwen.harness_providers.codex import CodexHarnessProvider

        return CodexHarnessProvider()
    if provider == "dsh":
        from openjiuwen.harness_providers.dsh import DshHarnessProvider

        return DshHarnessProvider()
    raise ValueError(f"unknown harness provider {provider!r}; expected one of {', '.join(PROVIDER_NAMES)}")


def load_manifest(manifest: AgentTemplateSpec | str | Path) -> AgentTemplateSpec:
    """Return the manifest as an ``AgentTemplateSpec``.

    A path may point at a package directory or its ``manifest.json``.
    """

    if isinstance(manifest, AgentTemplateSpec):
        return manifest
    return load_agent_template_package(find_agent_template_manifest(manifest))


def manifest_mcp_servers(manifest: AgentTemplateSpec) -> tuple[McpServerConfig, ...]:
    """Translate manifest MCP declarations into protocol MCP server configs."""

    return tuple(_mcp_server_config(spec) for spec in manifest.mcps)


def _mcp_server_config(spec: McpServerSpec) -> McpServerConfig:
    name = spec.server_name or spec.server_id
    if not name:
        raise ValueError("manifest MCP server requires server_name")
    if spec.type == "stdio":
        if not spec.command:
            raise ValueError(f"manifest stdio MCP server {name!r} requires command")
        return McpServerConfig(
            name=name,
            transport=McpTransport.STDIO,
            command=(spec.command, *spec.args),
            env=dict(spec.env),
        )
    if not spec.url:
        raise ValueError(f"manifest {spec.type} MCP server {name!r} requires url")
    return McpServerConfig(
        name=name,
        transport=McpTransport.HTTP,
        url=spec.url,
        headers=dict(spec.auth_headers),
    )


def manifest_provider_config(
    manifest: AgentTemplateSpec,
    *,
    provider: HarnessProviderName,
    config: Mapping[str, Any] | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """Derive the provider SPI configuration for ``manifest``.

    Explicit ``config`` keys always win; the manifest fills the model
    endpoint and portable skills (for native providers, the whole template).
    """

    values: dict[str, Any] = dict(config or {})
    if provider in {"native", "native_v2"}:
        values.setdefault("agent_template", manifest.model_dump(mode="json"))
        if language is not None:
            values.setdefault("language", language)
        return values
    for section in _DEEP_AGENT_ONLY_SECTIONS:
        if getattr(manifest, section):
            raise ValueError(
                f"manifest section {section!r} depends on the DeepAgent framework; "
                f"provider {provider!r} cannot materialize it"
            )
    if manifest.skills:
        values.setdefault("skills", [skill.model_dump(mode="json") for skill in manifest.skills])
    model = manifest.model
    if model is None:
        return values
    client = model.model_client_config
    request = model.model_request_config
    model_name = request.model_name if request is not None and request.model_name else None
    api_base = client.api_base or None
    api_key = client.api_key or None
    if provider == "claudecode":
        values.setdefault("model", {"model": model_name, "api_base": api_base, "api_key": api_key})
    elif provider == "codex":
        raw_provider = client.client_provider
        provider_name = str(getattr(raw_provider, "value", raw_provider)) if raw_provider else None
        values.setdefault(
            "model",
            {"model": model_name, "provider": provider_name, "api_base": api_base, "api_key": api_key},
        )
    elif provider == "dsh":
        if model_name is not None:
            values.setdefault("model", model_name)
        if api_base is not None:
            values.setdefault("base_url", api_base)
        if api_key is not None:
            values.setdefault("api_key", api_key)
    return values


def create_harness(
    manifest: AgentTemplateSpec | str | Path,
    *,
    provider: HarnessProviderName,
    config: Mapping[str, Any] | None = None,
    language: str | None = None,
) -> HarnessProtocol:
    """Create an unstarted protocol harness for ``manifest`` on ``provider``.

    Args:
        manifest: An ``AgentTemplateSpec`` or a path to its package.
        provider: One of ``native`` / ``native_v2`` / ``claudecode`` / ``codex`` / ``dsh``.
        config: Provider-specific overrides merged over the manifest-derived
            configuration (see :func:`manifest_provider_config`).
        language: Language used to render manifest prompt sections.

    Raises:
        ValueError: When the provider is unknown, or when a third-party
            provider receives a manifest carrying DeepAgent-only sections.
    """

    if provider not in PROVIDER_NAMES:
        raise ValueError(f"unknown harness provider {provider!r}; expected one of {', '.join(PROVIDER_NAMES)}")
    template = load_manifest(manifest)
    provider_impl = resolve_provider(provider)
    provider_config = manifest_provider_config(template, provider=provider, config=config, language=language)
    return provider_impl.create(provider_config)


def build_harness_context(
    manifest: AgentTemplateSpec | str | Path,
    *,
    provider: HarnessProviderName,
    host_session_id: str,
    agent_id: str | None = None,
    agent_name: str | None = None,
    language: str = "cn",
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    extra_system_prompt: str | None = None,
    host_capabilities: frozenset[HostCapability] = frozenset(),
    resume_policy: ResumePolicy = ResumePolicy.NEW,
    checkpoint: HarnessCheckpoint | None = None,
    checkpoint_sink: HarnessCheckpointSink | None = None,
    interactions: HarnessInteractionHandler | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> HarnessContext:
    """Build the start context carrying the manifest's prompt and MCP servers.

    For ``native`` / ``native_v2`` the template itself is loaded by the harness, so only
    ``extra_system_prompt`` reaches the context; for third-party providers
    the persona prompt sections are rendered into ``system_prompt`` and the
    manifest MCP servers become ``mcp_servers``.
    """

    template = load_manifest(manifest)
    card = template.agent_card
    prompt_parts: list[str] = []
    mcp_servers: tuple[McpServerConfig, ...] = ()
    if provider not in {"native", "native_v2"}:
        rendered = render_agent_template_system_prompt(template, language=language)
        if rendered:
            prompt_parts.append(rendered)
        mcp_servers = manifest_mcp_servers(template)
    if extra_system_prompt:
        prompt_parts.append(extra_system_prompt)
    capabilities = set(host_capabilities)
    if checkpoint_sink is not None:
        capabilities.add(HostCapability.CHECKPOINT_SINK)
    if mcp_servers:
        capabilities.add(HostCapability.MCP_SERVERS)
    return HarnessContext(
        agent_name=agent_name or card.name,
        agent_id=agent_id or card.id or card.name,
        host_session_id=host_session_id,
        system_prompt="\n\n".join(prompt_parts),
        host_capabilities=frozenset(capabilities),
        resume_policy=resume_policy,
        cwd=cwd,
        env=dict(env or {}),
        checkpoint=checkpoint,
        checkpoint_sink=checkpoint_sink,
        mcp_servers=mcp_servers,
        interactions=interactions,
        metadata=dict(metadata or {}),
    )


__all__ = [
    "HarnessProviderName",
    "PROVIDER_NAMES",
    "build_harness_context",
    "create_harness",
    "load_manifest",
    "manifest_mcp_servers",
    "manifest_provider_config",
    "resolve_provider",
]
