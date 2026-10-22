# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Create protocol harnesses from an AgentTemplate manifest.

The manifest (``manifest.json`` with ``package_type=agent_template`` or an
in-memory ``AgentTemplateSpec``) is the harness-expert authored description
of one agent: identity, model, persona prompt sections, MCP servers and the
portable skills and DeepAgent-only extension points (tools, rails, sub-agents).  The
factory maps it onto a provider selected by name.

Provider names resolve in this order:

1. built-in providers (:data:`PROVIDER_NAMES`), a closed set that always wins;
2. providers registered in-process with :func:`register_harness_provider`;
3. installed packages declaring an entry point in the
   :data:`HARNESS_PROVIDER_ENTRY_POINT_GROUP` group, whose value is a
   ``HarnessProvider`` class (instantiated without arguments) or instance.

An installed provider replaced on disk (same distribution, different
``RECORD``) is imported afresh by the next resolution: harnesses already
created keep running the code they were built from, new ones get the new code.

Every provider that runs a model implements ``ModelConfigProvider.model_config``
to map a host's ``ModelChoice`` (a built-in model, or one on a host endpoint
when the card declares ``ENDPOINT_MODELS``) onto its own configuration; the
manifest factory and the team spawn path both go through
:func:`provider_model_config`.
"""

from __future__ import annotations

import hashlib
import importlib
import sys
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Callable, Literal, Mapping

from openjiuwen.core.common.logging import LazyLogger, LogManager
from openjiuwen.harness.resources.extension_loader import find_agent_template_manifest, load_agent_template_package
from openjiuwen.harness.resources.extension_resolver import render_agent_template_system_prompt
from openjiuwen.harness.schema.extension_spec import AgentTemplateSpec, McpServerSpec
from openjiuwen.harness_protocol import (
    HarnessCapability,
    HarnessCheckpoint,
    HarnessCheckpointSink,
    HarnessContext,
    HarnessInteractionHandler,
    HarnessProtocol,
    HarnessProvider,
    HostCapability,
    McpServerConfig,
    McpTransport,
    ModelChoice,
    ModelConfigProvider,
    ModelEndpoint,
    ResumePolicy,
)

HarnessProviderName = Literal["native", "native_v2", "claudecode", "codex", "dsh"]
PROVIDER_NAMES: tuple[HarnessProviderName, ...] = ("native", "native_v2", "claudecode", "codex", "dsh")
# STABLE PUBLIC API: published provider packages declare this entry-point
# group in their pyproject.toml. Changing it breaks every external provider.
HARNESS_PROVIDER_ENTRY_POINT_GROUP = "openjiuwen.harness_providers"
# Manifest sections only the in-process DeepAgent can materialize.
_DEEP_AGENT_ONLY_SECTIONS = ("tools", "rails", "subagents")
_NATIVE_PROVIDERS = frozenset({"native", "native_v2"})
# In-process registrations (register_harness_provider): name -> provider factory.
_REGISTERED_PROVIDERS: dict[str, Callable[[], HarnessProvider]] = {}
# Installed provider distributions already imported: distribution key -> RECORD digest.
_IMPORTED_DISTRIBUTIONS: dict[str, str] = {}

logger = LazyLogger(lambda: LogManager.get_logger("harness_providers"))

ProviderFactory = Callable[[], HarnessProvider]


def register_harness_provider(name: str, factory: ProviderFactory) -> None:
    """Register a third-party provider in-process.

    For providers that are not shipped as an installed package (tests,
    private code). Packages declare an entry point instead.

    Args:
        name: The provider name used with :func:`create_harness`.
        factory: Zero-argument callable returning the ``HarnessProvider``,
            typically the provider class itself.

    Raises:
        ValueError: When ``name`` is empty or names a built-in provider.
    """
    if not name or name in PROVIDER_NAMES:
        raise ValueError(f"cannot register harness provider {name!r}: empty or built-in name")
    _REGISTERED_PROVIDERS[name] = factory


def unregister_harness_provider(name: str) -> None:
    """Remove an in-process registration; unknown names are ignored."""
    _REGISTERED_PROVIDERS.pop(name, None)


def available_providers() -> tuple[str, ...]:
    """Return every resolvable provider name: built-in, registered, then installed."""
    names = [*PROVIDER_NAMES]
    for name in (*_REGISTERED_PROVIDERS, *(entry.name for entry in _provider_entry_points())):
        if name not in names:
            names.append(name)
    return tuple(names)


def resolve_provider(provider: str) -> HarnessProvider:
    """Return the provider SPI implementation registered under ``provider``.

    Raises:
        ValueError: When no built-in, registered or installed provider has
            that name, or an installed provider cannot be loaded.
    """

    if provider in PROVIDER_NAMES:
        return _resolve_builtin(provider)
    factory = _REGISTERED_PROVIDERS.get(provider)
    if factory is not None:
        return _as_provider(provider, factory)
    for entry in _provider_entry_points():
        if entry.name != provider:
            continue
        _forget_replaced_distribution(entry)
        try:
            loaded = entry.load()
        except Exception as exc:
            raise ValueError(f"harness provider {provider!r} is installed but cannot be loaded: {exc}") from exc
        return _as_provider(provider, loaded)
    raise ValueError(f"unknown harness provider {provider!r}; expected one of {', '.join(available_providers())}")


def _provider_entry_points() -> tuple[Any, ...]:
    return tuple(entry_points(group=HARNESS_PROVIDER_ENTRY_POINT_GROUP))


def _forget_replaced_distribution(entry: Any) -> None:
    """Drop an installed provider's modules when its files changed since they were imported.

    A host replaces a provider package in place (pip, or extracting a new
    wheel over the old directory) while its process keeps running. Python
    would keep serving the modules imported from the old files, so a changed
    ``RECORD`` (it lists every file with its hash) removes the distribution's
    top-level packages from ``sys.modules`` and the entry point imports them
    again. Objects built from the old modules keep them alive and keep working.
    """
    dist = getattr(entry, "dist", None)
    if dist is None:
        return
    record = dist.read_text("RECORD")
    if record is None:
        return
    key = f"{dist.locate_file('')}::{dist.metadata['Name']}"
    digest = hashlib.sha256(record.encode()).hexdigest()
    previous = _IMPORTED_DISTRIBUTIONS.get(key)
    _IMPORTED_DISTRIBUTIONS[key] = digest
    if previous is None or previous == digest:
        return
    packages = _top_level_packages(dist)
    stale = [name for name in sys.modules if name.split(".", 1)[0] in packages]
    for name in stale:
        sys.modules.pop(name, None)
    importlib.invalidate_caches()
    logger.info("harness provider %r was replaced on disk; reimporting %s", entry.name, ", ".join(sorted(packages)))


def _top_level_packages(dist: Any) -> frozenset[str]:
    """Return the importable top-level names a distribution installs."""
    listed = dist.read_text("top_level.txt")
    if listed:
        return frozenset(line.strip() for line in listed.splitlines() if line.strip())
    names: set[str] = set()
    for file in dist.files or ():
        top = file.parts[0]
        if top.endswith((".dist-info", ".data")) or top == "..":
            continue
        name = top.split(".", 1)[0] if len(file.parts) == 1 else top
        if name.isidentifier():
            names.add(name)
    return frozenset(names)


def _as_provider(name: str, loaded: Any) -> HarnessProvider:
    """Instantiate a provider class (or call a factory) and check the SPI."""
    provider = loaded
    if isinstance(loaded, type) or not isinstance(loaded, HarnessProvider):
        if not callable(loaded):
            raise ValueError(f"harness provider {name!r} is neither a provider nor a provider factory")
        try:
            provider = loaded()
        except Exception as exc:
            raise ValueError(f"harness provider {name!r} could not be instantiated: {exc}") from exc
    if not isinstance(provider, HarnessProvider):
        raise ValueError(f"harness provider {name!r} does not implement HarnessProvider (card + create)")
    return provider


def _resolve_builtin(provider: str) -> HarnessProvider:
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
            command=_stdio_command(spec),
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


def _stdio_command(spec: McpServerSpec) -> tuple[str, ...]:
    """Resolve package-relative stdio script args against the MCP directory."""
    cwd = Path(spec.cwd).expanduser() if spec.cwd else None
    args: list[str] = []
    for arg in spec.args:
        candidate = Path(arg)
        if cwd is not None and not candidate.is_absolute():
            located = cwd / candidate
            if located.is_file():
                args.append(str(located.resolve()))
                continue
        args.append(arg)
    return (spec.command, *args)


def manifest_provider_config(
    manifest: AgentTemplateSpec,
    *,
    provider: str,
    config: Mapping[str, Any] | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """Derive the provider SPI configuration for ``manifest``.

    Explicit ``config`` keys always win; the manifest fills the model
    model and portable skills (for native providers, the whole template).
    The model reaches every other provider through its ``model_config`` hook
    (see :func:`provider_model_config`).
    """

    values: dict[str, Any] = dict(config or {})
    if provider in _NATIVE_PROVIDERS:
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
    choice = _manifest_model_choice(manifest)
    if choice is None:
        return values
    for key, value in provider_model_config(provider, choice).items():
        values.setdefault(key, value)
    return values


def _manifest_model_choice(manifest: AgentTemplateSpec) -> ModelChoice | None:
    """Return the manifest's model, served by its endpoint when it names an API base."""
    model = manifest.model
    if model is None:
        return None
    client = model.model_client_config
    request = model.model_request_config
    model_name = request.model_name if request is not None and request.model_name else None
    if not client.api_base:
        return ModelChoice(model=model_name)
    raw_provider = client.client_provider
    endpoint = ModelEndpoint(
        api_base=client.api_base,
        api_key=client.api_key or None,
        provider=str(getattr(raw_provider, "value", raw_provider)) if raw_provider else None,
    )
    return ModelChoice(model=model_name, endpoint=endpoint)


def provider_model_config(
    provider: str,
    choice: ModelChoice,
    *,
    fallback: ModelChoice | None = None,
) -> dict[str, Any]:
    """Map a model choice onto a provider's configuration through its ``model_config``.

    Args:
        provider: Any resolvable provider name.
        choice: The model to run, built-in or on a host endpoint.
        fallback: Optional model the provider may switch to on its own.

    Returns:
        The configuration fragment to merge into the provider's ``create`` mapping.

    Raises:
        ValueError: When the provider configures no models, takes no endpoint
            models while ``choice`` names an endpoint, has no authentication
            fallback while ``fallback`` is given, or rejects the choice.
    """
    resolved = resolve_provider(provider)
    if not isinstance(resolved, ModelConfigProvider):
        raise ValueError(f"harness provider {provider!r} does not configure models")
    if fallback is not None and not resolved.card.supports(HarnessCapability.AUTH_FALLBACK):
        raise ValueError(f"harness provider {provider!r} has no authentication fallback")
    uses_endpoint = choice.endpoint is not None or (fallback is not None and fallback.endpoint is not None)
    if uses_endpoint and not resolved.card.supports(HarnessCapability.ENDPOINT_MODELS):
        raise ValueError(f"harness provider {provider!r} does not take endpoint models")
    return dict(resolved.model_config(choice, fallback=fallback))


def create_harness(
    manifest: AgentTemplateSpec | str | Path,
    *,
    provider: str,
    config: Mapping[str, Any] | None = None,
    language: str | None = None,
) -> HarnessProtocol:
    """Create an unstarted protocol harness for ``manifest`` on ``provider``.

    Args:
        manifest: An ``AgentTemplateSpec`` or a path to its package.
        provider: A built-in name (``native`` / ``native_v2`` / ``claudecode`` /
            ``codex`` / ``dsh``), a name registered with
            :func:`register_harness_provider`, or the entry-point name of an
            installed provider package (see :func:`available_providers`).
        config: Provider-specific overrides merged over the manifest-derived
            configuration (see :func:`manifest_provider_config`).
        language: Language used to render manifest prompt sections.

    Raises:
        ValueError: When the provider is unknown, or when a third-party
            provider receives a manifest carrying DeepAgent-only sections.
    """

    provider_impl = resolve_provider(provider)
    template = load_manifest(manifest)
    provider_config = manifest_provider_config(template, provider=provider, config=config, language=language)
    return provider_impl.create(provider_config)


def build_harness_context(
    manifest: AgentTemplateSpec | str | Path,
    *,
    provider: str,
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
    if provider not in _NATIVE_PROVIDERS:
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
    "HARNESS_PROVIDER_ENTRY_POINT_GROUP",
    "HarnessProviderName",
    "PROVIDER_NAMES",
    "ProviderFactory",
    "available_providers",
    "build_harness_context",
    "create_harness",
    "load_manifest",
    "manifest_mcp_servers",
    "manifest_provider_config",
    "provider_model_config",
    "register_harness_provider",
    "resolve_provider",
    "unregister_harness_provider",
]
