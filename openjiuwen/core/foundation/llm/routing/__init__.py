# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Model selection and model-group routing config compiler."""

from openjiuwen.core.foundation.llm.routing.compiler import compile_model_selection
from openjiuwen.core.foundation.llm.routing.schema import (
    CompiledModelSelection,
    ModelSelection,
    ResolvedModel,
    ResolvedModelGroup,
    ResolvedRoute,
)

__all__ = [
    "CompiledModelSelection",
    "ModelSelection",
    "ResolvedModel",
    "ResolvedModelGroup",
    "ResolvedRoute",
    "compile_model_selection",
]
