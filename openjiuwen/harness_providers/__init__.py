# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Built-in implementations of ``openjiuwen.harness_protocol``.

The package hosts one provider per supported agent runtime (``native`` for
the in-process DeepAgent, ``native_v2`` for NativeHarness, ``claudecode``, ``codex`` and ``dsh``), the
``HarnessIOAdapter`` that projects the protocol onto the DeepAgent-style
input/output contract, and the manifest-driven ``create_harness`` factory.
Heavy provider modules are imported lazily so importing this package never
requires an optional vendor SDK.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "HarnessIOAdapter",
    "HarnessProviderName",
    "PROVIDER_NAMES",
    "build_harness_context",
    "create_harness",
    "resolve_provider",
]


def __getattr__(name: str) -> Any:
    """Lazily import the public surface on demand."""
    if name == "HarnessIOAdapter":
        from openjiuwen.harness_providers.io_adapter import HarnessIOAdapter

        return HarnessIOAdapter
    if name in {"HarnessProviderName", "PROVIDER_NAMES", "build_harness_context", "create_harness", "resolve_provider"}:
        from openjiuwen.harness_providers import factory

        return getattr(factory, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
