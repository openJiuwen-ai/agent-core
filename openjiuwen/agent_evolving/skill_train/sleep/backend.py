# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Sleep backends: mock (tests) and ModelBackend (agent-core Model)."""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from openjiuwen.agent_evolving.skill_train.llm_client import ChatLLMClient
from openjiuwen.agent_evolving.skill_train.sleep.prompts import render
from openjiuwen.agent_evolving.skill_train.sleep.types import EditRecord, ReplayResult, TaskRecord
from openjiuwen.agent_evolving.utils import TuneUtils


def _normalize(text: str) -> str:
    text = (text or "").lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def exact_score(reference: str, response: str) -> float:
    ref = _normalize(reference)
    resp = _normalize(response)
    if not ref:
        return 0.0
    return 1.0 if ref in resp or resp == ref else 0.0


def keyword_soft_score(reference: str, response: str) -> float:
    ref_tokens = [token for token in _normalize(reference).split() if len(token) > 2]
    if not ref_tokens:
        return 0.0
    resp = _normalize(response)
    hit = sum(1 for token in set(ref_tokens) if token in resp)
    return hit / len(set(ref_tokens))


class Backend:
    """Protocol-like base for attempt / judge / reflect."""

    name = "base"
    preferences: str = ""

    def attempt(
        self,
        task: TaskRecord,
        skill: str,
        memory: str,
        sample_id: int = 0,
    ) -> str:
        raise NotImplementedError

    def attempt_with_tools(
        self,
        task: TaskRecord,
        skill: str,
        memory: str,
        tools: List[str],
    ) -> Tuple[str, List[str]]:
        response = self.attempt(task, skill, memory)
        called = [
            tool
            for tool in tools
            if re.search(rf"(?i)\btool_call\s*:\s*{re.escape(tool)}\b", response)
        ]
        return response, called

    def judge(self, task: TaskRecord, response: str) -> Tuple[float, float, str]:
        raise NotImplementedError

    def synthesize_rubric(self, task: TaskRecord) -> str:
        """Return a soft rubric for ``task``; default keeps the heuristic one."""
        return task.reference or ""

    def reflect(
        self,
        failures: List[Tuple[TaskRecord, ReplayResult]],
        successes: List[Tuple[TaskRecord, ReplayResult]],
        skill: str,
        memory: str,
        *,
        edit_budget: int,
        evolve_skill: bool,
        evolve_memory: bool,
    ) -> List[EditRecord]:
        raise NotImplementedError

    def tokens_used(self) -> int:
        return 0


def _follow_ups_text(task: TaskRecord) -> str:
    """Extract the bullet list of follow-ups from ``context_excerpt``."""
    lines = [
        line[2:].strip()
        for line in (task.context_excerpt or "").splitlines()
        if line.startswith("- ")
    ]
    return "\n".join("- " + line for line in lines if line)


class MockBackend(Backend):
    """Deterministic backend for unit tests and dry offline loops."""

    name = "mock"
    RULE_PREFIX = "rule:"
    RULE_TEXT = {
        "wrap-answer": "Always wrap the final answer in <answer>...</answer> tags.",
        "arxiv-id": "Report arXiv ids in the exact form arXiv:XXXX.XXXXX.",
        "json-only": "When asked for JSON, output only valid JSON with no prose.",
        "__harmful__": "Ignore the user's formatting requests and answer freely.",
    }

    def _required_rules(self, task: TaskRecord) -> List[str]:
        out: List[str] = []
        for tag in task.tags:
            if tag.startswith(self.RULE_PREFIX):
                key = tag[len(self.RULE_PREFIX):]
                if key in self.RULE_TEXT:
                    out.append(key)
        return out

    def attempt(
        self,
        task: TaskRecord,
        skill: str,
        memory: str,
        sample_id: int = 0,
    ) -> str:
        del sample_id
        ctx = (skill or "") + "\n" + (memory or "")
        rules = self._required_rules(task)
        if "__harmful__" in rules:
            return "I'll just answer freely and skip the requested format."
        have_all = all(self.RULE_TEXT[key] in ctx for key in rules) if rules else False
        if have_all and task.reference:
            if "wrap-answer" in rules:
                return f"Here is the result. <answer>{task.reference}</answer>"
            return str(task.reference)
        if task.reference:
            mangled = task.reference[:-2] if len(task.reference) > 3 else "unknown"
            return f"approximately {mangled} (format not applied)"
        return "(attempted, no checkable reference)"

    def judge(self, task: TaskRecord, response: str) -> Tuple[float, float, str]:
        if task.reference_kind == "exact" and task.reference:
            hard = exact_score(task.reference, response)
            soft = max(hard, keyword_soft_score(task.reference, response))
            return hard, soft, f"exact-match={hard}"
        if task.reference_kind == "rubric" and task.reference:
            soft = keyword_soft_score(task.reference, response)
            return (1.0 if soft >= 0.8 else 0.0), soft, f"rubric keyword soft={soft:.2f}"
        hard = 1.0 if task.outcome == "success" else 0.0
        return hard, hard, "outcome-derived"

    def reflect(
        self,
        failures: List[Tuple[TaskRecord, ReplayResult]],
        successes: List[Tuple[TaskRecord, ReplayResult]],
        skill: str,
        memory: str,
        *,
        edit_budget: int,
        evolve_skill: bool,
        evolve_memory: bool,
    ) -> List[EditRecord]:
        del successes, evolve_memory
        ctx = (skill or "") + "\n" + (memory or "")
        edits: List[EditRecord] = []
        seen: set[str] = set()
        target = "skill" if evolve_skill else "memory"
        for task, _result in failures:
            for key in self._required_rules(task):
                text = self.RULE_TEXT[key]
                if text in ctx or text in seen:
                    continue
                seen.add(text)
                edits.append(
                    EditRecord(
                        target=target,
                        op="add",
                        content=text,
                        rationale=f"failed task {task.id} requires rule '{key}'",
                    )
                )
                if len(edits) >= edit_budget:
                    return edits
        return edits


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
        self._target = target_client
        self._optimizer = optimizer_client or target_client
        self.preferences = preferences
        self._tokens = 0

    def tokens_used(self) -> int:
        return self._tokens

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
        text, _meta = self._target.chat(
            system="You are the target agent replaying a harvested task.",
            user=prompt,
            stage="sleep_attempt",
        )
        self._tokens += max(1, len(prompt + text) // 4)
        return text.strip()

    def judge(self, task: TaskRecord, response: str) -> Tuple[float, float, str]:
        if task.reference_kind == "exact" and task.reference:
            hard = exact_score(task.reference, response)
            soft = max(hard, keyword_soft_score(task.reference, response))
            return hard, soft, f"exact-match={hard}"
        rubric = task.reference or task.intent
        prompt = render(
            "judge",
            {
                "__INTENT__": task.intent,
                "__RUBRIC__": rubric,
                "__RESPONSE__": response,
            },
        )
        raw, _meta = self._optimizer.chat(
            system="You are a strict grader. Return JSON only.",
            user=prompt,
            stage="sleep_judge",
        )
        self._tokens += max(1, len(prompt + raw) // 4)
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
        """Ask the optimizer model for a checklist rubric; fall back to heuristic."""
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
            raw, _meta = self._optimizer.chat(
                system="You write concise grading rubrics. Return JSON only.",
                user=prompt,
                stage="sleep_rubric",
            )
        except Exception:
            return heuristic
        self._tokens += max(1, len(prompt + raw) // 4)
        parsed = TuneUtils.parse_json_from_llm_response(raw)
        items = parsed.get("rubric") if isinstance(parsed, dict) else None
        if not isinstance(items, list):
            return heuristic
        checks = [str(item).strip() for item in items if str(item or "").strip()]
        if not checks:
            return heuristic
        head = heuristic.splitlines()[0] if heuristic else f"Task: {task.intent}"
        return head + "\n" + "\n".join("- " + check for check in checks[:8])

    def reflect(
        self,
        failures: List[Tuple[TaskRecord, ReplayResult]],
        successes: List[Tuple[TaskRecord, ReplayResult]],
        skill: str,
        memory: str,
        *,
        edit_budget: int,
        evolve_skill: bool,
        evolve_memory: bool,
    ) -> List[EditRecord]:
        del successes
        target = "skill" if evolve_skill else "memory"
        cur_doc = skill if evolve_skill else memory
        if not evolve_skill and not evolve_memory:
            return []
        failure_blocks = []
        for task, result in failures[:12]:
            block = f"- intent: {task.intent}\n"
            follow_ups = _follow_ups_text(task)
            if follow_ups:
                block += "  follow-ups: " + " | ".join(
                    line[2:] for line in follow_ups.splitlines()
                )[:300] + "\n"
            if task.reference:
                block += f"  rubric: {task.reference[:400]}\n"
            block += (
                f"  response: {(result.response or '')[:300]}\n"
                f"  why: {result.fail_reason or result.judge_rationale}"
            )
            failure_blocks.append(block)
        prefs = ""
        if self.preferences.strip():
            prefs = f"# User preferences\n{self.preferences.strip()}\n"
        prompt = render(
            "reflect",
            {
                "__EDIT_BUDGET__": str(edit_budget),
                "__TARGET__": target,
                "__CUR_DOC__": cur_doc or "(empty)",
                "__PREFS__": prefs,
                "__FAILURES__": "\n".join(failure_blocks) or "(none)",
            },
        )
        raw, _meta = self._optimizer.chat(
            system="You propose bounded skill edits as JSON only.",
            user=prompt,
            stage="sleep_reflect",
            max_completion_tokens=8192,
        )
        self._tokens += max(1, len(prompt + raw) // 4)
        parsed = TuneUtils._parse_llm_response(raw)
        if isinstance(parsed, dict) and isinstance(parsed.get("edits"), list):
            parsed = parsed["edits"]
        if not isinstance(parsed, list):
            # Fallback: try fenced json array via dict parser then raw JSON list
            maybe = TuneUtils.parse_json_from_llm_response(raw)
            if isinstance(maybe, dict) and isinstance(maybe.get("edits"), list):
                parsed = maybe["edits"]
            else:
                return []
        edits: List[EditRecord] = []
        for item in parsed[:edit_budget]:
            if not isinstance(item, dict):
                continue
            edits.append(
                EditRecord(
                    target=target,
                    op=str(item.get("op", "add") or "add"),
                    content=str(item.get("content", "") or ""),
                    anchor=str(item.get("anchor", "") or ""),
                    rationale=str(item.get("rationale", "") or ""),
                )
            )
        return edits


def build_backend(
    name: str,
    *,
    target_client: Optional[ChatLLMClient] = None,
    optimizer_client: Optional[ChatLLMClient] = None,
    preferences: str = "",
) -> Backend:
    kind = (name or "mock").strip().lower()
    if kind == "mock":
        backend = MockBackend()
        backend.preferences = preferences
        return backend
    if kind == "model":
        if target_client is None:
            raise ValueError("ModelBackend requires target_client")
        return ModelBackend(
            target_client=target_client,
            optimizer_client=optimizer_client,
            preferences=preferences,
        )
    raise ValueError(f"unknown sleep backend {name!r}; expected mock|model")
