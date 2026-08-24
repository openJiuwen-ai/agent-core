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
import contextvars
import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.harness.agent_ras.agents.base import (
    FAULT_DOMAIN_LLM_THINKING_LOOP,
    NoOpAgentAdapter,
    SKILL_TIMEOUT_SECONDS,
    resolve_skill,
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

_CODE_FENCE_RE = re.compile(r"```[\w]*\n.*?```", re.DOTALL)
_CLAUSE_SPLIT_RE = re.compile(r"[。！？\n]+")
# Delete separators (underscore, punctuation, whitespace) — do NOT replace with
# spaces — so xxx_0 → xxx0 and stays one identifier token after findall.
_NON_LEXICAL_CHAR_RE = re.compile(r"[^a-zA-Z0-9\u4e00-\u9fff]+")
# Keep digits glued to letters (p2, v1, xxx0); bare counts (2650字) stay out.
_LEXICAL_TOKEN_RE = re.compile(
    r"[a-zA-Z]+[0-9]*|[0-9]+[a-zA-Z]+|[\u4e00-\u9fff]+"
)
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
    compact = _NON_LEXICAL_CHAR_RE.sub("", lowered)
    tokens = _LEXICAL_TOKEN_RE.findall(compact)
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


def _clause_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    key_a = _extract_lexical_key(a)
    key_b = _extract_lexical_key(b)
    if not key_a or not key_b:
        return 0.0
    if (
        len(key_a.replace(" ", "")) <= MIN_CLAUSE_LEN
        or len(key_b.replace(" ", "")) <= MIN_CLAUSE_LEN
    ):
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


def _largest_similar_cluster(
    clauses: list[str],
    sim_threshold: float,
) -> list[int]:
    """Return indices of the largest connected component under pairwise similarity."""
    n = len(clauses)
    if n < 2:
        return []
    adj: list[set[int]] = [set() for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if _clause_similarity(clauses[i], clauses[j]) >= sim_threshold:
                adj[i].add(j)
                adj[j].add(i)

    seen: set[int] = set()
    best: list[int] = []
    for start in range(n):
        if start in seen or not adj[start]:
            continue
        stack = [start]
        seen.add(start)
        comp: list[int] = []
        while stack:
            u = stack.pop()
            comp.append(u)
            for v in adj[u]:
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        if len(comp) > len(best):
            best = comp
    return best


def _strategy_similar_clauses(
    text: str,
    threshold: int,
    sim_threshold: float,
) -> Optional[DetectionResult]:
    clauses = _split_clauses(text)
    if len(clauses) < threshold:
        return None

    # Count the largest similarity connected component (not the union of all
    # pairwise hits), so unrelated small pairs cannot accumulate to threshold.
    cluster = _largest_similar_cluster(clauses, sim_threshold)
    if len(cluster) < threshold:
        return None

    positions = _clause_positions_in_text(text, clauses)
    start_pos = min(positions[i] for i in cluster)
    return DetectionResult(
        detected=True,
        start_pos=start_pos,
        mode="similar_clauses",
        count=len(cluster),
        threshold=threshold,
    )


def _truncate_snippet(text: str, max_chars: int = 500) -> str:
    """Return up to max_chars of ``text``, appending ``…<truncated>`` if longer.

    Used to keep log messages readable while still including actionable
    excerpts of the offending loop text.
    """
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…<truncated>"


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
        # Log per-model-call stream char totals before clearing.
        if self._generation > 0 and (self._text_rep or self._plan_exec):
            tr_parts: list[str] = []
            for ct, ch in self._text_rep.items():
                tr_parts.append(
                    f"{ct}(total={ch.total_length} scanned={ch.last_scanned_pos}"
                    f" latched={ch.detection_latched})"
                )
            pe_parts: list[str] = []
            for ct, ch in self._plan_exec.items():
                pe_parts.append(
                    f"{ct}(total={ch.total_length} last_eval={ch.last_evaluated_pos}"
                    f" latched={ch.detection_latched} in_flight={ch.eval_in_flight})"
                )
            logger.info(
                "[LlmThinkingLoopDetector] model_call end generation=%d "
                "detection_start_chars=%d "
                "text_rep=[%s] plan_exec=[%s]",
                self._generation,
                self._config.detection_start_chars,
                " ".join(tr_parts) if tr_parts else "none",
                " ".join(pe_parts) if pe_parts else "none",
            )

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

        logger.warning(
            "[LlmThinkingLoopDetector] L1/L2 text repetition DETECTED "
            "member=%s chunk_type=%s mode=%s "
            "total_chars=%d buffer_chars=%d repeat_count=%d threshold=%d "
            "content_snippet=%s",
            member,
            chunk_type,
            result.mode,
            ch.total_length,
            len(ch.buffer),
            result.count,
            result.threshold,
            _truncate_snippet(ch.buffer),
        )

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
        logger.info(
            "[LlmThinkingLoopDetector] L3 semantic eval TASK_START "
            "member=%s chunk_type=%s "
            "total_chars=%d incremental_chars=%d semantic_eval_chars=%d "
            "excerpt_preview=%s",
            member,
            chunk_type,
            ch.total_length,
            incremental_len,
            self._config.semantic_eval_chars,
            _truncate_snippet(excerpt),
        )
        # Empty Context cuts parent write_stream ContextVars (_current_session,
        # tool_batch, cwd) so nested detection does not inherit host state.
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
            ),
            context=contextvars.Context(),
        )
        self._eval_tasks.add(task)
        task.add_done_callback(self._eval_tasks.discard)
        return None

    async def _run_semantic_eval(self, job: _SemanticEvalJob) -> None:
        t0 = time.monotonic()
        ch = job.ch
        excerpt = job.excerpt
        trigger_buffer_len = job.trigger_buffer_len
        chunk_type = job.chunk_type
        member = job.member
        generation = job.generation
        if generation != self._generation:
            logger.info(
                "[LlmThinkingLoopDetector] semantic eval aborted "
                "member=%s reason=stale_generation elapsed_ms=%d",
                member,
                int((time.monotonic() - t0) * 1000),
            )
            return
        abnormal = False
        skill_result: dict[str, Any] = {}
        detection_skill = resolve_skill(FAULT_DOMAIN_LLM_THINKING_LOOP, "detection")
        t1 = time.monotonic()
        try:
            result = await self._agents.invoke_skill(
                role="detection",
                skill_name=detection_skill,
                payload=excerpt,
                timeout=float(SKILL_TIMEOUT_SECONDS),
            )
            invoke_ms = int((time.monotonic() - t1) * 1000)
            if isinstance(result, dict):
                skill_result = result
            abnormal = self._parse_skill_abnormal(skill_result)
            total_ms = int((time.monotonic() - t0) * 1000)
            if not abnormal:
                fail_reason = (
                    skill_result.get("fail_open_reason")
                    if isinstance(skill_result, dict)
                    else None
                )
                if not skill_result:
                    logger.warning(
                        "[LlmThinkingLoopDetector] semantic eval fail-open "
                        "member=%s skill=%s reason=empty_result "
                        "total_ms=%d invoke_ms=%d excerpt_chars=%d",
                        member,
                        detection_skill,
                        total_ms,
                        invoke_ms,
                        len(excerpt),
                    )
                elif fail_reason:
                    logger.warning(
                        "[LlmThinkingLoopDetector] semantic eval fail-open "
                        "member=%s skill=%s reason=%s "
                        "total_ms=%d invoke_ms=%d excerpt_chars=%d",
                        member,
                        detection_skill,
                        fail_reason,
                        total_ms,
                        invoke_ms,
                        len(excerpt),
                    )
                else:
                    logger.info(
                        "[LlmThinkingLoopDetector] semantic eval normal "
                        "member=%s skill=%s "
                        "total_ms=%d invoke_ms=%d excerpt_chars=%d",
                        member,
                        detection_skill,
                        total_ms,
                        invoke_ms,
                        len(excerpt),
                    )
        except Exception as exc:
            total_ms = int((time.monotonic() - t0) * 1000)
            logger.warning(
                "[LlmThinkingLoopDetector] semantic evaluation failed "
                "member=%s error_type=%s total_ms=%d",
                member,
                type(exc).__name__,
                total_ms,
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
        logger.warning(
            "[LlmThinkingLoopDetector] L3 semantic ANOMALY DETECTED "
            "member=%s skill=%s "
            "total_chars=%d incremental_chars=%d "
            "primary_fault=%s confidence=%s "
            "thinking_excerpt=%s rationale=%s",
            member,
            detection_skill,
            ch.total_length,
            len(excerpt),
            primary_fault,
            skill_result.get("confidence"),
            _truncate_snippet(excerpt),
            str(skill_result.get("rationale") or "")[:200],
        )
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
