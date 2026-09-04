# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""Policy interface — generates candidate prompts from task + history + memory."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import Model, SystemMessage, UserMessage
from openjiuwen.dev_tools.tune.optimizer.prompt_search.history import OptimizationHistory
from openjiuwen.dev_tools.tune.optimizer.prompt_search.models import PromptCandidate, PromptRecord, PromptTaskSpec
from openjiuwen.dev_tools.tune.utils import TuneUtils


@dataclass
class PolicyRequest:
    """Inputs to one round of candidate generation."""

    task: PromptTaskSpec
    history: OptimizationHistory
    num_candidates: int
    iteration: int = 1
    temperature: float = 0.9
    similar_records: list = field(default_factory=list)


class PromptPolicy(ABC):
    """Produces candidate system prompts. The strategy should evolve with history."""

    @abstractmethod
    async def generate(self, request: PolicyRequest) -> list[PromptCandidate]:
        ...


_SYSTEM = (
    "You are a prompt engineer optimizing a SYSTEM PROMPT for a fixed task. "
    "You propose diverse, high-quality candidate system prompts and improve them using "
    "feedback from previous iterations. Output only valid JSON wrapped in ```json fences."
)

_PROMPT = """\
# Task objective (must be preserved — do not change what the prompt is for)
{objective}

{constraints_block}{base_block}{memory_block}{history_block}
# Instructions
Propose {n} DIFFERENT candidate SYSTEM PROMPTS that make an assistant perform the task above
as well as possible. Each candidate must:
- pursue the SAME objective (never drift to a different task);
- respect all stated constraints;
- differ meaningfully from the others (vary structure, specificity, examples, tone);
- apply the lessons from previous iterations when present.

Return ONLY valid JSON wrapped in ```json fences, an array of exactly {n} objects:
```json
[{{"prompt": "<the full system prompt>", "rationale": "<one line: what you changed and why>"}}]
```
"""


class LLMPromptPolicy(PromptPolicy):
    """Generate candidates with an LLM, evolving strategy via compressed history."""

    def __init__(self, model: Model) -> None:
        self._model = model

    async def generate(self, request: PolicyRequest) -> list[PromptCandidate]:
        task = request.task
        n = max(1, request.num_candidates)
        user_content = _PROMPT.format(
            objective=task.objective,
            n=n,
            constraints_block=_constraints_block(task.constraints),
            base_block=_base_block(task.base_prompt, request.history.best_prompt),
            memory_block=_memory_block(request.similar_records),
            history_block=_history_block(request.history),
        )
        try:
            response = await self._model.invoke(
                messages=[SystemMessage(content=_SYSTEM), UserMessage(content=user_content)],
                temperature=request.temperature,
            )
            candidates = _parse_candidates(response.content, n)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"LLMPromptPolicy: generation failed: {exc}")
            candidates = []

        # Always keep the best-known prompt in the pool on later iterations so the
        # optimizer never regresses below what it already found (elitism).
        if request.iteration > 1 and request.history.best_prompt:
            candidates.insert(
                0,
                PromptCandidate(
                    prompt=request.history.best_prompt,
                    rationale="carried-over best prompt (elitism)",
                ),
            )
        elif request.iteration == 1 and task.base_prompt:
            candidates.insert(
                0, PromptCandidate(prompt=task.base_prompt, rationale="user base prompt")
            )

        return candidates[:n] or [
            PromptCandidate(prompt=task.base_prompt or task.objective, rationale="fallback")
        ]


def _constraints_block(constraints: list) -> str:
    if not constraints:
        return ""
    lines = "\n".join(f"- {c}" for c in constraints)
    return f"# Constraints (all candidates must satisfy)\n{lines}\n\n"


def _base_block(base_prompt: str, best_prompt: str) -> str:
    reference = best_prompt or base_prompt
    if not reference:
        return ""
    return f"# Best current prompt (improve on this)\n{reference}\n\n"


def _memory_block(records: list) -> str:
    if not records:
        return ""
    lines = []
    for rec in records[:3]:
        prompt = rec.prompt if len(rec.prompt) <= 400 else rec.prompt[:400] + "…"
        lines.append(f"- (reward {rec.reward:.2f}) {prompt}")
    body = "\n".join(lines)
    return f"# Prompts that worked on similar past tasks (inspiration)\n{body}\n\n"


def _history_block(history) -> str:
    rendered = history.render()
    if not rendered:
        return ""
    return f"# Feedback from previous iterations\n{rendered}\n\n"


def _parse_candidates(raw: str, n: int) -> list[PromptCandidate]:
    data = TuneUtils.parse_json_from_llm_response(raw)
    if isinstance(data, dict):
        # tolerate {"candidates": [...]} or a single object
        data = data.get("candidates") or data.get("prompts") or [data]
    if not isinstance(data, list):
        return []
    candidates: list[PromptCandidate] = []
    for item in data:
        if isinstance(item, str):
            prompt, rationale = item, ""
        elif isinstance(item, dict):
            prompt = str(item.get("prompt") or item.get("system_prompt") or "").strip()
            rationale = str(item.get("rationale") or "").strip()
        else:
            continue
        if prompt:
            candidates.append(PromptCandidate(prompt=prompt, rationale=rationale))
    return candidates


__all__ = ["PolicyRequest", "PromptPolicy", "LLMPromptPolicy"]
