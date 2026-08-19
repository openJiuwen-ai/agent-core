# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compatibility wrapper for the pre-refactor online rail factory path."""

from __future__ import annotations

from ..core.rail_factory import (
    build_online_rail_from_env,
    build_online_training_rail_from_env,
    build_rl_online_rail_from_env,
    has_online_training_rail,
    is_online_training_rail_instance,
)

__all__ = [
    "build_online_rail_from_env",
    "build_online_training_rail_from_env",
    "build_rl_online_rail_from_env",
    "has_online_training_rail",
    "is_online_training_rail_instance",
]
