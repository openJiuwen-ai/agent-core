# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Capability inventory provider protocol."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from openjiuwen.symphony.models import CapabilityDescriptor, SourceSnapshot


@runtime_checkable
class CapabilityProvider(Protocol):
    """Asynchronous source of normalized capability inventory data."""

    async def capabilities(self) -> Sequence[CapabilityDescriptor]:
        """Return the capabilities in the current provider snapshot."""

        ...

    async def source_snapshot(self) -> SourceSnapshot:
        """Return the stable identity of the current provider snapshot."""

        ...


@runtime_checkable
class AtomicCapabilityProvider(CapabilityProvider, Protocol):
    """Optional provider extension for reading inventory and identity atomically."""

    async def inventory_snapshot(self) -> tuple[SourceSnapshot, Sequence[CapabilityDescriptor]]:
        """Return one mutually consistent source snapshot and capability inventory."""

        ...
