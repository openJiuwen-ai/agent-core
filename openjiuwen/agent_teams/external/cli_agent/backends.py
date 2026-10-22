# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Registry of external CLI backend kinds."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from typing import Literal

from openjiuwen.agent_teams.external.cli_agent.adapters import available_adapters, build_adapter

BackendKind = Literal["sdk", "adapter", "provider"]


@dataclass(frozen=True, slots=True)
class SdkRequirement:
    """Optional Python SDK a backend imports when its member starts."""

    module: str
    """Top-level import name probed before the member is registered."""

    distribution: str
    """Package name to install when ``module`` is not importable."""

    extra: str
    """``openjiuwen`` optional-dependency extra that pulls the package in."""


@dataclass(frozen=True, slots=True)
class ExternalCliBackend:
    """Static metadata for an external CLI backend kind."""

    name: str
    kind: BackendKind
    supports_command_override: bool
    injects_system_prompt_via_arg: bool
    sdk_requirement: SdkRequirement | None = None
    """SDK the backend needs at spawn time; ``None`` for subprocess adapters."""
    provider: str | None = None
    """Harness provider driving the member; ``None`` for subprocess adapters."""


_SDK_BACKENDS: dict[str, ExternalCliBackend] = {
    "claude": ExternalCliBackend(
        name="claude",
        kind="sdk",
        supports_command_override=False,
        injects_system_prompt_via_arg=True,
        sdk_requirement=SdkRequirement(
            module="claude_agent_sdk",
            distribution="claude-agent-sdk",
            extra="claude",
        ),
        provider="claudecode",
    ),
    "codex": ExternalCliBackend(
        name="codex",
        kind="sdk",
        supports_command_override=False,
        injects_system_prompt_via_arg=True,
        sdk_requirement=SdkRequirement(
            module="openai_codex",
            distribution="openai-codex",
            extra="codex",
        ),
        provider="codex",
    ),
}


def backend_for(name: str) -> ExternalCliBackend | None:
    """Return backend metadata for ``name``, or None when unsupported.

    Lookup order: SDK backends, subprocess adapters, then third-party harness
    providers (registered in process or installed through the
    ``openjiuwen.harness_providers`` entry point group). Built-in providers are
    not member backends; Claude Code and Codex members use their SDK backends.
    """
    sdk_backend = _SDK_BACKENDS.get(name)
    if sdk_backend is not None:
        return sdk_backend
    if name in available_adapters():
        adapter = build_adapter(name)
        return ExternalCliBackend(
            name=name,
            kind="adapter",
            supports_command_override=True,
            injects_system_prompt_via_arg=adapter.injects_system_prompt_via_arg(),
        )
    if name in third_party_providers():
        # The system prompt travels in HarnessContext.system_prompt; the
        # provider decides how its runtime receives it.
        return ExternalCliBackend(
            name=name,
            kind="provider",
            supports_command_override=False,
            injects_system_prompt_via_arg=True,
            provider=name,
        )
    return None


def third_party_providers() -> tuple[str, ...]:
    """Return the harness provider names that are not built into openjiuwen."""
    from openjiuwen.harness_providers.factory import PROVIDER_NAMES, available_providers

    return tuple(name for name in available_providers() if name not in PROVIDER_NAMES)


def provider_takes_endpoint_models(name: str) -> bool:
    """Return whether third-party provider ``name`` runs models on host endpoints.

    True only when the provider maps models at all (``ModelConfigProvider``)
    and its card declares ``ENDPOINT_MODELS``.
    """
    from openjiuwen.harness_protocol import HarnessCapability, ModelConfigProvider
    from openjiuwen.harness_providers.factory import resolve_provider

    provider = resolve_provider(name)
    return isinstance(provider, ModelConfigProvider) and provider.card.supports(HarnessCapability.ENDPOINT_MODELS)


def takes_auth_fallback(name: str) -> bool:
    """Return whether CLI kind ``name`` switches to a team model when its login fails.

    The fallback is a team pool entry on a host endpoint, so the member's
    provider must map models (``ModelConfigProvider``) and its card must declare
    both ``AUTH_FALLBACK`` and ``ENDPOINT_MODELS``; subprocess adapters never do.
    """
    backend = backend_for(name)
    if backend is None or backend.provider is None:
        return False
    from openjiuwen.harness_protocol import HarnessCapability, ModelConfigProvider
    from openjiuwen.harness_providers.factory import resolve_provider

    provider = resolve_provider(backend.provider)
    card = provider.card
    return (
        isinstance(provider, ModelConfigProvider)
        and card.supports(HarnessCapability.AUTH_FALLBACK)
        and card.supports(HarnessCapability.ENDPOINT_MODELS)
    )


def available_backends() -> tuple[str, ...]:
    """Return all supported external CLI backend names."""
    known = (*_SDK_BACKENDS, *available_adapters())
    return (*known, *(name for name in third_party_providers() if name not in known))


def is_known_backend(name: str) -> bool:
    """Return whether ``name`` is a supported external CLI backend."""
    return backend_for(name) is not None


def missing_sdk_requirement(name: str) -> SdkRequirement | None:
    """Return the backend's SDK requirement when it is not importable.

    Probes with ``importlib.util.find_spec`` so the SDK is not imported here;
    the harness still imports it lazily when the member actually starts.

    Args:
        name: A known external CLI backend name.

    Returns:
        The unmet ``SdkRequirement``, or ``None`` when the backend needs no
        SDK or the SDK is importable in the current interpreter.
    """
    backend = backend_for(name)
    if backend is None or backend.sdk_requirement is None:
        return None
    requirement = backend.sdk_requirement
    if importlib.util.find_spec(requirement.module) is not None:
        return None
    return requirement


__all__ = [
    "ExternalCliBackend",
    "SdkRequirement",
    "available_backends",
    "backend_for",
    "is_known_backend",
    "missing_sdk_requirement",
    "provider_takes_endpoint_models",
    "takes_auth_fallback",
    "third_party_providers",
]
