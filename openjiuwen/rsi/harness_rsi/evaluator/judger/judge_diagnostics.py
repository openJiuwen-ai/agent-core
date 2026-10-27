# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Capture only completion metadata, scoped to one judge assessment attempt."""

from contextlib import contextmanager
from contextvars import ContextVar

_JUDGE_MODEL_CALLS = ContextVar("judge_model_calls", default=None)


@contextmanager
def collect_judge_model_calls():
    calls = []
    token = _JUDGE_MODEL_CALLS.set(calls)
    try:
        yield calls
    finally:
        _JUDGE_MODEL_CALLS.reset(token)


def record_judge_model_response(response):
    calls = _JUDGE_MODEL_CALLS.get()
    if calls is None:
        return
    diagnostic = {}
    finish_reason = getattr(response, "finish_reason", None)
    if finish_reason and finish_reason != "null":
        diagnostic["finish_reason"] = str(finish_reason)
    usage = getattr(response, "usage_metadata", None)
    if usage is not None:
        fields = ("input_tokens", "output_tokens", "total_tokens", "reasoning_tokens")
        diagnostic["usage"] = {key: getattr(usage, key) for key in fields
                               if isinstance(getattr(usage, key, None), (int, float))}
    if diagnostic:
        calls.append(diagnostic)
