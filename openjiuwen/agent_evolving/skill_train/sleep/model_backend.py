# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""LLM-backed sleep backend (target + optimizer clients)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from openjiuwen.agent_evolving.skill_train.llm_client import ChatLLMClient
from openjiuwen.agent_evolving.skill_train.sleep.backend_base import Backend, ReflectRequest
from openjiuwen.agent_evolving.skill_train.sleep.prompts import render
from openjiuwen.agent_evolving.skill_train.sleep.scoring import (
    keyword_soft_score,
    score_exact_pair,
)
from openjiuwen.agent_evolving.skill_train.sleep.types import EditRecord, ReplayResult, TaskRecord
from openjiuwen.agent_evolving.utils import TuneUtils
from openjiuwen.core.common.logging import logger

# Transport / decode failures from the optimizer client; programming bugs propagate.
_OPTIMIZER_CALL_ERRORS = (OSError, RuntimeError, TimeoutError, ValueError, TypeError)


@dataclass(frozen=True)
class _ClientPair:
    target: ChatLLMClient
    optimizer: ChatLLMClient


def _follow_ups_text(task: TaskRecord) -> str:
    bullets = []
    for line in (task.context_excerpt or "").splitlines():
        if line.startswith("- "):
            item = line[2:].strip()
            if item:
                bullets.append("- " + item)
    return "\n".join(bullets)


def _loads_jsonish(raw: str) -> object | None:
    parsed = TuneUtils.parse_json_from_llm_response(raw)
    if parsed is not None:
        return parsed
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None
    except TypeError:
        return None


def _edits_from_payload(raw: str, *, budget: int, target: str) -> List[EditRecord]:
    payload = _loads_jsonish(raw)
    if isinstance(payload, dict):
        maybe = payload.get("edits")
        items: Sequence[object] = maybe if isinstance(maybe, list) else []
    elif isinstance(payload, list):
        items = payload
    else:
        return []
    out: List[EditRecord] = []
    for item in items[:budget]:
        if not isinstance(item, dict):
            continue
        out.append(
            EditRecord(
                target=target,
                op=str(item.get("op", "add") or "add"),
                content=str(item.get("content", "") or ""),
                anchor=str(item.get("anchor", "") or ""),
                rationale=str(item.get("rationale", "") or ""),
            )
        )
    return out


def _format_failure_block(task: TaskRecord, result: ReplayResult) -> str:
    parts = [f"- intent: {task.intent}"]
    follow_ups = _follow_ups_text(task)
    if follow_ups:
        joined = " | ".join(line[2:] for line in follow_ups.splitlines())
        parts.append(f"  follow-ups: {joined[:300]}")
    if task.reference:
        parts.append(f"  rubric: {task.reference[:400]}")
    why = result.fail_reason or result.judge_rationale
    parts.append(f"  response: {(result.response or '')[:300]}")
    parts.append(f"  why: {why}")
    return "\n".join(parts)


class ModelBackend(Backend):
    """Backend backed by skill_train ChatLLMClient (optimizer/target)."""

    name = "model"

    def __init__(
        self,
        *,
        target_client: ChatLLMClient,
        optimizer_client: Optional[ChatLLMClient] = None,
        preferences: str = "",
    ) -> None:
        optimizer = optimizer_client or target_client
        self._clients = _ClientPair(target=target_client, optimizer=optimizer)
        self.preferences = preferences
        self._token_tally = 0

    def tokens_used(self) -> int:
        return self._token_tally

    def _charge(self, prompt: str, reply: str) -> None:
        self._token_tally += max(1, len(prompt + reply) // 4)

    def attempt(
        self,
        task: TaskRecord,
        skill: str,
        memory: str,
        sample_id: int = 0,
    ) -> str:
        del sample_id
        prompt = render(
            "attempt",
            {
                "__SKILL__": skill or "(empty)",
                "__MEMORY__": memory or "(empty)",
                "__INTENT__": task.intent,
                "__CONTEXT__": task.context_excerpt or "",
            },
        )
        text, _meta = self._clients.target.chat(
            system="You are the target agent replaying a harvested task.",
            user=prompt,
            stage="sleep_attempt",
        )
        self._charge(prompt, text)
        return text.strip()

    def attempt_with_tools(
        self,
        task: TaskRecord,
        skill: str,
        memory: str,
        tools: List[str],
    ) -> Tuple[str, List[str]]:
        response = self.attempt(task, skill, memory)
        pattern = r"(?i)\btool_call\s*:\s*{}\b"
        called = [
            tool for tool in tools if re.search(pattern.format(re.escape(tool)), response)
        ]
        return response, called

    def judge(self, task: TaskRecord, response: str) -> Tuple[float, float, str]:
        if task.reference_kind == "exact" and task.reference:
            return score_exact_pair(task.reference, response)
        rubric = task.reference or task.intent
        prompt = render(
            "judge",
            {
                "__INTENT__": task.intent,
                "__RUBRIC__": rubric,
                "__RESPONSE__": response,
            },
        )
        raw, _meta = self._clients.optimizer.chat(
            system="You are a strict grader. Return JSON only.",
            user=prompt,
            stage="sleep_judge",
        )
        self._charge(prompt, raw)
        parsed = TuneUtils.parse_json_from_llm_response(raw)
        if isinstance(parsed, dict):
            soft = float(parsed.get("score", 0.0) or 0.0)
            soft = max(0.0, min(1.0, soft))
            reason = str(parsed.get("reason", "") or "")
            hard = 1.0 if soft >= 0.8 else 0.0
            return hard, soft, reason
        soft = keyword_soft_score(rubric, response)
        return (1.0 if soft >= 0.8 else 0.0), soft, "fallback-keyword"

    def synthesize_rubric(self, task: TaskRecord) -> str:
        heuristic = task.reference or ""
        follow_ups = _follow_ups_text(task)
        if not follow_ups and not heuristic:
            return ""
        prompt = render(
            "rubric",
            {
                "__INTENT__": task.intent,
                "__FOLLOW_UPS__": follow_ups or "(none)",
                "__ATTEMPTED__": (task.attempted_solution or "(none)")[:1200],
                "__HEURISTIC__": heuristic or "(none)",
            },
        )
        try:
            raw, _meta = self._clients.optimizer.chat(
                system="You write concise grading rubrics. Return JSON only.",
                user=prompt,
                stage="sleep_rubric",
            )
        except _OPTIMIZER_CALL_ERRORS as exc:
            logger.warning("[skill_sleep] rubric synthesis failed: %s", exc, exc_info=True)
            return heuristic
        self._charge(prompt, raw)
        parsed = TuneUtils.parse_json_from_llm_response(raw)
        items = parsed.get("rubric") if isinstance(parsed, dict) else None
        if not isinstance(items, list):
            return heuristic
        checks = [str(item).strip() for item in items if str(item or "").strip()]
        if not checks:
            return heuristic
        head = heuristic.splitlines()[0] if heuristic else f"Task: {task.intent}"
        return head + "\n" + "\n".join("- " + check for check in checks[:8])

    def reflect_request(self, request: ReflectRequest) -> List[EditRecord]:
        if not request.evolve_skill and not request.evolve_memory:
            return []
        target = "skill" if request.evolve_skill else "memory"
        cur_doc = request.skill if request.evolve_skill else request.memory
        blocks = [_format_failure_block(task, result) for task, result in list(request.failures)[:12]]
        prefs = ""
        if self.preferences.strip():
            prefs = f"# User preferences\n{self.preferences.strip()}\n"
        prompt = render(
            "reflect",
            {
                "__EDIT_BUDGET__": str(request.edit_budget),
                "__TARGET__": target,
                "__CUR_DOC__": cur_doc or "(empty)",
                "__PREFS__": prefs,
                "__FAILURES__": "\n".join(blocks) or "(none)",
            },
        )
        raw, _meta = self._clients.optimizer.chat(
            system="You propose bounded skill edits as JSON only.",
            user=prompt,
            stage="sleep_reflect",
            max_completion_tokens=8192,
        )
        self._charge(prompt, raw)
        return _edits_from_payload(raw, budget=request.edit_budget, target=target)
