# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Model selection and model-group routing config compiler."""

from openjiuwen.core.foundation.llm.routing.compiler import (
    ModelGroupModelCandidate,
    compile_model_selection,
    get_model_group_models,
)
from openjiuwen.core.foundation.llm.routing.schema import (
    CompiledModelSelection,
    ModelSelection,
    ResolvedRoutingConfig,
    ResolvedModel,
    ResolvedModelGroup,
    ResolvedRoute,
    RoutingStrategyName,
)

__all__ = [
    "CompiledModelSelection",
    "ModelGroupModelCandidate",
    "ModelSelection",
    "ResolvedModel",
    "ResolvedModelGroup",
    "ResolvedRoutingConfig",
    "ResolvedRoute",
    "RoutingStrategyName",
    "compile_model_selection",
    "get_model_group_models",
]
