# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Registries for auto-harness metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, TypeVar

from openjiuwen.auto_harness.schema import (
    PipelineSpec,
    StageSpec,
)

SpecT = TypeVar("SpecT")


@dataclass
class BaseRegistry(Generic[SpecT]):
    """Shared registry implementation."""

    _items: dict[str, SpecT] = field(
        default_factory=dict
    )

    def register(self, spec: SpecT) -> None:
        name = getattr(spec, "name")
        if name in self._items:
            raise ValueError(
                f"Duplicate registration: {name}"
            )
        self._items[name] = spec

    def get(self, name: str) -> SpecT | None:
        return self._items.get(name)

    def names(self) -> list[str]:
        return list(self._items.keys())

    def require(self, name: str) -> SpecT:
        spec = self.get(name)
        if spec is None:
            raise KeyError(f"Unknown item '{name}'")
        return spec


@dataclass
class StageRegistry(BaseRegistry[StageSpec]):
    """Registry for stage specs."""


@dataclass
class PipelineRegistry(BaseRegistry[PipelineSpec]):
    """Registry for pipeline specs."""
