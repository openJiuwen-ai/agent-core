# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Success detection for the TTSE fail path (blame gating).

A :class:`SuccessDetector` decides ``success`` / ``partial`` / ``fail`` / ``skip``
so :class:`TTSERail` can gate induce and the blame -> retire -> synthesize pass.

Default production detector is :class:`SignalBasedSuccessDetector`:
  0. external ``ttse_score`` (if present) -> success/partial/fail (benchmark bypass)
  1. execution_failure signal -> partial (induce, no blame); short-circuits user_intent
  2. user_intent feedback (only when no execution_failure) -> partial
  3. tool_calls (prefer snapshot ``ttse_invoke_tool_calls``, else messages)
     < detect_min_tool_calls -> skip (Judge not run)
  4. extract write/edit output_paths (if any; logged only); always proceed to Judge
  5. one Judge LLM on query + final_reply -> success|partial|fail

:class:`TrajectoryErrorSuccessDetector` remains for trajectory-error defaults / test injection.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, List, Optional

from openjiuwen.agent_evolving.optimizer.llm_resilience import (
    LLMInvokePolicy,
    invoke_text_with_retry,
)
from openjiuwen.agent_evolving.signal.from_conv import (
    ConversationSignalDetector,
    detect_tool_error_signals,
)
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model

from .config import TTSEConfig
from .prompts import detect_judge_prompt
from .trajectory_adapter import (
    count_tool_calls,
    extract_final_reply,
    extract_output_paths,
)

_VALID_OUTCOMES = frozenset({"success", "partial", "fail"})


@dataclass(frozen=True)
class SuccessOutcome:
    """The outcome of a task: ``success`` / ``partial`` / ``fail`` / ``skip`` + score."""

    outcome: str
    score: float
    reason: str = ""


class SuccessDetector(ABC):
    """Decide task success to gate the TTSE blame/synthesize pass."""

    @abstractmethod
    async def detect(
        self,
        trajectory: Any,
        messages: Any,
        *,
        ctx: Any = None,
        snapshot: Optional[dict] = None,
    ) -> SuccessOutcome:
        raise NotImplementedError


def _explicit_score(ctx: Any, snapshot: Optional[dict]) -> Optional[float]:
    """Look for an externally-provided score (grader/harness)."""
    value: Any = None
    if snapshot:
        value = snapshot.get("ttse_score")
    if value is None and ctx is not None:
        value = getattr(ctx, "ttse_score", None)
        if value is None:
            inputs = getattr(ctx, "inputs", None)
            value = getattr(inputs, "ttse_score", None) if inputs is not None else None
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def classify_explicit_score(score: float, *, success_threshold: float) -> str:
    """Map a grader score onto ``success`` / ``partial`` / ``fail``.

    ``>= success_threshold`` success; ``(0, success_threshold)`` partial; ``0`` fail.
    """
    if score >= success_threshold:
        return "success"
    if score > 0:
        return "partial"
    return "fail"


def _outcome_from_explicit_score(score: float, threshold: float) -> SuccessOutcome:
    """Map an external grader score to success/partial/fail."""
    return SuccessOutcome(
        classify_explicit_score(score, success_threshold=threshold),
        score,
        "explicit-score",
    )


def _trajectory_has_error(trajectory: Any) -> bool:
    """True if any recorded trajectory step carries an error."""
    if trajectory is None:
        return False
    steps = getattr(trajectory, "steps", None) or []
    for step in steps:
        if getattr(step, "error", None):
            return True
        detail = getattr(step, "detail", None)
        if detail is not None and getattr(detail, "error", None):
            return True
    return False


class TrajectoryErrorSuccessDetector(SuccessDetector):
    """Explicit-score-if-present, else trajectory-error scan.

    Kept for grader-driven benches and test injection. Not the rail default.

    * An explicit score wins when provided: >= ``success_threshold`` -> success,
      ``> 0`` -> partial, ``0`` -> fail.
    * Otherwise a recorded error on any trajectory step -> fail.
    * Otherwise success.
    """

    def __init__(self, success_threshold: float = TTSEConfig().success_threshold) -> None:
        self.success_threshold = success_threshold

    async def detect(
        self,
        trajectory: Any,
        messages: Any,
        *,
        ctx: Any = None,
        snapshot: Optional[dict] = None,
    ) -> SuccessOutcome:
        score = _explicit_score(ctx, snapshot)
        if score is not None:
            return _outcome_from_explicit_score(score, self.success_threshold)
        if _trajectory_has_error(trajectory):
            return SuccessOutcome("fail", 0.0, "trajectory-error")
        return SuccessOutcome("success", 1.0, "no-error-default")


def _task_query(ctx: Any, snapshot: Optional[dict], messages: Any) -> str:
    if snapshot:
        q = snapshot.get("ttse_task_query")
        if q:
            return str(q)
    if ctx is not None:
        inputs = getattr(ctx, "inputs", None)
        q = getattr(inputs, "query", None) or getattr(inputs, "retrieval_query", None)
        if q:
            return str(q)
    for raw in reversed(messages or []):
        role = raw.get("role") if isinstance(raw, dict) else getattr(raw, "role", None)
        if role != "user":
            continue
        content = raw.get("content") if isinstance(raw, dict) else getattr(raw, "content", "")
        if content:
            return str(content)
    return ""


def _has_execution_failure(
    trajectory: Any,
    messages: Any,
    *,
    signal_detector: Optional[ConversationSignalDetector],
) -> bool:
    try:
        if signal_detector is not None:
            signals = signal_detector.detect_trajectory_signals(
                trajectory,
                messages=messages,
                signal_types={"execution_failure", "script_artifact"},
            )
        else:
            msgs = messages
            if msgs is None and trajectory is not None:
                msgs = ConversationSignalDetector.convert_trajectory_to_messages(trajectory)
            signals = detect_tool_error_signals(msgs or [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("[TTSERail] detect execution_failure failed: %s", exc)
        return False
    return any(getattr(s, "signal_type", "") == "execution_failure" for s in signals or [])


async def _has_user_intent_feedback(
    messages: Any,
    *,
    llm: Model,
    model: str,
    signal_detector: Optional[Any] = None,
    language: str = "cn",
) -> bool:
    """True when passive user feedback yields ``user_intent`` signal(s).

    Prefer an injected detector that exposes ``detect_user_intent``. When a detector
    is injected without that method, treat as no feedback (test fakes). Otherwise
    build a bound :class:`ConversationSignalDetector`.
    """
    if signal_detector is not None:
        if not hasattr(signal_detector, "detect_user_intent"):
            return False
        detector = signal_detector
    else:
        detector = ConversationSignalDetector()
    bind = getattr(detector, "bind_llm", None)
    if callable(bind):
        detector = bind(llm=llm, model=model, language=language)
    try:
        signals = await detector.detect_user_intent(list(messages or []))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[TTSERail] detect user_intent failed: %s", exc)
        return False
    return bool(signals)


def _parse_judge_json(raw: str) -> Optional[dict]:
    text = (raw or "").strip()
    if not text:
        return None
    candidates = [text]
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())
    braced = re.search(r"\{[\s\S]*\}", text)
    if braced:
        candidates.append(braced.group(0))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return None


def _outcome_from_judge(data: dict) -> Optional[SuccessOutcome]:
    outcome = str(data.get("outcome") or "").strip().lower()
    if outcome not in _VALID_OUTCOMES:
        return None
    reason = str(data.get("reason") or "judge").strip() or "judge"
    score = {"success": 1.0, "partial": 0.5, "fail": 0.0}[outcome]
    return SuccessOutcome(outcome, score, f"judge:{reason}")


class SignalBasedSuccessDetector(SuccessDetector):
    """Default TTSE detector: external score bypass, failure/feedback fast-path, reply Judge.

    * External ``ttse_score`` (snapshot/ctx) wins first for benchmarks.
    * ``execution_failure`` (deterministic tool-output rules) -> ``partial``
      (runs before the tool-call gate).
    * Else ``user_intent`` feedback (skill-agnostic) -> ``partial``; skipped when
      ``execution_failure`` already matched.
    * Tool-call gate uses snapshot ``ttse_invoke_tool_calls`` when present
      (per-invoke), else ``count_tool_calls(messages)``; below min -> ``skip``
      (Judge not run).
    * Extract write/edit ``output_path`` list (logged; not fed to Judge).
    * One Judge LLM on query + final_reply.
    """

    def __init__(
        self,
        *,
        llm: Model,
        model: str,
        config: Optional[TTSEConfig] = None,
        signal_detector: Optional[ConversationSignalDetector] = None,
        detect_llm_policy: Optional[LLMInvokePolicy] = None,
        language: str = "cn",
    ) -> None:
        self._llm = llm
        self._model = model
        self._config = config or TTSEConfig()
        self._signal_detector = signal_detector
        self._policy = detect_llm_policy or self._config.detect_llm_policy
        self._language = language

    async def detect(
        self,
        trajectory: Any,
        messages: Any,
        *,
        ctx: Any = None,
        snapshot: Optional[dict] = None,
    ) -> SuccessOutcome:
        logger.info("[TTSERail] detect start detector=SignalBasedSuccessDetector")
        score = _explicit_score(ctx, snapshot)
        if score is not None:
            logger.info(
                "[TTSERail] detect branch=explicit_score score=%s threshold=%s outcome=%s",
                score,
                self._config.success_threshold,
                (
                    "success"
                    if score >= self._config.success_threshold
                    else "partial"
                    if score > 0
                    else "fail"
                ),
            )
            return _outcome_from_explicit_score(score, self._config.success_threshold)

        msgs: List[Any] = list(messages or [])

        logger.info("[TTSERail] detect checking execution_failure signals")
        if _has_execution_failure(trajectory, msgs, signal_detector=self._signal_detector):
            logger.info("[TTSERail] detect branch=partial_execution_failure")
            return SuccessOutcome("partial", 0.5, "signal:execution_failure")

        logger.info("[TTSERail] detect checking user_intent feedback")
        if await _has_user_intent_feedback(
            msgs,
            llm=self._llm,
            model=self._model,
            signal_detector=self._signal_detector,
            language=self._language,
        ):
            logger.info("[TTSERail] detect branch=partial_user_intent")
            return SuccessOutcome("partial", 0.5, "signal:user_intent")

        invoke_n = None
        if snapshot is not None:
            raw_n = snapshot.get("ttse_invoke_tool_calls")
            if isinstance(raw_n, int):
                invoke_n = raw_n
        if invoke_n is not None:
            n_calls = invoke_n
            gate_source = "invoke"
        else:
            n_calls = count_tool_calls(msgs)
            gate_source = "messages"
        logger.info(
            "[TTSERail] detect tool_calls=%s min=%s gate=%s source=%s",
            n_calls,
            self._config.detect_min_tool_calls,
            "pass" if n_calls >= self._config.detect_min_tool_calls else "fail",
            gate_source,
        )
        if n_calls < self._config.detect_min_tool_calls:
            logger.info(
                "[TTSERail] detect branch=skip_tool_calls tool_calls=%s min=%s source=%s",
                n_calls,
                self._config.detect_min_tool_calls,
                gate_source,
            )
            return SuccessOutcome(
                "skip",
                0.0,
                f"gate:tool_calls={n_calls}<{self._config.detect_min_tool_calls}",
            )

        paths = extract_output_paths(msgs, max_paths=self._config.detect_max_output_paths)
        query = _task_query(ctx, snapshot, msgs)
        final_reply = extract_final_reply(
            msgs,
            max_chars=self._config.detect_final_reply_chars,
        )
        logger.info(
            "[TTSERail] detect branch=judge_llm query_len=%s reply_len=%s n_paths=%s paths=%s",
            len(query or ""),
            len(final_reply or ""),
            len(paths),
            paths,
        )
        logger.info("[TTSERail] detect final_reply=%s", final_reply or "(empty)")
        prompt = detect_judge_prompt(query, final_reply)
        try:
            raw = await invoke_text_with_retry(
                self._llm,
                self._model,
                prompt,
                policy=self._policy,
                temperature=0.0,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[TTSERail] detect Judge LLM failed: %s", exc)
            return SuccessOutcome("skip", 0.0, f"judge_error:{exc}")

        data = _parse_judge_json(raw)
        if data is None:
            logger.warning("[TTSERail] detect Judge JSON parse failed")
            return SuccessOutcome("skip", 0.0, "judge_bad_json")
        parsed = _outcome_from_judge(data)
        if parsed is None:
            logger.warning("[TTSERail] detect Judge outcome invalid: %s", data.get("outcome"))
            return SuccessOutcome("skip", 0.0, "judge_bad_outcome")
        logger.info(
            "[TTSERail] detect branch=judge outcome=%s reason=%s",
            parsed.outcome,
            parsed.reason,
        )
        return parsed


__all__ = [
    "SuccessDetector",
    "SuccessOutcome",
    "TrajectoryErrorSuccessDetector",
    "SignalBasedSuccessDetector",
    "classify_explicit_score",
]
