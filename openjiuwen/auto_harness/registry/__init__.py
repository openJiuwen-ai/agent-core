# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Registries and builtin registration for auto-harness."""

from openjiuwen.auto_harness.registry.base import (
    PipelineRegistry,
    StageRegistry,
)
from openjiuwen.auto_harness.registry.builtin import (
    build_pipeline_registry,
    build_stage_registry,
    register_builtin_stages,
)

__all__ = [
    "PipelineRegistry",
    "StageRegistry",
    "build_pipeline_registry",
    "build_stage_registry",
    "register_builtin_stages",
]
