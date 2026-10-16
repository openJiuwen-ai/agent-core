# coding: utf-8
# SPDX-License-Identifier: MIT
# Derived from https://github.com/llm-as-a-verifier/llm-as-a-verifier
# Revision: 8db8a114355a9d7fdf9a8d1d5c87f6aeebd18770
#
# MIT License
#
# Copyright (c) 2026 llm-as-a-verifier
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
"""通过 jiuwen Model 跟踪调用内进展，参考 LLM as a verifier。"""

from __future__ import annotations

import asyncio
import math
import re
import string
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.config import (
    LLMApiMode,
    LLMAuthMode,
    ModelClientConfig,
    ModelRequestConfig,
    ProviderType,
)
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, BaseMessage, UserMessage
from openjiuwen.core.foundation.llm.utils.endpoint_profiles import normalize_model_client_config
from openjiuwen.harness.moa._validation import validate_positive_int
from openjiuwen.harness.prompts.moa_verifier import build_progress_prompt, format_steps

GRANULARITY = 20


_LETTERS = list(string.ascii_uppercase[:GRANULARITY])


LETTER_TO_VALUE = {
    **{_LETTERS[i]: i / (GRANULARITY - 1) for i in range(GRANULARITY)},
    **{_LETTERS[i].lower(): i / (GRANULARITY - 1) for i in range(GRANULARITY)},
}


@dataclass
class ProgressResult:
    """Online progress-checkpoint results.

    Attributes:
        steps: 1-indexed agent-step numbers that were scored as checkpoints.
        scores: progress score in [0, 1] after each checkpoint step, averaged
            over `n_evaluations` repeats (same order as `steps`).
        per_rep_scores: the raw per-repeat score history (n_evaluations x
            len(steps)); entries are None where a repeat produced no
            readable score for that checkpoint.
    """

    steps: list[int]
    scores: list[float]
    per_rep_scores: list[list[float | None]] = field(repr=False)


def _expected_value_from_alts(alts: Sequence[tuple[str, float]]) -> float | None:
    """Expectation over the letter values present in one position's top-K
    logprob alternatives; None if no scale letter appears."""
    vals_to_lp: dict[float, float] = {}
    for tok_str, lp in alts:
        # Some BPE tokenizers merge the tag's closing ">" with the answer
        # letter into one token (">B"); strip it so the letter still counts.
        t = (tok_str or "").lstrip().lstrip(">").lstrip()
        if not t:
            continue
        c = t[0]
        if c in LETTER_TO_VALUE:
            v = LETTER_TO_VALUE[c]
            if v not in vals_to_lp or lp > vals_to_lp[v]:
                vals_to_lp[v] = lp
    if not vals_to_lp:
        return None
    mx = max(vals_to_lp.values())
    probs = {v: math.exp(lp - mx) for v, lp in vals_to_lp.items()}
    total = sum(probs.values())
    return sum(v * p / total for v, p in probs.items())


def extract_progress_scores(
    text: str,
    tokens: Sequence[str] | None,
    position_logprobs: Sequence[Sequence[tuple[str, float]]] | None,
    n: int,
) -> list[float | None]:
    """Decode the n checkpoint scores from one verifier response.

    Use the logprob expectation at the answer position after each `<c{i}>`
    tag, with a text-parsing fallback.
    """
    scores: list[float | None] = [None] * n
    if tokens and position_logprobs:
        joined = ""
        positions_after = []
        for j, tok in enumerate(tokens):
            joined += tok
            positions_after.append((len(joined), j + 1))
        for i in range(1, n + 1):
            tag = f"<c{i}>"
            idx = joined.find(tag)
            if idx < 0:
                continue
            target_char = idx + len(tag)
            for end_char, next_pos in positions_after:
                if end_char > target_char:
                    answer_pos = next_pos - 1
                    if 0 <= answer_pos < len(position_logprobs):
                        v = _expected_value_from_alts(position_logprobs[answer_pos])
                        if v is not None:
                            scores[i - 1] = v
                    break
    # Fallback: tagged letters in the text, then bare one-letter lines.
    for i in range(1, n + 1):
        if scores[i - 1] is None:
            m = re.search(rf"<c{i}>\s*([A-Ta-t])\s*</c{i}>", text or "")
            if m:
                scores[i - 1] = LETTER_TO_VALUE[m.group(1)]
    if any(s is None for s in scores):
        bare = [
            ln.strip()
            for ln in (text or "").splitlines()
            if ln.strip() and len(ln.strip()) == 1 and ln.strip() in LETTER_TO_VALUE
        ]
        if len(bare) == n:
            for i, c in enumerate(bare):
                if scores[i] is None:
                    scores[i] = LETTER_TO_VALUE[c]
    return scores


def validate_chat_model(model: Model) -> None:
    """Accept a Model or transparent wrapper using OpenAI-compatible Chat."""
    client_config = getattr(model, "model_client_config", None)
    request_config = getattr(model, "model_config", None)
    if (
        not isinstance(client_config, ModelClientConfig)
        or not isinstance(request_config, ModelRequestConfig)
        or not callable(getattr(model, "invoke", None))
    ):
        raise build_error(
            StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
            error_msg="MoA progress scoring requires a preconstructed jiuwen Model",
        )
    normalized = normalize_model_client_config(client_config)
    if (
        normalized.client_provider != ProviderType.OpenAI
        or normalized.api_mode not in (None, LLMApiMode.ChatCompletions)
        or normalized.auth_mode == LLMAuthMode.OpenAIAccountOAuth
    ):
        raise build_error(
            StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
            error_msg="MoA progress scoring currently supports OpenAI-compatible Chat models only",
        )


def _field(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _text_content(response: AssistantMessage) -> str:
    content = _field(response, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        texts = []
        for part in content:
            text = _field(part, "text", "")
            if isinstance(text, str):
                texts.append(part if isinstance(part, str) else text)
        return "".join(texts)
    return ""


def _alternative(value: Any) -> tuple[str, float] | None:
    token = _field(value, "token")
    logprob = _field(value, "logprob")
    if isinstance(token, str) and isinstance(logprob, (int, float)) and math.isfinite(logprob):
        return token, float(logprob)
    return None


def _normalize_logprobs(response: AssistantMessage) -> tuple[list[str], list[list[tuple[str, float]]]]:
    rows = _field(_field(response, "logprobs"), "content", []) or []
    if not isinstance(rows, (list, tuple)):
        return [], []
    tokens = []
    positions = []
    for row in rows:
        token = _field(row, "token", "")
        tokens.append(token if isinstance(token, str) else "")
        top = _field(row, "top_logprobs", []) or []
        alternatives = []
        if isinstance(top, (list, tuple)):
            for value in top:
                pair = _alternative(value)
                if pair is not None:
                    alternatives.append(pair)
        if not alternatives:
            sampled = _alternative(row)
            if sampled is not None:
                alternatives = [sampled]
        positions.append(alternatives)
    return tokens, positions


def decode_progress_score(response: AssistantMessage, *, text_fallback: bool = True) -> float | None:
    """Decode this response's probability score, with optional text fallback."""
    tokens, positions = _normalize_logprobs(response)
    text = _text_content(response) if text_fallback else ""
    score = extract_progress_scores(text, tokens, positions, 1)[0]
    return score if score is not None and math.isfinite(score) and 0 <= score <= 1 else None


def _rejects_logprobs(exc: BaseException) -> bool:
    """Recognize an explicit provider parameter rejection, never general failures."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if getattr(current, "status_code", None) == 400:
            body = getattr(current, "body", None)
            error = body.get("error", body) if isinstance(body, Mapping) else {}
            message = str(error.get("message", "")).lower() if isinstance(error, Mapping) else ""
            if any(parameter in message for parameter in ("logprobs", "top_logprobs")) and any(
                wording in message for wording in ("not supported", "unsupported", "not support")
            ):
                return True
        current = getattr(current, "cause", None) or current.__cause__ or current.__context__
    return False


class ProgressTracker:
    """Append a checkpoint before asynchronously scoring its trajectory."""

    def __init__(self, problem: str, *, model: Model, n_evaluations: int = 1) -> None:
        validate_positive_int("n_evaluations", n_evaluations)
        validate_chat_model(model)
        self.problem = problem
        self.model = model
        self.n_evaluations = n_evaluations
        self.steps: list[int] = []
        self.scores: list[float] = []
        self._step_texts: list[str] = []
        self._per_step_reps: list[list[float | None]] = []
        self._logprobs_enabled = True

    async def _evaluate(self, prompt: str) -> float | None:
        config = self.model.model_config
        messages: list[BaseMessage] = [UserMessage(content=prompt)]
        kwargs: dict[str, Any] = {
            "messages": messages,
            "tools": None,
            "temperature": config.temperature if config.temperature is not None else 1.0,
            "max_tokens": config.max_tokens if config.max_tokens is not None else 4096,
        }
        if self._logprobs_enabled:
            kwargs.update(logprobs=True, top_logprobs=20)
        try:
            response = await self.model.invoke(**kwargs)
        except Exception as exc:
            if "logprobs" not in kwargs or not _rejects_logprobs(exc):
                raise
            self._logprobs_enabled = False
            kwargs.pop("logprobs")
            kwargs.pop("top_logprobs")
            response = await self.model.invoke(**kwargs)
        return decode_progress_score(response)

    async def update(self, step: str) -> float:
        """Submit one checkpoint; failed scores still retain its text."""
        self._step_texts.append(str(step))
        checkpoint = len(self._step_texts)
        prompt = build_progress_prompt(self.problem, format_steps(self._step_texts), checkpoint, [checkpoint])
        requests = [asyncio.create_task(self._evaluate(prompt)) for _ in range(self.n_evaluations)]
        try:
            reps = await asyncio.gather(*requests)
        except asyncio.CancelledError:
            for request in requests:
                request.cancel()
            await asyncio.gather(*requests, return_exceptions=True)
            raise
        except Exception as exc:
            # A repeat failure invalidates the checkpoint. Settle all requests
            # before returning so an invocation never leaves scoring work behind.
            await asyncio.gather(*requests, return_exceptions=True)
            if isinstance(exc, BaseError):
                raise
            raise build_error(
                StatusCode.DEEPAGENT_RUNTIME_ERROR,
                error_msg="MoA progress scoring request failed",
                cause=exc,
            ) from exc
        valid = [value for value in reps if value is not None]
        score = sum(valid) / len(valid) if valid else 0.5
        self.steps.append(checkpoint)
        self.scores.append(score)
        self._per_step_reps.append(reps)
        return score

    def result(self) -> ProgressResult:
        """Return detached score history, with one row per repeated evaluation."""
        if not self.steps:
            raise build_error(StatusCode.DEEPAGENT_RUNTIME_ERROR, error_msg="no steps tracked yet")
        per_rep = [[row[repeat] for row in self._per_step_reps] for repeat in range(self.n_evaluations)]
        return ProgressResult(list(self.steps), list(self.scores), per_rep)
