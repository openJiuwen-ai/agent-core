# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""High-level entry points: run_diagnosis and run_attribution.

``run_task_parse`` lives in :mod:`openjiuwen.agent_evolving.attribution.task_parse`
and is re-exported here.  The host injects IO (LLM, Jev, model, workspace,
status sink); everything else stays in core.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from openjiuwen.agent_evolving.attribution.agent import (
    AttributionAgentRuntime,
    ContextBudgetRail,
    build_attribution_agent,
    build_attribution_initial_prompt,
    build_wake_text,
)
from openjiuwen.agent_evolving.attribution.events import emit_single_llm_event
from openjiuwen.agent_evolving.attribution.keyword_detect import SIGNAL_TEXT_LIMIT, run_keyword_diagnosis
from openjiuwen.agent_evolving.attribution.model import (
    AttributionRunOutcome,
    DiagnosisResult,
    DiagnosticSignal,
    RewardOverall,
    SignalSource,
    StatusSink,
    SubjectRef,
    TaskParseResult,
    new_attr_id,
)
from openjiuwen.agent_evolving.attribution.nodes import TrajectoryNode, search_keyword
from openjiuwen.agent_evolving.attribution.reward_stage.reward_adapter import reward_enabled, run_reward
from openjiuwen.agent_evolving.attribution.reward_stage.reward_agent import build_reward_agent, load_reward_prompt
from openjiuwen.agent_evolving.attribution.reward_stage.reward_schema import RewardResult, reward_to_signals
from openjiuwen.agent_evolving.attribution.task_parse import TaskParseLLM, run_task_parse
from openjiuwen.agent_evolving.attribution.tools import (
    artifact_improvement_required,
    render_partial_view,
    render_trajectory_view,
)
from openjiuwen.core.common.logging import logger

_REWARD_TEMP_DIRNAME = ".reward_temp"


class NodeBackedRuntime:
    """Snapshot-backed :class:`AttributionAgentRuntime` for tests and hosts.

    Recording goes to in-memory stores the caller can read after the run;
    a host that persists results can either read them or pass its own
    runtime implementation instead.
    """

    def __init__(self, nodes_by_subagent: dict[str, list[TrajectoryNode]], language: str = "zh") -> None:
        self._nodes = nodes_by_subagent
        self._language = language
        self._index: dict[str, TrajectoryNode] = {}
        self.attributions: dict[str, list[dict[str, Any]]] = {}
        self.invalid_signals: set[str] = set()
        self.artifact_improvement: str | None = None
        self.artifact_improvement_required = True
        self.skill_improvements: dict[str, str] = {}
        for nodes in nodes_by_subagent.values():
            for node in nodes:
                self._index[node.node_id] = node

    def subjects(self) -> list[SubjectRef]:
        return [
            SubjectRef(id=subagent, display_name=nodes[0].display_name, kind=nodes[0].kind)
            for subagent, nodes in sorted(self._nodes.items())
            if nodes
        ]

    def search_keyword(self, keyword: str) -> dict[str, Any]:
        return search_keyword(self._nodes, keyword)

    def render_trajectory(self, subagent: str) -> dict[str, Any]:
        nodes = self._nodes.get(subagent)
        if nodes is None:
            return {"error": f"subagent not found: {subagent}"}
        return render_trajectory_view(nodes, self._language)

    def render_partial_trajectory(self, from_id: str, to_id: str) -> dict[str, Any]:
        subagent = from_id.rpartition(":")[0]
        nodes = self._nodes.get(subagent)
        if nodes is None:
            return {"error": f"subagent not found: {subagent}"}
        return render_partial_view(nodes, from_id, to_id, self._language)

    def render_node_detail(self, node_id: str) -> dict[str, Any]:
        node = self._index.get(node_id)
        if node is None:
            return {"error": f"node not found: {node_id}"}
        from openjiuwen.agent_evolving.attribution.nodes import render_node_detail as render_detail

        return render_detail(node)

    def render_node_field(self, node_id: str, field: str, start: int, limit: int) -> dict[str, Any]:
        node = self._index.get(node_id)
        if node is None:
            return {"error": f"node not found: {node_id}"}
        from openjiuwen.agent_evolving.attribution.nodes import render_node_field as render_field

        return render_field(node, field, start, limit)

    def record_attribution(self, signal_id: str, entries: list[dict[str, Any]]) -> str:
        status = "updated" if signal_id in self.attributions else "recorded"
        stored: list[dict[str, Any]] = []
        for entry in entries:
            copy = dict(entry)
            if not copy.get("attr_id"):
                copy["attr_id"] = new_attr_id()
            stored.append(copy)
        self.attributions[signal_id] = stored
        return status

    def mark_signal_invalid(self, signal_id: str) -> str:
        self.invalid_signals.add(signal_id)
        return "marked"

    def record_artifact_improvement(self, md: str) -> str:
        status = "updated" if self.artifact_improvement is not None else "recorded"
        self.artifact_improvement = md
        return status

    def record_skill_improvement(self, key: str, md: str) -> str:
        status = "updated" if key in self.skill_improvements else "recorded"
        self.skill_improvements[key] = md
        return status

    def completion_overview(self) -> dict[str, Any]:
        return {
            "attributed_signal_ids": sorted(self.attributions),
            "invalid_signal_ids": sorted(self.invalid_signals),
            "artifact_improvement_submitted": self.artifact_improvement is not None,
        }

    def on_progress(self) -> None:
        return None


@dataclass
class _MemoryRewardSink:
    payload: dict[str, Any] | None = None

    def submit(self, payload: dict[str, Any]) -> str:
        status = "updated" if self.payload is not None else "recorded"
        self.payload = dict(payload)
        return status


def _usage_from_response(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    result: dict[str, Any] = {}
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if isinstance(usage, dict):
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            try:
                result[key] = int(usage[key])
            except (KeyError, TypeError, ValueError):
                continue
    return result


def build_reward_user_prompt(
    *,
    task_description: str,
    message_artifacts: list[tuple[str, str]],
    file_artifacts: list[str],
    signals: list[DiagnosticSignal],
    user_attribution: str,
    language: str = "zh",
    user_request: str = "",
) -> str:
    """Assemble the Reward stage user prompt (P5); ``user_request`` (the user's own words) leads it."""
    if language == "en":
        message_block = "\n".join(f"- {node_id}: {text}" for node_id, text in message_artifacts) or "(none)"
        file_block = "\n".join(f"- {path}" for path in file_artifacts) or "(none)"
        signal_block = "\n".join(json.dumps(signal.to_dict(), ensure_ascii=False) for signal in signals) or "(none)"
        request_block = (
            f"## The User's Own Request (verbatim; the task is judged against it)\n{user_request}\n\n"
            if user_request
            else ""
        )
        return (
            request_block + "## Task Description\n"
            f"{task_description}\n\n"
            "## Task Message Artifacts ((node_id, text) pairs)\n"
            f"{message_block}\n\n"
            "## Task File Artifacts (absolute paths in the workspace)\n"
            f"{file_block}\n\n"
            "## Process Diagnostic Signals (one JSON per line, verbatim, not translated)\n"
            f"{signal_block}\n\n"
            "## User Supplementary Notes\n"
            f"{user_attribution or '(none)'}\n\n"
            "Inspect the artifacts above and complete the evaluation, then call submit_reward to "
            "submit the result."
        )
    message_block = "\n".join(f"- {node_id}: {text}" for node_id, text in message_artifacts) or "（无）"
    file_block = "\n".join(f"- {path}" for path in file_artifacts) or "（无）"
    signal_block = "\n".join(json.dumps(signal.to_dict(), ensure_ascii=False) for signal in signals) or "（无）"
    request_block = f"## 用户本人的请求（原文，按它评价）\n{user_request}\n\n" if user_request else ""
    return (
        request_block + "## 任务描述\n"
        f"{task_description}\n\n"
        "## 任务消息产物（(node_id, 文本) 对）\n"
        f"{message_block}\n\n"
        "## 任务文件产物（workspace 内绝对路径）\n"
        f"{file_block}\n\n"
        "## 过程诊断信号（每行一条 JSON，原文未翻译）\n"
        f"{signal_block}\n\n"
        "## 用户补充信息\n"
        f"{user_attribution or '（无）'}\n\n"
        "请检查上述产物并完成评价，完成后调用 submit_reward 提交结果。"
    )


def _rule_jev_result(
    signals: list[DiagnosticSignal],
    run_meta: dict[str, Any],
    user_attribution: str,
) -> DiagnosisResult:
    """Assemble the Reward-skipped outcome.

    When Reward did not consume the user note, the note degrades into an
    ``uncertain``/``user`` diagnostic signal appended after the rule/Jev
    signals so the attribution stage still receives it.  A blank note adds
    nothing.
    """
    note = (user_attribution or "").strip()
    if note:
        run_meta["user_note_signal"] = True
        signals = [
            *signals,
            DiagnosticSignal.create(
                text=note,
                source=SignalSource.USER,
                tier="uncertain",
                node_ids=[],
                metadata={"kind": "user_note"},
            ),
        ]
    return DiagnosisResult(
        reward_overall=None,
        signals=signals,
        signal_mode="rule_jev",
        run_meta=run_meta,
    )


async def run_diagnosis(
    *,
    run_id: str,
    language: str,
    nodes_by_subagent: dict[str, list[TrajectoryNode]],
    user_attribution: str,
    task_parse: TaskParseResult,
    workspace: str | None = None,
    keyword_mode: str = "rule",
    jev_client: Any = None,
    jev_threshold: float = 0.8,
    jev_concurrency: int = 8,
    model: Any = None,
    reward_card: Any = None,
    reward_budget_tokens: int = 600_000,
    reward_max_iterations: int = 32,
    status_sink: StatusSink | None = None,
    model_name: str = "",
    status_counter: list[int] | None = None,
) -> DiagnosisResult:
    """Run stage two: keyword signals, then Reward when artifacts exist.

    Reward success switches the signals to reward dimensions
    (``signal_mode="reward"``); a skip or failure falls back to the
    rule/Jev signals (``signal_mode="rule_jev"``).  On every fallback a
    non-empty user note degrades into an ``uncertain``/``user`` signal so
    the attribution stage still receives it as one of its inputs.
    """
    del run_id
    counter = status_counter if status_counter is not None else [0]
    keyword_started = _now_ms()
    signals, keyword_meta = await run_keyword_diagnosis(
        nodes_by_subagent,
        keyword_mode,
        jev_client,
        jev_threshold=jev_threshold,
        jev_concurrency=jev_concurrency,
    )
    emit_single_llm_event(
        status_sink,
        stage="keyword_diag",
        model=model_name,
        counter=counter,
        started_at_ns=keyword_started,
        ended_at_ns=_now_ms(),
        token_usage={},
    )
    run_meta: dict[str, Any] = {"keyword": keyword_meta}
    if task_parse.user_feedback is not None and keyword_meta.get("mode") == "rule":
        signals, run_meta["user_messages_not_feedback"] = _keep_user_feedback(signals, task_parse.user_feedback)
    has_artifacts = bool(task_parse.message_artifacts or task_parse.file_artifacts)
    if not has_artifacts:
        run_meta["reward"] = {"skipped": "no artifacts"}
        return _rule_jev_result(signals, run_meta, user_attribution)
    if model is None:
        run_meta["reward"] = {"skipped": "no model injected"}
        return _rule_jev_result(signals, run_meta, user_attribution)
    if reward_enabled():
        try:
            payload, reward_meta = await run_reward(
                task_parse=task_parse,
                nodes_by_subagent=nodes_by_subagent,
                user_attribution=user_attribution,
                model=model,
                language=language,
                status_sink=status_sink,
                model_name=model_name,
                status_counter=counter,
            )
            reward = RewardResult.from_payload(payload)
        except Exception as exc:  # noqa: BLE001 - fallback to rule/Jev signals, as for the Reward agent
            logger.warning("[attribution] Reward failed: %s", exc)
            run_meta["reward"] = {"engine": "reward", "failed": type(exc).__name__, "error": str(exc)[:200]}
            return _rule_jev_result(signals, run_meta, user_attribution)
        run_meta["reward"] = reward_meta
        return _reward_diagnosis(reward, signals, run_meta, tiers=[d["tier"] for d in payload["dimensions"]])
    sink = _MemoryRewardSink()
    prepare_reward_temp_dir(workspace)
    agent, rail, gate = build_reward_agent(
        model=model,
        card=reward_card,
        system_prompt=load_reward_prompt(language),
        workspace=workspace or ".",
        language=language,
        context_budget_tokens=reward_budget_tokens,
        max_iterations=reward_max_iterations,
        reward_sink=sink,
        status_sink=status_sink,
        model_name=model_name,
        status_counter=counter,
    )
    try:
        await agent.invoke(
            build_reward_user_prompt(
                task_description=task_parse.task_description,
                message_artifacts=task_parse.message_artifacts,
                file_artifacts=task_parse.file_artifacts,
                signals=signals,
                user_attribution=user_attribution,
                language=language,
                user_request=task_parse.user_request,
            )
        )
    except Exception as exc:  # noqa: BLE001 - fallback to rule/Jev signals
        run_meta["reward"] = {"failed": type(exc).__name__, "error": str(exc)[:200]}
        return _rule_jev_result(signals, run_meta, user_attribution)
    run_meta["reward"] = {
        "submitted": gate.submitted,
        "budget_exceeded": rail.exceeded,
        "tokens": rail.spent,
    }
    if not gate.submitted or sink.payload is None:
        return _rule_jev_result(signals, run_meta, user_attribution)
    reward = RewardResult.from_payload(sink.payload)
    return _reward_diagnosis(reward, signals, run_meta)


def _keep_user_feedback(signals: list[DiagnosticSignal], feedback: list[str]) -> tuple[list[DiagnosticSignal], int]:
    """Drop the user's follow-up messages task parsing judged not to be feedback (a new request, an answer).

    Returns the kept signals and how many user messages were dropped.
    """
    texts = {text[:SIGNAL_TEXT_LIMIT] for text in feedback}
    kept = [signal for signal in signals if signal.source != SignalSource.KEYWORD_USER or signal.text in texts]
    return kept, len(signals) - len(kept)


def _reward_diagnosis(
    reward: RewardResult,
    signals: list[DiagnosticSignal],
    run_meta: dict[str, Any],
    tiers: list[str] | None = None,
) -> DiagnosisResult:
    """Reward dimensions as signals, followed by the process signals no dimension took up.

    A process signal (a failed tool call, the user's follow-up message; see
    :func:`~openjiuwen.agent_evolving.attribution.keyword_detect.detect_rule_signals`) is kept
    beside the reward signals: the deliverable can be good while the run still went wrong somewhere.
    ``tiers`` (one per dimension) replace the score-band tiers when the Reward judged each claim itself.
    """
    reward_signals = reward_to_signals(reward, signals)
    if tiers is not None:
        reward_signals = [replace(signal, tier=tier) for signal, tier in zip(reward_signals, tiers, strict=True)]
    if not reward_signals:
        return DiagnosisResult(
            reward_overall=None,
            signals=signals,
            signal_mode="rule_jev",
            run_meta=run_meta,
        )
    taken = {signal_id for dimension in reward.dimensions for signal_id in dimension.related_signal_ids}
    kept = [signal for signal in signals if signal.signal_id not in taken and signal.tier != "perfect"]
    run_meta["process_signals_kept"] = len(kept)
    return DiagnosisResult(
        reward_overall=reward.overall,
        signals=reward_signals + kept,
        signal_mode="reward",
        run_meta=run_meta,
        reward_payload=reward.to_payload(),
    )


def prepare_reward_temp_dir(workspace: str | None) -> None:
    """Create an empty ``<workspace>/.reward_temp/`` scratch dir for the Reward run.

    The Reward prompt points its temporary scripts/intermediate files at this
    directory; clearing it first keeps one run from seeing a previous run's
    leftovers.  Failure is logged but never fatal — the agent can still run.
    """
    effective = workspace or "."
    try:
        temp_dir = Path(effective) / _REWARD_TEMP_DIRNAME
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.warning("[reward] failed to prepare %s; continuing", Path(effective) / _REWARD_TEMP_DIRNAME)


def _now_ms() -> int:
    import time

    return time.time_ns()


def _overview_all_done(runtime: AttributionAgentRuntime, signal_ids: list[str]) -> tuple[bool, dict[str, Any]]:
    overview = runtime.completion_overview()
    attributed = set(overview.get("attributed_signal_ids") or ())
    invalid = set(overview.get("invalid_signal_ids") or ())
    artifact_submitted = bool(overview.get("artifact_improvement_submitted")) or not (
        artifact_improvement_required(runtime)
    )
    pending = sorted(set(signal_ids) - attributed - invalid)
    if not pending and artifact_submitted:
        return True, {"all_done": True}
    return False, {
        "all_done": False,
        "pending_signal_ids": pending,
        "missing_artifact_improvement": not artifact_submitted,
    }


async def run_attribution(
    *,
    model: Any,
    nodes_by_subagent: dict[str, list[TrajectoryNode]],
    signals: list[DiagnosticSignal],
    task_description: str,
    reward_overall: RewardOverall | None = None,
    language: str = "zh",
    swarm_mode: bool = False,
    runtime: AttributionAgentRuntime | None = None,
    status_sink: StatusSink | None = None,
    model_name: str = "",
    status_counter: list[int] | None = None,
    total_token_budget: int = 3_000_000,
    max_iterations: int = 80,
    max_context_messages: int = 100,
    total_timeout_s: float | None = 600.0,
    session: Any = None,
) -> AttributionRunOutcome:
    """Run stage three with the one-shot wake (proposal §5.4).

    The host may pass its own runtime and session; otherwise a
    :class:`NodeBackedRuntime` and a fresh agent session are used.  The 600s
    timeout covers both invoke rounds when ``total_timeout_s`` is set.

    ``tier == "perfect"`` signals never enter this stage: they are filtered
    here (proposal §4.5) and reported via ``skipped_signal_ids`` so the host
    can surface the skip in state.
    """
    active_signals = [signal for signal in signals if signal.tier != "perfect"]
    skipped_signal_ids = sorted(signal.signal_id for signal in signals if signal.tier == "perfect")
    if not active_signals:
        return AttributionRunOutcome(
            status="success",
            completed=True,
            skipped_signal_ids=skipped_signal_ids,
            counts={"attributed": 0, "invalid": 0, "artifact": 0, "skill": 0},
            token_usage={"attribution": 0},
        )
    active_runtime = runtime or NodeBackedRuntime(nodes_by_subagent, language)
    # a deliverable was reviewed only when the Reward scored one: without it there is nothing to improve
    review_deliverables = any(signal.source == SignalSource.REWARD for signal in signals)
    active_runtime.artifact_improvement_required = review_deliverables
    agent, rail = build_attribution_agent(
        model=model,
        signals=active_signals,
        runtime=active_runtime,
        task_description=task_description,
        reward_overall=reward_overall,
        language=language,
        swarm_mode=swarm_mode,
        total_token_budget=total_token_budget,
        max_iterations=max_iterations,
        max_context_messages=max_context_messages,
        status_sink=status_sink,
        model_name=model_name,
        status_counter=status_counter,
    )
    from openjiuwen.core.session.agent import create_agent_session

    active_session = session if session is not None else create_agent_session()
    initial_prompt = build_attribution_initial_prompt(
        task_description=task_description,
        reward_overall=reward_overall,
        signals=active_signals,
        subjects=active_runtime.subjects(),
        language=language,
        review_deliverables=review_deliverables,
    )

    def outcome(completed: bool, wake_attempted: bool) -> AttributionRunOutcome:
        counts = {
            "attributed": len(active_runtime.completion_overview().get("attributed_signal_ids") or ()),
            "invalid": len(active_runtime.completion_overview().get("invalid_signal_ids") or ()),
            "artifact": 1 if active_runtime.completion_overview().get("artifact_improvement_submitted") else 0,
            "skill": 0,
        }
        if isinstance(active_runtime, NodeBackedRuntime):
            counts["skill"] = len(active_runtime.skill_improvements)
        if rail.exceeded:
            return AttributionRunOutcome(
                status="failed",
                failure_kind="tokens_exceeded",
                completed=completed,
                pending_signal_ids=_pending(active_runtime, active_signals),
                skipped_signal_ids=skipped_signal_ids,
                wake_attempted=wake_attempted,
                counts=counts,
                token_usage={"attribution": rail.spent},
            )
        if completed:
            return AttributionRunOutcome(
                status="success",
                completed=True,
                skipped_signal_ids=skipped_signal_ids,
                wake_attempted=wake_attempted,
                counts=counts,
                token_usage={"attribution": rail.spent},
            )
        return AttributionRunOutcome(
            status="failed",
            failure_kind="incomplete",
            completed=False,
            pending_signal_ids=_pending(active_runtime, active_signals),
            skipped_signal_ids=skipped_signal_ids,
            wake_attempted=wake_attempted,
            counts=counts,
            token_usage={"attribution": rail.spent},
        )

    loop = asyncio.get_running_loop()
    deadline = loop.time() + total_timeout_s if total_timeout_s else None

    async def invoke_with_timeout(prompt: str) -> bool:
        remaining = deadline - loop.time() if deadline is not None else None
        if remaining is not None and remaining <= 0:
            raise asyncio.TimeoutError
        try:
            await asyncio.wait_for(
                agent.invoke(prompt, session=active_session),
                timeout=remaining,
            )
        except asyncio.TimeoutError:
            raise
        except Exception:
            if rail.exceeded:
                return True
            raise
        return True

    try:
        await invoke_with_timeout(initial_prompt)
        all_done, check = _overview_all_done(active_runtime, [signal.signal_id for signal in active_signals])
        if all_done:
            return outcome(True, False)
        if deadline is not None and deadline - loop.time() <= 0:
            return outcome(False, False)
        wake_text = build_wake_text(check, language)
        await invoke_with_timeout(wake_text)
        all_done, _ = _overview_all_done(active_runtime, [signal.signal_id for signal in active_signals])
        return outcome(all_done, True)
    except asyncio.TimeoutError:
        return AttributionRunOutcome(
            status="failed",
            failure_kind="timeout",
            completed=False,
            pending_signal_ids=_pending(active_runtime, active_signals),
            skipped_signal_ids=skipped_signal_ids,
            wake_attempted=False,
            counts={},
            token_usage={"attribution": rail.spent},
        )
    except Exception as exc:  # noqa: BLE001 - surface the error to the host
        # The outcome carries the detail back to the host, but the traceback
        # belongs in the log: this branch runs inside a deep-agent loop where
        # exceptions can carry an empty message, so log it with full context.
        logger.exception(
            "[attribution] run failed with an exception: model=%s signals=%d",
            model_name or "-",
            len(active_signals),
        )
        return AttributionRunOutcome(
            status="failed",
            failure_kind="error",
            error_detail=str(exc)[:300],
            completed=False,
            pending_signal_ids=_pending(active_runtime, active_signals),
            skipped_signal_ids=skipped_signal_ids,
            wake_attempted=False,
            counts={},
            token_usage={"attribution": rail.spent},
        )


def _pending(runtime: AttributionAgentRuntime, signals: list[DiagnosticSignal]) -> list[str]:
    overview = runtime.completion_overview()
    attributed = set(overview.get("attributed_signal_ids") or ())
    invalid = set(overview.get("invalid_signal_ids") or ())
    return sorted({signal.signal_id for signal in signals} - attributed - invalid)


__all__ = [
    "NodeBackedRuntime",
    "build_reward_user_prompt",
    "run_attribution",
    "run_diagnosis",
    "run_task_parse",
    "TaskParseLLM",
    "ContextBudgetRail",
]
