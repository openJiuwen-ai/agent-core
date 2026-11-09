# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Forward guardrail result types from the detection library."""

from agent_shield.detection import (
    GuardrailResult,
    RiskAssessment,
    to_guardrail_result,
)

__all__ = [
    "GuardrailResult",
    "RiskAssessment",
    "to_guardrail_result",
]
