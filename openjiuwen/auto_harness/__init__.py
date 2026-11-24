# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Compatibility shim for AutoHarness after its move into RSI.

Keep the legacy top-level import path working:

    from openjiuwen.auto_harness import AutoHarnessOrchestrator

And keep legacy **submodule** imports working during the jiuwenswarm
transition window (core may land first):

    from openjiuwen.auto_harness.schema import AutoHarnessConfig
    from openjiuwen.auto_harness.infra.git_auth import build_git_auth_env
    from openjiuwen.auto_harness.pipelines import META_EVOLVE_PIPELINE
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
import sys
from types import ModuleType
from typing import Iterable

from openjiuwen.rsi.harness_rsi.auto_harness import (
    PIPELINE_PREFERENCE_AUTO,
    AutoHarnessConfig,
    AutoHarnessOrchestrator,
    AutoHarnessPaths,
    CycleResult,
    Experience,
    Gap,
    OptimizationTask,
    PipelineRegistry,
    PipelineSpec,
    ResearchContext,
    StageRegistry,
    StageSpec,
    create_auto_harness_orchestrator,
    normalize_pipeline_preference,
)

__all__ = [
    "AutoHarnessConfig",
    "AutoHarnessPaths",
    "AutoHarnessOrchestrator",
    "CycleResult",
    "Experience",
    "Gap",
    "OptimizationTask",
    "PIPELINE_PREFERENCE_AUTO",
    "PipelineRegistry",
    "PipelineSpec",
    "ResearchContext",
    "StageRegistry",
    "StageSpec",
    "create_auto_harness_orchestrator",
    "normalize_pipeline_preference",
]

_SRC_PREFIX = "openjiuwen.rsi.harness_rsi.auto_harness"
_DST_PREFIX = "openjiuwen.auto_harness"
_logger = logging.getLogger(__name__)


def _iter_src_module_names(src_pkg: ModuleType) -> Iterable[str]:
    """Yield fully-qualified module names under the RSI auto_harness package."""
    yield src_pkg.__name__
    if not getattr(src_pkg, "__path__", None):
        return
    for module_info in pkgutil.walk_packages(
        src_pkg.__path__,
        prefix=src_pkg.__name__ + ".",
    ):
        yield module_info.name


def _alias_submodules() -> None:
    """Map ``openjiuwen.auto_harness.*`` to the RSI implementation modules.

    Identity is preserved: ``sys.modules[legacy] is sys.modules[rsi]``.
    The legacy package root itself stays this shim module so top-level
    ``__all__`` exports remain explicit and documented.
    """
    src_pkg = importlib.import_module(_SRC_PREFIX)
    for name in _iter_src_module_names(src_pkg):
        if name == _SRC_PREFIX:
            continue
        try:
            mod = importlib.import_module(name)
        except Exception as exc:  # pragma: no cover - optional/broken leaf
            _logger.debug(
                "auto_harness compat: skip alias for %s (%s)",
                name,
                exc,
            )
            continue
        alias_name = _DST_PREFIX + name[len(_SRC_PREFIX):]
        sys.modules[alias_name] = mod


def __getattr__(name: str):
    """Resolve ``openjiuwen.auto_harness.<submodule>`` attribute access."""
    alias_name = f"{_DST_PREFIX}.{name}"
    cached = sys.modules.get(alias_name)
    if cached is not None:
        return cached
    try:
        mod = importlib.import_module(f"{_SRC_PREFIX}.{name}")
    except ModuleNotFoundError as exc:
        raise AttributeError(
            f"module {_DST_PREFIX!r} has no attribute {name!r}"
        ) from exc
    sys.modules[alias_name] = mod
    return mod


_alias_submodules()
