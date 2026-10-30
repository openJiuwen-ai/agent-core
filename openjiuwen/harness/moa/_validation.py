# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared validation for MoA counters, limits, and timeouts."""

import math

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error


def validate_positive_int(name: str, value: object) -> None:
    """Reject booleans and nonintegral values for a positive count."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise build_error(StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR, error_msg=f"{name} must be a positive integer")


def validate_optional_budget(name: str, value: object) -> None:
    """Accept an unlimited budget or a nonnegative integer attempt limit."""
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise build_error(
            StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
            error_msg=f"{name} must be a nonnegative integer or None",
        )


def validate_positive_timeout(name: str, value: object) -> None:
    """Require a finite positive duration usable by the event loop."""
    try:
        valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value > 0
    except OverflowError:
        valid = False
    if not valid:
        raise build_error(StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR, error_msg=f"{name} must be finite and positive")
