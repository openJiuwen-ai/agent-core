# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Deprecated no-op: TiktokenCounter counts multimodal content natively."""

from __future__ import annotations

from openjiuwen.core.context_engine.token.tiktoken_counter import (
    DEFAULT_IMAGE_PLACEHOLDER_TOKENS,
)


def apply_tiktoken_counter_multimodal_patch() -> None:
    """No-op retained for backward compatibility with existing callers."""
    return None


__all__ = [
    "DEFAULT_IMAGE_PLACEHOLDER_TOKENS",
    "apply_tiktoken_counter_multimodal_patch",
]
