# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Deprecated no-op: TiktokenCounter counts multimodal content natively.

``TiktokenCounter.count_messages`` now handles block-list ``content`` itself —
text blocks are counted as text and ``image_url``/``image`` blocks as a fixed
placeholder (see ``DEFAULT_IMAGE_PLACEHOLDER_TOKENS``, overridable via the
``TIKTOKEN_IMAGE_PLACEHOLDER_TOKENS`` env var). The runtime monkey-patch this
module used to install is therefore unnecessary; the entry point is kept as a
no-op so existing callers and imports continue to work.
"""

from __future__ import annotations

from openjiuwen.core.context_engine.token.tiktoken_counter import (  # noqa: F401
    DEFAULT_IMAGE_PLACEHOLDER_TOKENS,
)


def apply_tiktoken_counter_multimodal_patch() -> None:
    """No-op kept for backward compatibility; core counting is multimodal-aware."""
    return None


__all__ = [
    "apply_tiktoken_counter_multimodal_patch",
]
