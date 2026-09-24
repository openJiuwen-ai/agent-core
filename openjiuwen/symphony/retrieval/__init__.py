"""Skill retrieval package grouped by build, search, and shared helpers."""

from __future__ import annotations

from importlib import import_module
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import build as build
    from . import common as common
    from . import llm as llm
    from . import search as search

__all__ = [
    "build",
    "common",
    "llm",
    "search",
]


def __getattr__(name: str) -> ModuleType:
    if name in __all__:
        module = import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
