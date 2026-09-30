# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Registry of external CLI backend kinds."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from typing import Literal

from openjiuwen.agent_teams.external.cli_agent.adapters import available_adapters, build_adapter

BackendKind = Literal["sdk", "adapter"]


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
    ),
}


def backend_for(name: str) -> ExternalCliBackend | None:
    """Return backend metadata for ``name``, or None when unsupported."""
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
    return None


def available_backends() -> tuple[str, ...]:
    """Return all supported external CLI backend names."""
    return (*_SDK_BACKENDS, *available_adapters())


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
]
