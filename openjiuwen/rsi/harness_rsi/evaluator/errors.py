# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Evaluator exception taxonomy shared with external benchmark adapters."""


class EvaluationInfrastructureError(RuntimeError):
    """The evaluator could not obtain a trustworthy score for a case."""


__all__ = ["EvaluationInfrastructureError"]
