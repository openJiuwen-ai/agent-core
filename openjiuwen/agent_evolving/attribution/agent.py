# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Attribution agent: prompts, runtime protocol, context budget rail, factory.

The agent locates the nodes causing each diagnostic signal with the ten
read-only/report tools and must call ``check_attribution_complete`` until
``all_done=true``.  Trajectory access and result recording are provided by
the host through one :class:`AttributionAgentRuntime` per run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from openjiuwen.agent_evolving.attribution.model import (
    DiagnosticSignal,
    LlmRequestEvent,
    RewardOverall,
    StatusSink,
    SubjectRef,
)
from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.rail.base import AgentRail, ModelCallInputs

if TYPE_CHECKING:
    from openjiuwen.core.foundation.llm.model import Model
    from openjiuwen.harness.deep_agent import DeepAgent

_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"

_DEFAULT_CONTEXT_BUDGET_TOKENS = 3_000_000
_DEFAULT_MAX_ITERATIONS = 80
_DEFAULT_MAX_CONTEXT_MESSAGES = 100
_AGENT_CARD_ID = "attribution-agent"


def _usage_tokens(response: object | None) -> int:
    """Total tokens one model response reports, or 0 when it reports none."""
    if response is None:
        return 0
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return 0
    total = getattr(usage, "total_tokens", 0) or 0
    if total > 0:
        return int(total)
    inputs = getattr(usage, "input_tokens", 0) or 0
    outputs = getattr(usage, "output_tokens", 0) or 0
    return int(inputs + outputs)


class ContextBudgetRail(AgentRail):
    """Bill provider-reported tokens and force-finish past the budget.

    A force-finish (not an abort) keeps whatever the tools already recorded.
    The host reads :attr:`exceeded` to mark the run ``failed: tokens exceeded``.
    """

    priority = 950

    def __init__(self, budget_tokens: int = _DEFAULT_CONTEXT_BUDGET_TOKENS, reason_prefix: str = "attribution") -> None:
        super().__init__()
        if budget_tokens <= 0:
            raise ValueError("budget_tokens must be greater than zero")
        self._budget = budget_tokens
        self._reason_prefix = reason_prefix
        self.spent = 0
        self.exceeded = False

    def _finish_payload(self, when: str) -> dict[str, Any]:
        return {
            "reason": f"{self._reason_prefix} context budget exceeded ({self.spent}/{self._budget}, {when})",
            "status": "truncated",
        }

    async def before_model_call(self, ctx: Any) -> None:
        if self.exceeded:
            ctx.request_force_finish(self._finish_payload("refused"))

    async def after_model_call(self, ctx: Any) -> None:
        inputs = ctx.inputs
        if not isinstance(inputs, ModelCallInputs):
            return
        tokens = _usage_tokens(inputs.response)
        if tokens > 0:
            self.spent += tokens
        if self.spent > self._budget:
            if not self.exceeded:
                self.exceeded = True
                logger.warning(
                    "[%s] context budget exceeded (%s/%s); finishing agent",
                    self._reason_prefix,
                    self.spent,
                    self._budget,
                )
            ctx.request_force_finish(self._finish_payload("exceeded"))


def _response_text(response: Any) -> str:
    content = getattr(response, "content", None)
    if content is None and isinstance(response, dict):
        content = response.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("content"), str):
                parts.append(part["content"])
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts)
    return ""


def _response_tool_names(response: Any) -> list[str]:
    tool_calls = getattr(response, "tool_calls", None)
    if tool_calls is None and isinstance(response, dict):
        tool_calls = response.get("tool_calls")
    names: list[str] = []
    for call in tool_calls or ():
        name = getattr(call, "name", None) or getattr(getattr(call, "function", None), "name", None)
        if name is None and isinstance(call, dict):
            name = call.get("name") or call.get("function", {}).get("name")
        if name:
            names.append(str(name))
    return names


class LlmStatusRail(AgentRail):
    """Report request-level LLM status events (running -> terminal) to a sink."""

    priority = 940

    def __init__(
        self,
        sink: StatusSink,
        *,
        stage: str,
        agent_id: str,
        model: str,
        counter: list[int],
    ) -> None:
        super().__init__()
        self._sink = sink
        self._stage = stage
        self._agent_id = agent_id
        self._model = model
        self._counter = counter
        self._last_request_id: str | None = None
        self._event_started: dict[str, int] = {}
        self._event_iterations: dict[str, int | None] = {}

    def _request_id(self) -> str:
        self._counter[0] += 1
        return f"llm-{self._counter[0]}"

    async def before_model_call(self, ctx: Any) -> None:
        inputs = ctx.inputs
        iteration = inputs.react_iteration if isinstance(inputs, ModelCallInputs) else None
        event = LlmRequestEvent(
            request_id=self._request_id(),
            stage=self._stage,
            call_kind="agent",
            status="running",
            started_at_ns=_now_ns(),
            agent_id=self._agent_id,
            iteration=iteration,
            model=self._model,
        )
        self._last_request_id = event.request_id
        self._event_started[event.request_id] = event.started_at_ns
        self._event_iterations[event.request_id] = iteration
        try:
            self._sink.report(event)
        except Exception:  # noqa: BLE001 - status loss must not break the run
            logger.debug("[%s] status sink raised; ignored", self._stage)

    async def after_model_call(self, ctx: Any) -> None:
        inputs = ctx.inputs
        if not isinstance(inputs, ModelCallInputs):
            return
        request_id = self._last_request_id
        if request_id is None:
            return
        self._last_request_id = None
        response = inputs.response
        usage = getattr(response, "usage_metadata", None)
        token_usage: dict[str, Any] = {}
        if usage is not None:
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                value = getattr(usage, key, 0) or 0
                if value:
                    token_usage[key] = int(value)
        completed = LlmRequestEvent(
            request_id=request_id,
            stage=self._stage,
            call_kind="agent",
            status="succeeded",
            started_at_ns=self._event_started.get(request_id, _now_ns()),
            ended_at_ns=_now_ns(),
            agent_id=self._agent_id,
            iteration=self._event_iterations.pop(request_id, None),
            model=self._model,
            response_preview=_response_text(response)[:100] or None,
            tool_calls=_response_tool_names(response),
            token_usage=token_usage,
        )
        try:
            self._sink.report(completed)
        except Exception:  # noqa: BLE001 - status loss must not break the run
            logger.debug("[%s] status sink raised; ignored", self._stage)

    async def on_model_exception(self, ctx: Any) -> None:
        request_id = self._last_request_id
        if request_id is None:
            return
        self._last_request_id = None
        error = getattr(ctx, "error", None) or "model call failed"
        failed = LlmRequestEvent(
            request_id=request_id,
            stage=self._stage,
            call_kind="agent",
            status="failed",
            started_at_ns=self._event_started.get(request_id, _now_ns()),
            ended_at_ns=_now_ns(),
            agent_id=self._agent_id,
            iteration=self._event_iterations.pop(request_id, None),
            model=self._model,
            error=str(error)[:200],
        )
        try:
            self._sink.report(failed)
        except Exception:  # noqa: BLE001 - status loss must not break the run
            logger.debug("[%s] status sink raised; ignored", self._stage)


def _now_ns() -> int:
    import time

    return time.time_ns()


class AttributionAgentRuntime(Protocol):
    """Host-provided trajectory access and result sink for one run."""

    def subjects(self) -> list[SubjectRef]:
        """Return the subjects available for attribution."""
        ...

    def search_keyword(self, keyword: str) -> dict[str, Any]:
        """Search the trajectory for a keyword."""
        ...

    def render_trajectory(self, subagent: str) -> dict[str, Any]:
        """Render one subagent's trajectory."""
        ...

    def render_partial_trajectory(self, from_id: str, to_id: str) -> dict[str, Any]:
        """Render the trajectory between two nodes."""
        ...

    def render_node_detail(self, node_id: str) -> dict[str, Any]:
        """Render one node's details."""
        ...

    def render_node_field(self, node_id: str, field: str, start: int, limit: int) -> dict[str, Any]:
        """Render a character window of one node field."""
        ...

    def record_attribution(self, signal_id: str, entries: list[dict[str, Any]]) -> str:
        """Record attribution entries for a signal."""
        ...

    def mark_signal_invalid(self, signal_id: str) -> str:
        """Mark a signal as invalid."""
        ...

    def record_artifact_improvement(self, md: str) -> str:
        """Record Markdown advice for improving the artifact."""
        ...

    def record_skill_improvement(self, key: str, md: str) -> str:
        """Record Markdown advice for improving a skill."""
        ...

    def completion_overview(self) -> dict[str, Any]:
        """Return the attribution completion state."""
        ...

    def on_progress(self) -> None:
        """Notify the host that attribution has progressed."""
        ...


def _normalize_language(language: str) -> str:
    return "en" if str(language).strip().lower() == "en" else "zh"


_prompt_cache: dict[str, str] = {}


def _load_prompt(filename: str) -> str:
    if filename not in _prompt_cache:
        _prompt_cache[filename] = (_PROMPT_DIR / filename).read_text(encoding="utf-8")
    return _prompt_cache[filename]


def build_attribution_system_prompt(language: str = "zh", swarm_mode: bool = False) -> str:
    """Assemble the system prompt; the swarm addendum is appended when enabled."""
    lang = _normalize_language(language)
    prompt = _load_prompt(f"{lang}.md")
    if swarm_mode:
        prompt = f"{prompt}\n\n{_load_prompt(f'swarm_{lang}.md')}"
    return prompt


def render_signal_block(signals: list[DiagnosticSignal]) -> str:
    """One JSON object per diagnostic signal, verbatim and untranslated."""
    return "\n".join(json.dumps(signal.to_dict(), ensure_ascii=False) for signal in signals)


def render_subjects_block(subjects: list[SubjectRef]) -> str:
    """One line per participating subagent."""
    return "\n".join(f"- {subject.id} | {subject.display_name} | {subject.kind}" for subject in subjects)


def build_attribution_initial_prompt(
    *,
    task_description: str,
    reward_overall: RewardOverall | None,
    signals: list[DiagnosticSignal],
    subjects: list[SubjectRef],
    language: str = "zh",
    review_deliverables: bool = True,
) -> str:
    """Assemble the initial user prompt template (task, reward, signals, subjects).

    ``review_deliverables=False`` (no deliverable was reviewed: the selection holds none's final version)
    adds a scope note: analyse the process only and submit no artifact improvement.
    """
    lang = _normalize_language(language)
    reward_block = (
        f"- 总分：{reward_overall.total_score}\n- 总评：{reward_overall.comment}"
        if reward_overall is not None
        else "（本次无 Reward 总评）"
    )
    if lang == "en":
        reward_block = (
            f"- Total score: {reward_overall.total_score}\n- Comment: {reward_overall.comment}"
            if reward_overall is not None
            else "(no reward overall this run)"
        )
    if lang == "en":
        return (
            "## Task Description\n"
            f"{task_description}\n\n"
            "## Reward Overall\n"
            f"{reward_block}\n\n"
            "## Diagnostic Signals (one JSON per line, verbatim, not translated)\n"
            f"{render_signal_block(signals) or '(none)'}\n\n"
            "## Subagent List (subagent_id = subagent_key; display_name is the display name; kind is the type)\n"
            f"{render_subjects_block(subjects)}\n\n"
            "Process every diagnostic signal above: attribute it (submit_attribution) or mark it "
            "invalid (mark_signal_invalid); submit the artifact/task-execution improvement and, when "
            "applicable, skill improvements; finally call check_attribution_complete until all_done=true."
            + (
                ""
                if review_deliverables
                else "\n\n## Scope\nThe selection holds no final version of a deliverable, so no deliverable "
                "was reviewed: analyse the process only (tool errors, environment, efficiency, how skills were "
                "used) and do not call submit_artifact_improvement; check_attribution_complete does not ask for it."
            )
        )
    return (
        "## 任务描述\n"
        f"{task_description}\n\n"
        "## Reward 总评\n"
        f"{reward_block}\n\n"
        "## 待归因诊断信号（每行一条 JSON，原文未翻译）\n"
        f"{render_signal_block(signals) or '（无）'}\n\n"
        "## subagent 列表（subagent_id = subagent_key；display_name 为展示名；kind 为类型）\n"
        f"{render_subjects_block(subjects)}\n\n"
        "请逐条处理上述诊断信号：定位归因（submit_attribution）或标记无效（mark_signal_invalid）；"
        "提交产物/任务执行改进意见与（如适用）技能改进意见；最后调用 check_attribution_complete，"
        "直到 all_done=true。"
        + (
            ""
            if review_deliverables
            else "\n\n## 本次范围\n框选中没有交付物的最终版本，本次没有评估交付物：只分析过程问题（工具报错、环境、"
            "效率、Skill 用法），不要调用 submit_artifact_improvement；check_attribution_complete 不会要求它。"
        )
    )


def build_wake_text(check_json: dict[str, Any], language: str = "zh") -> str:
    """Assemble the one-shot wake user-turn text from a check result."""
    block = json.dumps(check_json, ensure_ascii=False)
    if _normalize_language(language) == "en":
        return (
            "The previous run ended naturally, but the completion check did not pass. "
            f"check_attribution_complete returned:\n\n{block}\n\n"
            "Please finish the remaining items:\n"
            "- For each signal in pending_signal_ids: complete its attribution via submit_attribution, "
            "or mark it invalid via mark_signal_invalid;\n"
            "- If missing_artifact_improvement=true: submit the artifact improvement via "
            "submit_artifact_improvement;\n"
            "- Then call check_attribution_complete again until all_done=true; at the end, echo that "
            "JSON verbatim."
        )
    return (
        "上一轮运行已自然结束，但完成度检查未通过。check_attribution_complete 返回：\n\n"
        f"{block}\n\n"
        "请继续完成未汇报项：\n"
        "- pending_signal_ids 中的每条信号：用 submit_attribution 完成定位归因，或 mark_signal_invalid 标记无效；\n"
        "- 若 missing_artifact_improvement=true：调用 submit_artifact_improvement 补交改进意见；\n"
        "- 完成后再次调用 check_attribution_complete，直到 all_done=true；结束时原样复述该 JSON。"
    )


def build_attribution_agent(
    *,
    model: "Model",
    signals: list[DiagnosticSignal],
    runtime: AttributionAgentRuntime,
    task_description: str,
    reward_overall: RewardOverall | None,
    language: str = "zh",
    swarm_mode: bool = False,
    total_token_budget: int = _DEFAULT_CONTEXT_BUDGET_TOKENS,
    max_iterations: int = _DEFAULT_MAX_ITERATIONS,
    max_context_messages: int = _DEFAULT_MAX_CONTEXT_MESSAGES,
    status_sink: StatusSink | None = None,
    model_name: str = "",
    status_counter: list[int] | None = None,
) -> tuple["DeepAgent", ContextBudgetRail]:
    """Build the attribution agent and its budget rail.

    Only the ten custom tools are registered; task loop, subagents, mcps and
    skills stay off.  The host reads ``rail.exceeded`` after ``invoke``.
    """
    from openjiuwen.agent_evolving.attribution.tools import build_attribution_tools
    from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard
    from openjiuwen.harness.factory import create_deep_agent

    rail = ContextBudgetRail(total_token_budget)
    try:
        from openjiuwen.harness.observability import maybe_agent_observability_rail

        observability_rail = maybe_agent_observability_rail()
    except ImportError:
        observability_rail = None
        logger.warning("[attribution] observability rail unavailable; run spans disabled")
    rails: list[Any] = [rail]
    if observability_rail is not None:
        rails.append(observability_rail)
    if status_sink is not None:
        rails.append(
            LlmStatusRail(
                status_sink,
                stage="attribution",
                agent_id=_AGENT_CARD_ID,
                model=model_name,
                counter=status_counter if status_counter is not None else [0],
            )
        )
    agents = create_deep_agent(
        model,
        card=AgentCard(
            id=_AGENT_CARD_ID,
            name="AttributionAgent",
            description="Trajectory attribution agent / 轨迹归因 Agent",
        ),
        system_prompt=build_attribution_system_prompt(language, swarm_mode=swarm_mode),
        tools=list(
            build_attribution_tools(runtime, [signal.signal_id for signal in signals]),  # type: ignore[arg-type]
        ),
        rails=rails,
        max_iterations=max_iterations,
        context_engine_config=ContextEngineConfig(max_context_message_num=max_context_messages),
        enable_security_rail=False,
        enable_model_anomaly_detection_rail=False,
        enable_sys_operation=False,
    )
    return agents, rail


__all__ = [
    "AttributionAgentRuntime",
    "ContextBudgetRail",
    "LlmStatusRail",
    "build_attribution_agent",
    "build_attribution_initial_prompt",
    "build_attribution_system_prompt",
    "build_wake_text",
    "render_signal_block",
    "render_subjects_block",
]
