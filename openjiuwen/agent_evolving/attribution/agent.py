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
import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from openjiuwen.agent_evolving.attribution.language import output_language_directive
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

_DEFAULT_CONTEXT_BUDGET_TOKENS: int | None = None  # no token limit: tokens are only counted
_DEFAULT_MAX_ITERATIONS = 80
_DEFAULT_MAX_CONTEXT_MESSAGES = 100
_AGENT_CARD_ID = "attribution-agent"


def without_time_limits(model: Any) -> Any:
    """A copy of ``model`` whose client has no HTTP, first-chunk or idle time limit; the caller's model is untouched.

    The limits are the ones the Reward evidence agent runs with (``NO_TIME_LIMITS``), own HTTP client included: a
    shared client is reused by endpoint and key and keeps the timeout it was built with.  Anything that is not a core
    ``Model`` with a client config (a test double) is returned as it is.
    """
    from openjiuwen.agent_evolving.reward.evidence_agent import NO_TIME_LIMITS
    from openjiuwen.core.foundation.llm.model import Model

    client = getattr(model, "model_client_config", None)
    if not isinstance(model, Model) or client is None:
        return model
    return Model(model_client_config=client.model_copy(update=NO_TIME_LIMITS), model_config=model.model_config)


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
    """Bill provider-reported tokens and force-finish past the budget (``None``: count only, never finish).

    A force-finish (not an abort) keeps whatever the tools already recorded.
    The host reads :attr:`exceeded` to mark the run ``failed: tokens exceeded``.
    """

    priority = 950

    def __init__(
        self, budget_tokens: int | None = _DEFAULT_CONTEXT_BUDGET_TOKENS, reason_prefix: str = "attribution"
    ) -> None:
        super().__init__()
        if budget_tokens is not None and budget_tokens <= 0:
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
        if self._budget is not None and self.spent > self._budget:
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

    def mark_signal_topic(
        self,
        signal_id: str,
        category: str,
        skill_key: str | None = None,
        relation: str | None = None,
    ) -> str:
        """Mark a signal as belonging to a skill or experience topic."""
        ...

    def record_reward_failure(self, signal_id: str, entries: list[dict[str, Any]]) -> str:
        """Record located causes for one reward-source signal."""
        ...

    def record_skill_cause(self, signal_id: str, entries: list[dict[str, Any]]) -> str:
        """Record located causes for one skill-marked signal."""
        ...

    def record_experience_cause(self, signal_id: str, entries: list[dict[str, Any]]) -> str:
        """Record located causes for one experience-marked signal."""
        ...

    def mark_signal_invalid(self, signal_id: str) -> str:
        """Mark a signal as invalid."""
        ...

    def record_artifact_improvement(self, md: str) -> str:
        """Record Markdown advice for improving the artifact (experimental path)."""
        ...

    def record_skill_evolution(self, key: str, md: str, confidence: float = 0.5) -> str:
        """Record Markdown advice for improving a skill."""
        ...

    def record_experience_evolution(self, outcome: str, suggestions: list[str]) -> str:
        """Record the experience verdict of this diagnosis."""
        ...

    def record_trajectory_summary(self, summary: str) -> str:
        """Record the trajectory narrative."""
        ...

    def skill_keys(self) -> set[str]:
        """Return the Skill keys usable by this run (the prompt inventory)."""
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


_SKILL_TOOLS = frozenset(
    {
        "submit_skill_evolution",
        "submit_skill_evolution_cause",
    }
)
_EXPERIENCE_TOOLS = frozenset(
    {
        "submit_experience_evolution",
        "submit_experience_cause",
    }
)
_MARK_TOOL = "mark_signal_topic"
_BLOCK_PATTERN = re.compile(r"<!--\s*(\w+):start\s*-->\n?(.*?)<!--\s*\1:end\s*-->\n?", re.S)


def filter_attribution_tools(
    tools: list[Any],
    *,
    skill_evolution: bool = True,
    ttse_advice: bool = True,
) -> list[Any]:
    """Drop the tools of a disabled switch (and the mark tool when both are off)."""
    dropped: set[str] = set()
    if not skill_evolution:
        dropped |= _SKILL_TOOLS
    if not ttse_advice:
        dropped |= _EXPERIENCE_TOOLS
    if not skill_evolution and not ttse_advice:
        dropped.add(_MARK_TOOL)
    return [tool for tool in tools if getattr(tool, "tool_name", "") not in dropped]


def filter_skill_evolution_tool(tools: list[Any], *, enabled: bool = True) -> list[Any]:
    """Backward-compatible wrapper: drop only the skill evolution tools when disabled."""
    return filter_attribution_tools(tools, skill_evolution=enabled, ttse_advice=True)


def _strip_named_blocks(prompt: str, disabled: set[str]) -> str:
    """Remove the optional prompt blocks of disabled switches."""

    def replace(match: re.Match[str]) -> str:
        return "" if match.group(1) in disabled else match.group(2)

    return _BLOCK_PATTERN.sub(replace, prompt)


def _renumber_goals(prompt: str) -> str:
    """Close up the numbered goal list after optional goals were removed."""
    lines = prompt.splitlines()
    try:
        start = next(
            index for index, line in enumerate(lines) if line.strip().startswith(("# 目标", "# Goals"))
        )
    except StopIteration:
        return prompt
    number = 0
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.startswith("# "):
            break
        match = re.match(r"^\d+\. (.*)$", line)
        if match:
            number += 1
            lines[index] = f"{number}. {match.group(1)}"
    return "\n".join(lines)


def build_attribution_system_prompt(
    language: str = "zh",
    swarm_mode: bool = False,
    skill_evolution: bool = True,
    ttse_advice: bool = True,
) -> str:
    """Assemble the system prompt; disabled switches lose their optional blocks."""
    lang = _normalize_language(language)
    prompt = _load_prompt(f"{lang}.md")
    if swarm_mode:
        prompt = f"{prompt}\n\n{_load_prompt(f'swarm_{lang}.md')}"
    disabled: set[str] = set()
    if not skill_evolution:
        disabled.add("skill_evolution")
    if not ttse_advice:
        disabled.add("experience")
    if disabled:
        prompt = _renumber_goals(_strip_named_blocks(prompt, disabled))
    return f"{prompt}\n\n{output_language_directive(lang)}"


def render_signal_block(signals: list[DiagnosticSignal], premarks: dict[str, list[str]] | None = None) -> str:
    """One JSON object per diagnostic signal, verbatim and untranslated.

    ``premarks`` (signal id -> skill keys) appends a rule hint to that line; it
    never creates an obligation.
    """
    rows: list[str] = []
    for signal in signals:
        data = signal.to_dict()
        keys = (premarks or {}).get(signal.signal_id)
        if keys:
            data["rule_premark"] = list(keys)
        rows.append(json.dumps(data, ensure_ascii=False))
    return "\n".join(rows)


def render_subjects_block(subjects: list[SubjectRef]) -> str:
    """One line per participating subagent."""
    return "\n".join(f"- {subject.id} | {subject.display_name} | {subject.kind}" for subject in subjects)


def render_skill_block(usages: list[Any]) -> str:
    """One line per used skill: its key and the node it first appeared in."""
    return "\n".join(f"- {usage.skill_key}（最早调用节点 {usage.first_node_id}）" for usage in usages)


def build_attribution_initial_prompt(
    *,
    task_description: str,
    reward_overall: RewardOverall | None,
    signals: list[DiagnosticSignal],
    subjects: list[SubjectRef],
    language: str = "zh",
    review_deliverables: bool = True,
    skill_evolution: bool = True,
    ttse_advice: bool = True,
    skill_usages: list[Any] | None = None,
    premarks: dict[str, list[str]] | None = None,
) -> str:
    """Assemble the initial user prompt (task, reward, signals, subjects, Skill list).

    ``skill_usages`` (rule-detected, optional) adds the Skill list section and
    the constraint that global skill advice must come from it.  ``premarks``
    append per-signal hints only.  Disabled switches drop their sections.
    """
    lang = _normalize_language(language)
    usages = list(skill_usages or ()) if skill_evolution else []
    if lang == "en":
        reward_block = (
            f"- Total score: {reward_overall.total_score}\n- Comment: {reward_overall.comment}"
            if reward_overall is not None
            else "(no reward overall this run)"
        )
        skill_section = (
            "\n\n## Skills Used by This Trajectory\n"
            f"{render_skill_block(usages)}\n"
            "`submit_skill_evolution` and `mark_signal_topic(category=\"skill\")` must use a name "
            "from this list."
            if usages
            else ""
        )
        experience_section = (
            "\n\n## Experience (TTSE) Hints\n"
            "- Tool execution failed -> outcome is at least `partial`;\n"
            "- A user negative-feedback signal exists -> `partial`;\n"
            "- Fewer than 5 tool calls -> do not submit an experience entry.\n"
            "Decide first, mark second: call `mark_signal_topic(category=\"experience\")` only when "
            "you will distill an experience; if you change your mind, undo the mark with "
            "`mark_signal_invalid`."
            if ttse_advice
            else ""
        )
        marked = skill_evolution or ttse_advice
        route_extra = (
            "- Signal marked skill/experience -> its `submit_*_cause` tool; unmarked rule signals are "
            "not submitted (their conclusion goes into `submit_trajectory_summary`).\n"
            if marked
            else ""
        )
        order = (
            "Work order: read with `view_*` -> judge and `mark_signal_topic` -> directed cause tools -> "
            "global advice tools -> `submit_trajectory_summary` -> `check_attribution_complete` until it "
            "returns all_done=true. The mark of a signal must precede its cause; interleaving is fine."
            if marked
            else "Work order: read with `view_*` -> directed cause tools -> `submit_trajectory_summary` -> "
            "`check_attribution_complete` until it returns all_done=true."
        )
        return (
            "## Task Description\n"
            f"{task_description}\n\n"
            "## Reward Overall\n"
            f"{reward_block}\n\n"
            "## Diagnostic Signals (one JSON per line, verbatim, not translated)\n"
            f"{render_signal_block(signals, premarks) or '(none)'}\n\n"
            "## Subagent List (subagent_id = subagent_key; display_name is the display name; kind is the type)\n"
            f"{render_subjects_block(subjects)}"
            f"{skill_section}"
            f"{experience_section}\n\n"
            "## Routing and order\n"
            "Route every signal exactly as follows:\n"
            "- Reward-source signal -> `submit_reward_failure` (or `mark_signal_invalid`);\n"
            f"{route_extra}"
            f"{order}"
            + (
                ""
                if review_deliverables
                else "\n\n## Scope\nThe selection holds no final version of a deliverable, so no deliverable "
                "was reviewed: analyse the process only (tool errors, environment, efficiency, how skills were "
                "used) and do not call submit_artifact_improvement; check_attribution_complete does not ask for it."
            )
        )
    reward_block = (
        f"- 总分：{reward_overall.total_score}\n- 总评：{reward_overall.comment}"
        if reward_overall is not None
        else "（本次无 Reward 总评）"
    )
    skill_section = (
        "\n\n## 本次轨迹使用的 Skill 清单\n"
        f"{render_skill_block(usages)}\n"
        "`submit_skill_evolution` 的 target 与 `mark_signal_topic(category=\"skill\")` 的 skill_key "
        "必须从本清单选择。"
        if usages
        else ""
    )
    experience_section = (
        "\n\n## 经验判定（TTSE）提示\n"
        "- 轨迹存在工具执行失败 → outcome 至少 `partial`；\n"
        "- 存在用户负反馈信号 → `partial`；\n"
        "- 工具调用次数 < 5 → 本次不产出 experience 条目。\n"
        "先判定后标记：只有确定会沉淀经验时才调用 `mark_signal_topic(category=\"experience\")`；"
        "若标记后推翻判定，用 `mark_signal_invalid` 解除。"
        if ttse_advice
        else ""
    )
    marked = skill_evolution or ttse_advice
    route_extra = (
        "- 被标 skill/experience 的信号 → 对应的 `submit_*_cause` 工具；未标记的规则信号不提交，"
        "结论写进 `submit_trajectory_summary`。\n"
        if marked
        else ""
    )
    order = (
        "工作顺序：`view_*` 读轨迹 → 判定并 `mark_signal_topic` → 定向归因工具 → 全局演进工具 → "
        "`submit_trajectory_summary` → `check_attribution_complete`，直到返回 all_done=true。每个信号的"
        "标记必须先于其定向提交；允许边判边标边提。"
        if marked
        else "工作顺序：`view_*` 读轨迹 → 定向归因工具 → `submit_trajectory_summary` → "
        "`check_attribution_complete`，直到返回 all_done=true。"
    )
    return (
        "## 任务描述\n"
        f"{task_description}\n\n"
        "## Reward 总评\n"
        f"{reward_block}\n\n"
        "## 待归因诊断信号（每行一条 JSON，原文未翻译）\n"
        f"{render_signal_block(signals, premarks) or '（无）'}\n\n"
        "## subagent 列表（subagent_id = subagent_key；display_name 为展示名；kind 为类型）\n"
        f"{render_subjects_block(subjects)}"
        f"{skill_section}"
        f"{experience_section}\n\n"
        "## 信号路由与工作顺序\n"
        "按以下规则逐条路由：\n"
        "- reward 源信号 → `submit_reward_failure`（或 `mark_signal_invalid`）；\n"
        f"{route_extra}"
        f"{order}"
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
            "- pending_reward_signal_ids: submit_reward_failure or mark_signal_invalid;\n"
            "- pending_skill_cause_signal_ids / pending_experience_cause_signal_ids: submit the "
            "matching submit_*_cause tool;\n"
            "- missing_skill_globals: submit_skill_evolution for each listed skill;\n"
            "- missing_experience_evolution=true: submit_experience_evolution;\n"
            "- missing_trajectory_summary=true: submit_trajectory_summary;\n"
            "- Then call check_attribution_complete again until all_done=true; at the end, echo that "
            "JSON verbatim."
        )
    return (
        "上一轮运行已自然结束，但完成度检查未通过。check_attribution_complete 返回：\n\n"
        f"{block}\n\n"
        "请继续完成未汇报项：\n"
        "- pending_reward_signal_ids：用 submit_reward_failure 完成定位归因，或 mark_signal_invalid 标记无效；\n"
        "- pending_skill_cause_signal_ids / pending_experience_cause_signal_ids：调用对应的 submit_*_cause 工具；\n"
        "- missing_skill_globals：为列出的每个 skill 调用 submit_skill_evolution；\n"
        "- missing_experience_evolution=true：调用 submit_experience_evolution；\n"
        "- missing_trajectory_summary=true：调用 submit_trajectory_summary；\n"
        "- 完成后再次调用 check_attribution_complete，直到 all_done=true；结束时原样复述该 JSON。"
    )


def build_trajectory_summary_system_prompt(language: str = "zh") -> str:
    """System prompt of the summary-only agent (no signals, no findings)."""
    lang = _normalize_language(language)
    return f"{_load_prompt(f'summary_{lang}.md')}\n\n{output_language_directive(lang)}"


def build_trajectory_summary_initial_prompt(
    *,
    task_hint: str,
    subjects: list[SubjectRef],
    language: str = "zh",
) -> str:
    """Initial user prompt for the summary-only agent."""
    lang = _normalize_language(language)
    hint = (task_hint or "").strip()
    if lang == "en":
        return (
            "## Task Hint (may be empty or incomplete)\n"
            f"{hint or '(none)'}\n\n"
            "## Subagent List (subagent_id = subagent_key; display_name is the display name; kind is the type)\n"
            f"{render_subjects_block(subjects)}\n\n"
            "Read the selected trajectory, then call submit_trajectory_summary with one paragraph of at "
            "most 300 characters that contains no concrete node ids."
        )
    return (
        "## 任务线索（可能为空或不完整）\n"
        f"{hint or '（无）'}\n\n"
        "## subagent 列表（subagent_id = subagent_key；display_name 为展示名；kind 为类型）\n"
        f"{render_subjects_block(subjects)}\n\n"
        "请阅读选中范围的轨迹，然后调用 submit_trajectory_summary 提交一段不超过 300 字、"
        "不包含具体节点 id 的摘要。"
    )


def build_trajectory_summary_agent(
    *,
    model: "Model",
    runtime: AttributionAgentRuntime,
    language: str = "zh",
    total_token_budget: int | None = _DEFAULT_CONTEXT_BUDGET_TOKENS,
    max_iterations: int = 40,
    max_context_messages: int = _DEFAULT_MAX_CONTEXT_MESSAGES,
    status_sink: StatusSink | None = None,
    model_name: str = "",
    status_counter: list[int] | None = None,
) -> tuple["DeepAgent", ContextBudgetRail]:
    """Build the summary-only agent: read tools + submit_trajectory_summary."""
    from openjiuwen.agent_evolving.attribution.tools import build_summary_tools
    from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard
    from openjiuwen.harness.factory import create_deep_agent

    rail = ContextBudgetRail(total_token_budget)
    try:
        from openjiuwen.harness.observability import maybe_agent_observability_rail

        observability_rail = maybe_agent_observability_rail()
    except ImportError:
        observability_rail = None
        logger.warning("[summary] observability rail unavailable; run spans disabled")
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
        without_time_limits(model),
        tool_owner_id=f"{_AGENT_CARD_ID}-summary-{uuid.uuid4().hex[:12]}",
        card=AgentCard(
            id=f"{_AGENT_CARD_ID}-summary",
            name="TrajectorySummaryAgent",
            description="Trajectory summary agent / 轨迹摘要 Agent",
        ),
        system_prompt=build_trajectory_summary_system_prompt(language),
        tools=build_summary_tools(runtime),
        rails=rails,
        max_iterations=max_iterations,
        context_engine_config=ContextEngineConfig(max_context_message_num=max_context_messages),
        enable_security_rail=False,
        enable_model_anomaly_detection_rail=True,
        enable_sys_operation=False,
    )
    return agents, rail


def build_attribution_agent(
    *,
    model: "Model",
    signals: list[DiagnosticSignal],
    runtime: AttributionAgentRuntime,
    task_description: str,
    reward_overall: RewardOverall | None,
    language: str = "zh",
    swarm_mode: bool = False,
    total_token_budget: int | None = _DEFAULT_CONTEXT_BUDGET_TOKENS,
    max_iterations: int = _DEFAULT_MAX_ITERATIONS,
    max_context_messages: int = _DEFAULT_MAX_CONTEXT_MESSAGES,
    status_sink: StatusSink | None = None,
    model_name: str = "",
    status_counter: list[int] | None = None,
    skill_evolution: bool = True,
    ttse_advice: bool = True,
) -> tuple["DeepAgent", ContextBudgetRail]:
    """Build the attribution agent and its budget rail.

    Only the custom tools are registered; task loop, subagents, mcps and
    skills stay off.  Disabled switches drop their tools and prompt blocks.
    The host reads ``rail.exceeded`` after ``invoke``.
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
        without_time_limits(model),
        # The agent's tools are registered process-wide under "<tool>_<owner id>": one owner per run, so a diagnosis
        # running at the same time cannot take over this run's tools (and with them its trajectory and submissions).
        tool_owner_id=f"{_AGENT_CARD_ID}-{uuid.uuid4().hex[:12]}",
        card=AgentCard(
            id=_AGENT_CARD_ID,
            name="AttributionAgent",
            description="Trajectory attribution agent / 轨迹归因 Agent",
        ),
        system_prompt=build_attribution_system_prompt(
            language,
            swarm_mode=swarm_mode,
            skill_evolution=skill_evolution,
            ttse_advice=ttse_advice,
        ),
        tools=filter_attribution_tools(
            build_attribution_tools(runtime, list(signals)),
            skill_evolution=skill_evolution,
            ttse_advice=ttse_advice,
        ),
        rails=rails,
        max_iterations=max_iterations,
        context_engine_config=ContextEngineConfig(max_context_message_num=max_context_messages),
        enable_security_rail=False,
        # retries a dropped connection, 429 or 5xx (up to 3 times); tool-loop compaction stays off by default
        enable_model_anomaly_detection_rail=True,
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
    "build_trajectory_summary_agent",
    "build_trajectory_summary_initial_prompt",
    "build_trajectory_summary_system_prompt",
    "build_wake_text",
    "filter_attribution_tools",
    "filter_skill_evolution_tool",
    "render_signal_block",
    "render_skill_block",
    "render_subjects_block",
]
