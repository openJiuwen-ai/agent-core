# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Exposure policy for callable tools.

The exposure value describes when a tool may be included in the model-visible
tool list. It is runtime policy metadata and must not be serialized into the
model-facing :class:`ToolInfo` schema.
"""

from enum import Enum


class ToolExposure(str, Enum):
    """Controls when a callable tool is exposed to the model."""

    DIRECT = "direct"
    DEFERRED = "deferred"


__all__ = ["ToolExposure"]
