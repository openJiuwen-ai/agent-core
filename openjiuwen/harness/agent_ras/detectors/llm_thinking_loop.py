# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""LlmThinkingLoopDetector — aligned with ``thinking_loop_lock_rail.py`` (PR#3102).

Per stream type, maintain independent eval channels that never mix char/time
anchors across ``llm_output`` / ``llm_reasoning``:

  - **text_repetition** (L1/L2): runs on **both** ``llm_output`` and
    ``llm_reasoning`` via ``LoopDetector`` (suffix_cycle → similar_clauses),
    gated by ``detection_start_chars`` then ``window_max_chars`` since last scan
    (same value is the near-window FIFO size and min detect length).
  - **plan_execution** (L3): semantic Skill via ``RASAgents.invoke_skill``,
    runs on ``llm_reasoning`` and (temporarily) ``llm_output``, gated by
    ``detection_start_chars`` then incremental chars >= ``semantic_eval_chars``
    (default 10k).

``tool_calls.delta`` may still be emitted by ``ReActAgent`` (enterprise-dev
baseline), but this detector does not consume it — only visible assistant
``llm_output`` / ``llm_reasoning`` text is scanned.

``BEFORE_MODEL_CALL`` resets all channel state (per model_call isolation).
"""
from __future__ import annotations

import asyncio
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.harness.agent_ras.agents.base import (
    FAULT_DOMAIN_LLM_THINKING_LOOP,
    NoOpAgentAdapter,
    SKILL_TIMEOUT_SECONDS,
    skill_for,
)
from openjiuwen.harness.agent_ras.agents.ras_agents import RASAgents
from openjiuwen.harness.agent_ras.detectors.base import AsyncRecoveryHandler
from openjiuwen.harness.agent_ras.config import LlmThinkingLoopConfig
from openjiuwen.harness.agent_ras.models import (
    Anomaly,
    AnomalyKind,
    Severity,
    Signal,
    SignalKind,
)


# Visible assistant token streams scanned for dead-loop text (L1/L2).
STREAM_CHANNELS: tuple[str, ...] = (
    "llm_output",
    "llm_reasoning",
)

# L3 semantic / plan_execution: temporarily includes llm_output for models
# that do not emit a separate reasoning stream (e.g. deepseek-chat).
L3_STREAM_CHANNELS: frozenset[str] = frozenset({"llm_reasoning", "llm_output"})

# --- LoopDetector constants (thinking_loop_lock_rail.py) ---
MIN_CLAUSE_LEN = 5
SUFFIX_CYCLE_MIN_PATTERN_LEN = 10
SUFFIX_CYCLE_MAX_PATTERN_LEN = 150
SUFFIX_CYCLE_TAIL_WINDOW = 600
SIMILAR_CLAUSE_ENUM_TOP_RATIO = 0.7
SIMILAR_CLAUSE_ENUM_STRICT_RATIO = 0.85

_CODE_FENCE_RE = re.compile(r"```[\w]*\n.*?```", re.DOTALL)
_CLAUSE_SPLIT_RE = re.compile(r"[。！？\n]+")
_DIGIT_SKELETON_RE = re.compile(r"\d+")
_LEXICAL_TOKEN_RE = re.compile(r"[a-zA-Z]+|[\u4e00-\u9fff]")
_NORMALIZE_PREFIX_RE = re.compile(r"(^|\n)\s*(\d+[.)]\s*|step\s+[\dA-Z]+:\s*)")


@dataclass
class DetectionResult:
    detected: bool = False
    start_pos: int = 0
    mode: str = ""
    count: int = 0
    threshold: int = 0


def is_degenerate(text: str) -> bool:
    if not text:
        return True
    stripped = text.strip()
    if not stripped:
        return True
    unique_chars = set(stripped.replace(" ", "").replace("\n", "").replace("\t", ""))
    if len(unique_chars) <= 2:
        return True
    total = len(text)
    whitespace = sum(1 for c in text if c.isspace())
    if total > 0 and whitespace / total >= 0.7:
        return True
    return False


def _strip_code_fences(text: str) -> str:
    return _CODE_FENCE_RE.sub("", text)


def _extract_lexical_key(clause: str) -> str:
    if not clause:
        return ""
    lowered = clause.lower()
    tokens = _LEXICAL_TOKEN_RE.findall(lowered)
    return " ".join(tokens)


def _clause_lexical_char_len(clause: str) -> int:
    return len(_extract_lexical_key(clause).replace(" ", ""))


def _split_clauses(text: str) -> list[str]:
    cleaned = _strip_code_fences(text)
    parts = _CLAUSE_SPLIT_RE.split(cleaned)
    clauses: list[str] = []
    for part in parts:
        stripped = part.strip()
        if len(stripped) <= MIN_CLAUSE_LEN:
            continue
        if _clause_lexical_char_len(stripped) <= MIN_CLAUSE_LEN:
            continue
        clauses.append(stripped)
    return clauses


def _digit_skeleton(clause: str) -> str:
    return _DIGIT_SKELETON_RE.sub("#", clause)


def _is_enumeration_exempt(clauses: list[str]) -> bool:
    if len(clauses) < 3:
        return False
    skels = [_digit_skeleton(c) for c in clauses]
    cnt = Counter(skels)
    dominant_skel, dominant_count = cnt.most_common(1)[0]
    top_ratio = dominant_count / len(clauses)
    if top_ratio < SIMILAR_CLAUSE_ENUM_TOP_RATIO or "#" not in dominant_skel:
        return False
    nums: list[int] = []
    for clause in clauses:
        if _digit_skeleton(clause) != dominant_skel:
            continue
        found = _DIGIT_SKELETON_RE.findall(clause)
        if found:
            nums.append(int(found[0]))
    if len(nums) >= 3 and len(set(nums)) == len(nums):
        sorted_nums = sorted(nums)
        if sorted_nums[-1] - sorted_nums[0] == len(sorted_nums) - 1:
            return True
    return top_ratio >= SIMILAR_CLAUSE_ENUM_STRICT_RATIO


def _clause_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    key_a = _extract_lexical_key(a)
    key_b = _extract_lexical_key(b)
    if not key_a or not key_b:
        return 0.0
    if len(key_a.replace(" ", "")) <= MIN_CLAUSE_LEN or len(key_b.replace(" ", "")) <= MIN_CLAUSE_LEN:
        return 0.0
    return SequenceMatcher(None, key_a, key_b).ratio()


def _clause_positions_in_text(text: str, clauses: list[str]) -> list[int]:
    positions: list[int] = []
    search_from = 0
    for clause in clauses:
        idx = text.find(clause, search_from)
        if idx < 0:
            idx = search_from
        positions.append(idx)
        search_from = idx + max(1, len(clause))
    return positions


def normalize(text: str) -> str:
    if not text:
        return text
    try:
        text = text.lower()
        text = re.sub(r"[^\S\n]+", " ", text)
        text = _NORMALIZE_PREFIX_RE.sub(r"\1", text)
        return text
    except Exception:
        return text


def _strategy_suffix_cycle(
    text: str,
    threshold: int,
    tail: int = SUFFIX_CYCLE_TAIL_WINDOW,
    min_p: int = SUFFIX_CYCLE_MIN_PATTERN_LEN,
    max_p: int = SUFFIX_CYCLE_MAX_PATTERN_LEN,
) -> Optional[DetectionResult]:
    if len(text) < threshold * min_p:
        return None
    tail_text = text[-tail:]
    max_period = min(max_p, len(tail_text) // threshold)
    if max_period < min_p:
        return None
    for p_len in range(min_p, max_period + 1):
        pat = tail_text[-p_len:]
        if is_degenerate(pat):
            continue
        repeats = 0
        i = len(tail_text) - p_len
        while i >= 0:
            if tail_text[i:i + p_len] == pat:
                repeats += 1
                if repeats >= threshold:
                    break
                i -= p_len
            else:
                break
        if repeats >= threshold:
            offset_in_tail = len(tail_text) - repeats * p_len
            start_pos = max(0, len(text) - len(tail_text) + offset_in_tail)
            return DetectionResult(
                detected=True,
                start_pos=start_pos,
                mode="suffix_cycle",
                count=repeats,
                threshold=threshold,
            )
    return None


def _strategy_similar_clauses(
    text: str,
    threshold: int,
    sim_threshold: float,
) -> Optional[DetectionResult]:
    clauses = _split_clauses(text)
    if len(clauses) < threshold:
        return None
    if _is_enumeration_exempt(clauses):
        return None

    similar_indices: set[int] = set()
    clause_count = len(clauses)
    for i in range(clause_count):
        for j in range(i + 1, clause_count):
            if _clause_similarity(clauses[i], clauses[j]) >= sim_threshold:
                similar_indices.add(i)
                similar_indices.add(j)

    if len(similar_indices) < threshold:
        return None

    positions = _clause_positions_in_text(text, clauses)
    start_pos = min(positions[i] for i in similar_indices)
    return DetectionResult(
        detected=True,
        start_pos=start_pos,
        mode="similar_clauses",
        count=len(similar_indices),
        threshold=threshold,
    )


@dataclass
class LoopDetector:
    """Literal loop detector — suffix_cycle → similar_clauses (stateless)."""

    min_text_length: int = 80
    similar_clause_sim_threshold: float = 0.95
    _strategies: list = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        sim_th = self.similar_clause_sim_threshold
        self._strategies = [
            ("suffix_cycle", _strategy_suffix_cycle),
            (
                "similar_clauses",
                lambda text, threshold: _strategy_similar_clauses(
                    text, threshold, sim_threshold=sim_th,
                ),
            ),
        ]

    def detect(self, text: str, threshold: int = 5) -> DetectionResult:
        if not text or len(text) < self.min_text_length:
            return DetectionResult(detected=False, threshold=threshold)
        if is_degenerate(text):
            return DetectionResult(detected=False, threshold=threshold)
        norm = normalize(text)
        for _mode_name, strategy in self._strategies:
            try:
                result = strategy(norm, threshold)
            except Exception:
                logger.debug(
                    "[LoopDetector] strategy %s failed",
                    _mode_name,
                    exc_info=True,
                )
                result = None
            if result is not None:
                return result
        return DetectionResult(detected=False, threshold=threshold)


@dataclass
class _TextRepetitionChannel:
    buffer: str = ""
    total_length: int = 0
    last_scanned_pos: int = 0
    detection_latched: bool = False


@dataclass
class _PlanExecutionChannel:
    buffer: str = ""
    total_length: int = 0
    last_evaluated_pos: int = 0
    last_evaluated_at: float = field(default_factory=time.monotonic)
    model_call_start: float = field(default_factory=time.monotonic)
    eval_in_flight: bool = False
    detection_latched: bool = False


@dataclass
class _SemanticEvalJob:
    ch: _PlanExecutionChannel
    excerpt: str
    trigger_buffer_len: int
    chunk_type: str
    member: str
    generation: int


class LlmThinkingLoopDetector:
    """Dual-channel LLM thinking-loop detector aligned with ThinkingLoopLockRail."""

    def __init__(
        self,
        config: LlmThinkingLoopConfig | None = None,
        agents: RASAgents | None = None,
    ) -> None:
        self._config = config or LlmThinkingLoopConfig()
        self._agents = agents or RASAgents(NoOpAgentAdapter())
        self._text_rep: dict[str, _TextRepetitionChannel] = {}
        self._plan_exec: dict[str, _PlanExecutionChannel] = {}
        self._buffers: dict[str, tuple[str, int]] = {}
        self._async_recovery_handler: AsyncRecoveryHandler | None = None
        self._generation = 0
        self._eval_tasks: set[asyncio.Task] = set()

    @property
    def name(self) -> str:
        return "llm_thinking_loop"

    def set_async_recovery_handler(
        self,
        handler: AsyncRecoveryHandler | None,
    ) -> None:
        self._async_recovery_handler = handler

    def has_async_recovery_in_flight(self) -> bool:
        return self.has_eval_in_flight()

    async def await_async_recovery(self, timeout: float) -> None:
        await self.await_in_flight_evals(timeout)

    def release_async_recovery(self) -> None:
        self.release_eval_in_flight()

    def reset(self) -> None:
        self._generation += 1
        self._cancel_eval_tasks()
        self._text_rep.clear()
        self._plan_exec.clear()
        self._buffers.clear()

    def release_text_repetition_latch(self) -> None:
        """Allow L1/L2 re-detection after recovery normal in the same model call.

        Clears ``detection_latched`` and advances ``last_scanned_pos`` so the next
        scan requires ``window_max_chars`` of new text (avoids instant re-fire
        on the already-confirmed loop buffer).
        """
        for ch in self._text_rep.values():
            ch.detection_latched = False
            ch.last_scanned_pos = len(ch.buffer)

    def release_plan_execution_latch(self) -> None:
        """Allow L3 semantic re-detection after Reviewer normal / fail-open.

        Clears ``detection_latched`` / ``eval_in_flight`` and advances the eval
        cursor so the next skill invoke needs a fresh ``semantic_eval_chars``
        window of new text.
        """
        now = time.monotonic()
        for ch in self._plan_exec.values():
            ch.detection_latched = False
            ch.eval_in_flight = False
            ch.last_evaluated_pos = len(ch.buffer)
            ch.last_evaluated_at = now

    def release_after_recovery_normal(self) -> None:
        """Release L1/L2 and L3 latches after automatic recovery normal / fail-open."""
        self.release_text_repetition_latch()
        self.release_plan_execution_latch()

    # Backward-compatible alias used by older tests / callers.
    release_after_hitl_yes = release_after_recovery_normal

    def _cancel_eval_tasks(self) -> None:
        for task in list(self._eval_tasks):
            task.cancel()
        self._eval_tasks.clear()

    async def aclose(self) -> None:
        """Cancel and join semantic evaluation tasks."""
        tasks = list(self._eval_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._eval_tasks.clear()

    def has_eval_in_flight(self) -> bool:
        if self._eval_tasks:
            return True
        return any(ch.eval_in_flight for ch in self._plan_exec.values())

    def release_eval_in_flight(self) -> None:
        self._cancel_eval_tasks()
        for ch in self._plan_exec.values():
            ch.eval_in_flight = False

    async def await_in_flight_evals(self, timeout: float) -> None:
        """Wait for in-progress L3 skill invocations (used by AgentRASRail.after_invoke)."""
        if timeout <= 0:
            return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.has_eval_in_flight():
                return
            pending = [t for t in self._eval_tasks if not t.done()]
            if pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                done, _ = await asyncio.wait(
                    pending,
                    timeout=min(0.05, remaining),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in done:
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        logger.warning(
                            "[LlmThinkingLoopDetector] eval task failed "
                            "during await_in_flight_evals",
                            exc_info=True,
                        )
            else:
                await asyncio.sleep(0.05)

        if self.has_eval_in_flight():
            logger.warning(
                "[LlmThinkingLoopDetector] await_in_flight_evals timed out after %.1fs",
                timeout,
            )
            self.release_eval_in_flight()

    def _text_rep_channel(self, chunk_type: str) -> _TextRepetitionChannel:
        ch = self._text_rep.get(chunk_type)
        if ch is None:
            ch = _TextRepetitionChannel()
            self._text_rep[chunk_type] = ch
        return ch

    def _plan_exec_channel(self, chunk_type: str) -> _PlanExecutionChannel:
        ch = self._plan_exec.get(chunk_type)
        if ch is None:
            now = time.monotonic()
            ch = _PlanExecutionChannel(
                model_call_start=now,
                last_evaluated_at=now,
            )
            self._plan_exec[chunk_type] = ch
        return ch

    def _channel(self, chunk_type: str) -> _PlanExecutionChannel:
        return self._plan_exec_channel(chunk_type)

    @staticmethod
    def _trim_text_rep_buffer(
        buffer: str,
        max_chars: int,
        pos_markers: list[int],
    ) -> tuple[str, list[int]]:
        if max_chars <= 0 or len(buffer) <= max_chars:
            return buffer, pos_markers
        overflow = len(buffer) - max_chars
        buffer = buffer[overflow:]
        pos_markers = [max(0, p - overflow) for p in pos_markers]
        return buffer, pos_markers

    @staticmethod
    def _parse_skill_abnormal(result: Any) -> bool:
        if not isinstance(result, dict):
            return False
        return result.get("abnormal") is True

    async def observe(self, signal: Signal) -> Optional[Anomaly]:
        try:
            if signal.kind == SignalKind.STREAM_CHUNK:
                return await self._observe_stream(signal)
            if signal.kind == SignalKind.BEFORE_MODEL_CALL:
                self.reset()
                return None
            return None
        except Exception:
            logger.warning(
                "[LlmThinkingLoopDetector] observe failed member=%s kind=%s",
                signal.member_name,
                signal.kind,
                exc_info=True,
            )
            return None

    async def _observe_stream(self, signal: Signal) -> Optional[Anomaly]:
        chunk_type = signal.chunk_type
        if chunk_type not in STREAM_CHANNELS:
            return None
        text = signal.chunk_text or ""
        if not text:
            return None

        tr = self._text_rep_channel(chunk_type)
        run_l3 = chunk_type in L3_STREAM_CHANNELS
        pe = self._plan_exec_channel(chunk_type) if run_l3 else None

        if tr.detection_latched and (pe is None or pe.detection_latched):
            return None

        tr.buffer += text
        tr.total_length += len(text)
        tr.buffer, [tr.last_scanned_pos] = self._trim_text_rep_buffer(
            tr.buffer,
            self._config.window_max_chars,
            [tr.last_scanned_pos],
        )
        self._buffers[chunk_type] = (tr.buffer, tr.total_length)

        if pe is not None:
            pe.buffer += text
            pe.total_length += len(text)
            semantic_window = max(
                self._config.window_max_chars,
                self._config.semantic_eval_chars * 2,
            )
            if len(pe.buffer) > semantic_window:
                overflow = len(pe.buffer) - semantic_window
                pe.buffer = pe.buffer[overflow:]
                pe.last_evaluated_pos = max(0, pe.last_evaluated_pos - overflow)

        start_chars = self._config.detection_start_chars

        if (
            not tr.detection_latched
            and tr.total_length >= start_chars
        ):
            anomaly = self._run_text_repetition(
                tr, chunk_type, signal.member_name, text,
            )
            if anomaly is not None:
                return anomaly

        if (
            pe is not None
            and not pe.detection_latched
            and pe.total_length >= start_chars
        ):
            return await self._maybe_invoke_semantic_skill(
                pe, chunk_type, signal.member_name,
            )
        return None

    def _run_text_repetition(
        self,
        ch: _TextRepetitionChannel,
        chunk_type: str,
        member: str,
        last_chunk_text: str,
    ) -> Optional[Anomaly]:
        pending = len(ch.buffer) - ch.last_scanned_pos
        if pending < self._config.window_max_chars:
            return None

        ch.last_scanned_pos = len(ch.buffer)
        detector = LoopDetector(
            min_text_length=self._config.window_max_chars,
            similar_clause_sim_threshold=self._config.similar_clause_sim_threshold,
        )
        result = detector.detect(ch.buffer, threshold=self._config.loop_repeat_threshold)
        if not result.detected:
            return None

        ch.detection_latched = True
        severity = Severity.LOW if result.mode == "suffix_cycle" else Severity.MEDIUM
        chunk_start = len(ch.buffer) - len(last_chunk_text)
        keep_len = (
            max(0, result.start_pos - chunk_start)
            if result.start_pos > chunk_start
            else 0
        )
        return self._build_anomaly(
            AnomalyKind.LLM_THINKING_LOOP,
            severity,
            member,
            {
                "mode": result.mode,
                "channel": "text_repetition",
                "recovery_profile": "thinking_loop_text_rep",
                "count": result.count,
                "start_pos": result.start_pos,
                "threshold": result.threshold,
                "buffer_len": ch.total_length,
                "chunk_type": chunk_type,
                "scanned_text": ch.buffer,
                "stream_chunk_keep_len": keep_len,
            },
        )

    async def _maybe_invoke_semantic_skill(
        self,
        ch: _PlanExecutionChannel,
        chunk_type: str,
        member: str,
    ) -> Optional[Anomaly]:
        if not self._config.semantic_content_enabled or ch.eval_in_flight:
            return None

        incremental_len = len(ch.buffer) - ch.last_evaluated_pos
        if incremental_len < self._config.semantic_eval_chars:
            return None

        excerpt = ch.buffer[ch.last_evaluated_pos:]
        if not excerpt.strip():
            ch.last_evaluated_pos = len(ch.buffer)
            ch.last_evaluated_at = time.monotonic()
            return None

        ch.eval_in_flight = True
        trigger_buffer_len = len(ch.buffer)
        excerpt_copy = excerpt
        generation = self._generation
        task = asyncio.create_task(
            self._run_semantic_eval(
                _SemanticEvalJob(
                    ch=ch,
                    excerpt=excerpt_copy,
                    trigger_buffer_len=trigger_buffer_len,
                    chunk_type=chunk_type,
                    member=member,
                    generation=generation,
                )
            )
        )
        self._eval_tasks.add(task)
        task.add_done_callback(self._eval_tasks.discard)
        return None

    async def _run_semantic_eval(self, job: _SemanticEvalJob) -> None:
        ch = job.ch
        excerpt = job.excerpt
        trigger_buffer_len = job.trigger_buffer_len
        chunk_type = job.chunk_type
        member = job.member
        generation = job.generation
        if generation != self._generation:
            return
        abnormal = False
        skill_result: dict[str, Any] = {}
        detection_skill = skill_for(FAULT_DOMAIN_LLM_THINKING_LOOP, "detection")
        try:
            result = await self._agents.invoke_skill(
                role="detection",
                skill_name=detection_skill,
                payload=excerpt,
                timeout=float(SKILL_TIMEOUT_SECONDS),
            )
            if isinstance(result, dict):
                skill_result = result
            abnormal = self._parse_skill_abnormal(skill_result)
            if not abnormal:
                fail_reason = (
                    skill_result.get("fail_open_reason")
                    if isinstance(skill_result, dict)
                    else None
                )
                if not skill_result:
                    logger.warning(
                        "[LlmThinkingLoopDetector] semantic eval fail-open "
                        "member=%s skill=%s reason=empty_result",
                        member,
                        detection_skill,
                    )
                elif fail_reason:
                    logger.warning(
                        "[LlmThinkingLoopDetector] semantic eval fail-open "
                        "member=%s skill=%s reason=%s",
                        member,
                        detection_skill,
                        fail_reason,
                    )
                else:
                    logger.info(
                        "[LlmThinkingLoopDetector] semantic eval normal "
                        "member=%s skill=%s",
                        member,
                        detection_skill,
                    )
        except Exception:
            logger.warning(
                "[LlmThinkingLoopDetector] semantic evaluation failed member=%s",
                member,
                exc_info=True,
            )
            abnormal = False
        finally:
            stale = generation != self._generation
            if not stale and not abnormal:
                ch.eval_in_flight = False
                ch.last_evaluated_pos = trigger_buffer_len
                ch.last_evaluated_at = time.monotonic()

        if generation != self._generation:
            return

        if not abnormal:
            return

        ch.detection_latched = True
        primary_fault = str(skill_result.get("primary_fault") or "semantic_deadlock")
        anomaly = self._build_anomaly(
            AnomalyKind.LLM_THINKING_DEAD_LOOP,
            Severity.HIGH,
            member,
            {
                "mode": "plan_execution_loop_lock",
                "channel": "plan_execution",
                "recovery_profile": "thinking_loop_plan_exec",
                "chunk_type": chunk_type,
                "buffer_len": ch.total_length,
                "incremental_len": len(excerpt),
                "thinking_excerpt": excerpt,
                "scanned_text": ch.buffer,
                "skill_name": detection_skill,
                "fault_domain": FAULT_DOMAIN_LLM_THINKING_LOOP,
                "stream_chunk_keep_len": 0,
                "primary_fault": primary_fault,
                "skill_rationale": str(skill_result.get("rationale") or ""),
                "skill_confidence": skill_result.get("confidence"),
            },
        )
        handler = self._async_recovery_handler
        if handler is None:
            ch.eval_in_flight = False
            return
        try:
            await handler(anomaly, chunk_type)
        except Exception:
            logger.warning(
                "[LlmThinkingLoopDetector] async recovery failed member=%s",
                member,
                exc_info=True,
            )
            ch.eval_in_flight = False

    @staticmethod
    def _build_anomaly(
        kind: AnomalyKind,
        severity: Severity,
        member: str,
        evidence: dict[str, Any],
    ) -> Anomaly:
        return Anomaly(
            detector="llm_thinking_loop",
            kind=kind,
            severity=severity,
            member_name=member,
            summary=f"{kind.value} ({evidence.get('mode', '?')})",
            evidence=evidence,
        )
