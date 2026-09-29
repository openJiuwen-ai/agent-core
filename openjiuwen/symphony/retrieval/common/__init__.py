"""Shared retrieval models and prompt resources."""

from __future__ import annotations

from importlib import import_module
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import models as models
    from . import prompts as prompts

__all__ = [
    "models",
    "prompts",
]


def __getattr__(name: str) -> ModuleType:
    if name in __all__:
        module = import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
