from __future__ import annotations

import re

from openjiuwen.core.context_engine.processor.offloader.rules.types import RuleContext


ERROR_RE = re.compile(
    r"\b(error|failed|failure|traceback|exception|warn|warning)\b",
    re.IGNORECASE,
)


def count_tokens(text: str, ctx: RuleContext) -> int:
    if ctx.count_tokens is not None:
        return max(ctx.count_tokens(text), 1)
    return max(len(text) // 3, 1)


def meets_savings_ratio(original: str, candidate: str, ctx: RuleContext) -> bool:
    original_tokens = count_tokens(original, ctx)
    candidate_tokens = count_tokens(candidate, ctx)
    if original_tokens <= 0:
        return False
    return 1 - candidate_tokens / original_tokens >= ctx.min_savings_ratio


def fits_budget_and_saves(original: str, candidate: str, ctx: RuleContext) -> bool:
    if count_tokens(candidate, ctx) > ctx.max_tokens:
        return False
    return meets_savings_ratio(original, candidate, ctx)
