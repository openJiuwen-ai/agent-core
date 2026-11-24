# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Rough token estimation shared by context-engine fallbacks."""

from __future__ import annotations

import math
import re

_CJK_IDEOGRAPH_RE = re.compile(r"[一-鿿]")

# One CJK ideograph is usually closer to one token than a quarter token.
# Non-CJK text keeps the long-standing ~4 chars/token heuristic.
_CJK_CHARS_PER_TOKEN = 1.8
_OTHER_CHARS_PER_TOKEN = 4


def estimate_text_tokens_rough(text: str) -> int:
    """Estimate tokens for plain text with CJK-aware weighting.

    Callers must strip image payloads first (for example with
    ``sanitize_content_for_text``) so base64 data URLs never enter this
    character estimate.
    """
    if not text:
        return 0
    cjk_count = sum(1 for _ in _CJK_IDEOGRAPH_RE.finditer(text))
    other_count = max(len(text) - cjk_count, 0)
    return int(
        math.ceil(cjk_count / _CJK_CHARS_PER_TOKEN)
        + math.ceil(other_count / _OTHER_CHARS_PER_TOKEN)
    )
