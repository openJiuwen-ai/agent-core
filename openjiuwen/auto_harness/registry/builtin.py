# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Built-in pipeline and stage registration."""

from __future__ import annotations

import importlib
import inspect
from typing import Any, Callable

from openjiuwen.auto_harness.pipelines.extended_evolve_pipeline import (
    ExtendedEvolvePipeline,
)
from openjiuwen.auto_harness.pipelines.meta_evolve_pipeline import (
    MetaEvolvePipeline,
)
from openjiuwen.auto_harness.registry.base import (
    PipelineRegistry,
    StageRegistry,
)
from openjiuwen.auto_harness.schema import (
    AutoHarnessConfig,
)
from openjiuwen.auto_harness.stages.assess import (
    AssessStage,
)
from openjiuwen.auto_harness.stages.commit import (
    CommitStage,
)
from openjiuwen.auto_harness.stages.implement import (
    ImplementStage,
)
from openjiuwen.auto_harness.stages.learnings import (
    LearningsStage,
)
from openjiuwen.auto_harness.stages.plan import (
    PlanStage,
)
from openjiuwen.auto_harness.stages.publish_pr import (
    PublishPRStage,
)
from openjiuwen.auto_harness.stages.verify import (
    VerifyStage,
)


def _load_registrar(path: str) -> Callable[..., Any]:
    """Load a registrar callable from ``module:callable`` notation."""
    module_name, sep, attr = path.partition(":")
    if not sep or not module_name or not attr:
        raise ValueError(
            "Registrar must use 'module:callable' syntax: "
            f"{path}"
        )
    module = importlib.import_module(module_name)
    registrar = getattr(module, attr)
    if not callable(registrar):
        raise TypeError(f"Registrar '{path}' is not callable")
    return registrar


def _call_pipeline_registrar(
    registrar: Callable[..., Any],
    pipeline_registry: PipelineRegistry,
    stage_registry: StageRegistry,
) -> None:
    """Invoke a pipeline registrar with a supported signature."""
    params = list(
        inspect.signature(registrar).parameters.values()
    )
    positional_kinds = {
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    }
    positional = [
        p for p in params if p.kind in positional_kinds
    ]
    if len(positional) >= 2:
        registrar(pipeline_registry, stage_registry)
        return
    registrar(pipeline_registry)


def register_builtin_stages(
    registry: StageRegistry,
) -> StageRegistry:
    """Register built-in stage metadata."""
    for stage_cls in [
        AssessStage,
        PlanStage,
        ImplementStage,
        VerifyStage,
        CommitStage,
        PublishPRStage,
        LearningsStage,
    ]:
        registry.register(stage_cls.spec())
    return registry


def build_stage_registry(
    config: AutoHarnessConfig,
) -> StageRegistry:
    """Build the stage registry from built-ins and extensions."""
    registry = register_builtin_stages(StageRegistry())
    for path in config.stage_registrars:
        _load_registrar(path)(registry)
    return registry


def build_pipeline_registry(
    config: AutoHarnessConfig,
    *,
    stage_registry: StageRegistry,
) -> PipelineRegistry:
    """Build the pipeline registry from built-ins and extensions."""
    registry = PipelineRegistry()
    registry.register(MetaEvolvePipeline.spec())
    registry.register(
        ExtendedEvolvePipeline.spec()
    )
    for path in config.pipeline_registrars:
        registrar = _load_registrar(path)
        _call_pipeline_registrar(
            registrar,
            registry,
            stage_registry,
        )
    return registry
