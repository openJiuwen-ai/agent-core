# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Registry of external CLI backend kinds."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from openjiuwen.agent_teams.external.cli_agent.adapters import available_adapters, build_adapter

BackendKind = Literal["sdk", "adapter"]


@dataclass(frozen=True, slots=True)
class ExternalCliBackend:
    """Static metadata for an external CLI backend kind."""

    name: str
    kind: BackendKind
    supports_command_override: bool
    injects_system_prompt_via_arg: bool


_SDK_BACKENDS: dict[str, ExternalCliBackend] = {
    "claude": ExternalCliBackend(
        name="claude",
        kind="sdk",
        supports_command_override=False,
        injects_system_prompt_via_arg=True,
    ),
    "codex": ExternalCliBackend(
        name="codex",
        kind="sdk",
        supports_command_override=False,
        injects_system_prompt_via_arg=True,
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


__all__ = ["ExternalCliBackend", "available_backends", "backend_for", "is_known_backend"]
