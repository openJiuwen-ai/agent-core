# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Task completion rail with integrated goal lifecycle support.

``TaskCompletionRail`` unified task-completion strategy Rail.
Responsibilities (task loop):
1. Carry loop-strategy parameters (replacing StopCondition).
2. before_model_call: inject completion-signal section into
   SystemPromptBuilder.
3. before_task_iteration: apply task_instruction template to
   the first-round query.
4. after_task_iteration: detect completion promise in output
   and notify CompletionPromiseEvaluator.
Only takes effect when ``enable_task_loop=True``.

When a ``goal_manager`` is supplied, the rail additionally drives the
goal attempt lifecycle (formerly in the standalone GoalCompletionDriver):
- Injecting the goal protocol via PromptAttachment on goal rounds
  (before_model_call; cleared on non-goal rounds)
- Replacing the query with <goal_task> XML (before_task_iteration)
- Consuming submit_goal_report and running assessment (after_task_iteration)
- Accumulating token usage (after_model_call)
- Writing back GoalRecord state transitions
- Registering/unregistering the SubmitGoalReportTool via init/uninit
"""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional

from openjiuwen.core.foundation.tool import Tool
from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts.prompt_attachment_manager import (
    PromptAttachmentKind,
)
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.prompts.sections.task_completion import (
    build_completion_signal_section,
    build_iteration_budget_section,
)
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.schema.stop_condition import (
    CompletionPromiseEvaluator,
    IterationBudgetExhaustedEvaluator,
    MaxRoundsEvaluator,
    RoundBudgetExhaustedEvaluator,
    StopConditionEvaluator,
    TimeoutEvaluator,
)

if TYPE_CHECKING:
    from openjiuwen.harness.goal.evaluation import GoalEvaluator
    from openjiuwen.harness.goal.manager import GoalManager
    from openjiuwen.harness.goal.schema import GoalAssessment, GoalRecord
    from openjiuwen.harness.tools.goal import GoalReportSink

logger = logging.getLogger(__name__)

# Cap transcript assessor input size so a long attempt cannot blow the
# assessor prompt / context window.  Truncation prefers the latest messages.
_ATTEMPT_CONTEXT_MAX_CHARS = 64_000

# ----------------------------------------------------------------
# Promise tag pattern
# ----------------------------------------------------------------
PROMISE_TAG_PATTERN = re.compile(
    r"<promise>\s*(.*?)\s*</promise>",
    re.DOTALL | re.IGNORECASE,
)

# Soft-hint threshold (hardcoded, no config knob): hint when
# remaining rounds <= this, mandatory wrap-up when the cap is reached.
_BUDGET_HINT_THRESHOLD = 3

# Expansion tools blocked during the budget wrap-up round:
# the wrap-up round is for the final summary only — no new delegations, no
# new tasks, no waking members, no swarmflow expansion. ``update_task`` is
# blocked only when it carries an assignee (reassignment = expansion);
# status-only updates stay allowed.
_WRAP_UP_BLOCKED_TOOLS = frozenset({
    "spawn_teammate",
    "spawn_human_agent",
    "spawn_bridge_agent",
    "spawn_external_cli",
    "create_task",
    # wake/revive a member or start new work through the mailbox
    "send_message",
    # orchestration fan-out and team expansion
    "swarmflow",
    "build_team",
    # approving a plan resumes member work
    "approve_plan",
})

# ``update_task`` task-graph changes that count as expansion: (re)assigning,
# adding/removing dependencies, adding/changing a reviewer. Status-only
# updates (including cancelled) stay allowed.
_TASK_GRAPH_EXPANSION_ARGS = (
    "assignee",
    "reviewer",
    "add_blocked_by",
    "remove_blocked_by",
)

# Inner-iteration grace for the wrap-up round (收尾轮硬上限，
# 硬编码不加旋钮): the summary should take a handful of model calls; beyond
# this the wrap-up round itself is force-finished.
_WRAP_UP_GRACE_ITERATIONS = 30


class TaskCompletionRail(DeepAgentRail):
    """Task-completion strategy Rail with optional goal lifecycle support.

    Carries loop-strategy parameters and implements lifecycle hooks that
    drive completion detection and prompt injection.

    When ``goal_manager`` is provided, the rail also manages goal
    attempts: tool registration, query replacement, protocol prompt
    injection, token accounting, report consumption, and assessment.

    Args:
        task_instruction: Optional format string with a
            ``{query}`` placeholder.  Applied to the query on
            the first (non-follow-up) iteration.
        completion_promise: Token the model must output inside
            ``<promise>…</promise>`` to signal task completion.
        max_rounds: Maximum number of outer-loop rounds before
            the loop is force-stopped.
        iteration_budget: Maximum number of INNER ReAct iterations
            (every model call) per invoke. Exceeding it force-finishes
            the current round at the next iteration boundary.
        timeout_seconds: Wall-clock timeout in seconds for the
            entire task loop.
        evaluators: Additional custom evaluators appended after
            the built-in ones.
        goal_manager: Optional GoalManager. When set, goal
            lifecycle hooks are activated.
        goal_language: Language for goal prompts (default "cn").
    """

    priority = 10

    def __init__(
            self,
            task_instruction: Optional[str] = None,
            completion_promise: Optional[str] = None,
            required_confirmations: int = 1,
            allow_promise_details: bool = False,
            max_rounds: Optional[int] = None,
            iteration_budget: Optional[int] = None,
            timeout_seconds: Optional[float] = None,
            evaluators: Optional[
                List[StopConditionEvaluator]
            ] = None,
            goal_manager: Optional[GoalManager] = None,
            goal_language: str = "cn",
            enable_budget_wrap_up: bool = False,
    ) -> None:
        super().__init__()
        self.task_instruction = task_instruction
        self.completion_promise = completion_promise
        self.required_confirmations = max(
            1, int(required_confirmations)
        )
        self.allow_promise_details = allow_promise_details
        self.max_rounds = max_rounds
        self.timeout_seconds = timeout_seconds
        self._extra_evaluators: List[StopConditionEvaluator] = (
                evaluators or []
        )

        # Goal support
        self._goal_manager = goal_manager
        self._goal_language = goal_language
        self._goal_report_sink: Optional["GoalReportSink"] = None
        self._goal_evaluator: Optional["GoalEvaluator"] = None
        self._goal_tools: List[Tool] = []
        self._is_goal_round = False
        self._current_goal_id: Optional[str] = None
        self._current_revision: Optional[int] = None
        self._current_session_id: Optional[str] = None
        self._current_attempt_messages: List[Any] = []
        self.attachment_manager = None

        # Iteration budget:
        # per-invoke INNER ReAct iteration counter (every model call),
        # soft hints and the optional shared team budget hook.
        # ``budget_tick`` is wired by the team layer (TeamAgent); it is a
        # zero-arg callable that ticks the round's shared ledger and
        # returns the remaining total budget (None = no ledger). Wired as
        # a getter-resolved callable so a mid-invoke ledger swap (new
        # round) can never be written through a stale reference.
        self.iteration_budget = iteration_budget
        self._iteration_count = 0
        self._iteration_cap_hit = False
        self._budget_total_exhausted = False
        # 成员池到顶（leader 仍持有预留）：leader rail 由 kernel 置位，
        # 停轮后进入收尾；成员侧不使用该标志。
        self._member_budget_exhausted = False
        self.budget_tick: Optional[Callable[[], Optional[int]]] = None
        # 非变更探针：只读剩余额度，不计数（结算点/看门狗用）。
        self.budget_probe: Optional[Callable[[], Optional[int]]] = None
        # 本外层轮是否已就 total 耗尽发起过止血（before_invoke 重新武装）
        self._budget_finish_armed = False
        # Wrap-up round (leader only): when a budget cap is
        # hit, grant exactly one final OUTER round for the delivery summary
        # (inner grace = _WRAP_UP_GRACE_ITERATIONS), with expansion tools
        # hard-blocked. ``on_budget_wrap_up`` fires once at wrap-up entry so
        # the team layer can stop members and repool tasks BEFORE the
        # summary round runs.
        self.enable_budget_wrap_up = enable_budget_wrap_up
        # 返回 list[str]（旧）或 {"repooled": [...], "counts": {...}}（新）
        self.on_budget_wrap_up: Optional[Callable[[], Awaitable[Any]]] = None
        # 收尾提示用的任务板计数（待办/被卡住/已完成）
        self._wrap_up_task_counts: Dict[str, int] = {}
        # 成员侧收口回调：迭代预算触顶时触发（成员无 wrap-up，
        # 触顶即收口）。team 层 kernel 接线；None = 不通报（非团队链路）。
        self.on_iteration_cap: Optional[Callable[[], Awaitable[None]]] = None
        # 成员侧 total 耗尽停轮回调：只回池自己在途任务（不摘牌/不通报）。
        # leader 不接此回调——回池由收尾轮统一负责。
        self.on_total_budget_exhausted: Optional[
            Callable[[], Awaitable[None]]
        ] = None
        self._wrap_up_consumed = False
        self._in_wrap_up_round = False
        self._wrap_up_iterations = 0
        # 收尾轮回池任务清单（kernel 回调返回，注入收尾提示）
        self._wrap_up_repooled: List[str] = []
        # Agent 名只用于日志；init() 时填充，未初始化时回落 "?"
        self._agent_name: str = "?"
        # 周期重置采样计数：每 10 个周期打一条 reset 日志
        self._budget_cycle_seq: int = 0

    def set_goal_manager(
            self, goal_manager: GoalManager,
    ) -> None:
        """Inject or replace the GoalManager after DeepAgent starts.

        Called by ``DeepAgent.start()`` after the
        manager is created so that the rail can drive goal
        lifecycle without requiring it at construction time.
        Also lazily initialises the report sink and evaluator when
        goal mode is first activated.
        """
        self._goal_manager = goal_manager
        if self._goal_report_sink is None:
            from openjiuwen.harness.goal.evaluation import GoalEvaluator
            from openjiuwen.harness.tools.goal import GoalReportSink

            self._goal_report_sink = GoalReportSink()
            self._goal_evaluator = GoalEvaluator()

    # -- evaluator builder --

    def build_evaluators(
            self,
    ) -> List[StopConditionEvaluator]:
        """Build the evaluator chain from rail parameters.

        Returns:
            Ordered list of evaluators.  Built-in evaluators
            precede any extra evaluators supplied at construction.
        """
        result: List[StopConditionEvaluator] = []
        if self.max_rounds is not None:
            result.append(MaxRoundsEvaluator(self.max_rounds))
        if self.iteration_budget is not None:
            result.append(
                IterationBudgetExhaustedEvaluator(
                    lambda: self._iteration_cap_hit
                            and (not self.enable_budget_wrap_up or self._wrap_up_consumed)
                )
            )
        if self.budget_tick is not None:
            result.append(
                RoundBudgetExhaustedEvaluator(
                    lambda: self._budget_stop_hit
                            and (not self.enable_budget_wrap_up or self._wrap_up_consumed)
                )
            )
        if self.timeout_seconds is not None:
            result.append(
                TimeoutEvaluator(self.timeout_seconds)
            )
        if self.completion_promise is not None:
            result.append(
                CompletionPromiseEvaluator(
                    self.completion_promise,
                    required_confirmations=self.required_confirmations,
                )
            )
        result.extend(self._extra_evaluators)
        return result

    # -- init / uninit --

    def init(self, agent: Any) -> None:
        """Register the goal tools when goal mode is active.

        Registers two tools that stay available for the whole DeepAgent lifecycle
        (both goal rounds and normal user turns):
        - ``submit_goal_report``: submit the structured attempt result.
        - ``get_current_goal``: let the model re-orient to the active goal
          when it has lost the ``<goal_task>`` context (e.g. after the user
          interjects a normal question).
        """
        self.attachment_manager = getattr(
            agent, "prompt_attachment_manager", None
        )
        self._agent_name = getattr(
            getattr(agent, "card", None), "name", "?",
        ) or "?"
        if self._goal_manager is None:
            return

        from openjiuwen.harness.tools.goal import (
            GetCurrentGoalTool,
            SubmitGoalReportTool,
        )

        agent_id = getattr(agent, "agent_id", None)
        tools: List[Tool] = [
            SubmitGoalReportTool(
                self._goal_report_sink,
                language=self._goal_language,
                agent_id=agent_id,
            ),
            GetCurrentGoalTool(
                self._goal_manager,
                language=self._goal_language,
                agent_id=agent_id,
            ),
        ]
        if hasattr(agent, "ability_manager"):
            for tool in tools:
                agent.ability_manager.add_ability(tool.card, tool)
            self._goal_tools = tools
            logger.info(
                "TaskCompletionRail: registered submit_goal_report "
                "and get_current_goal tools"
            )
        else:
            logger.warning(
                "TaskCompletionRail.init: agent has no ability_manager; "
                "goal tools were not registered"
            )

    def uninit(self, agent: Any) -> None:
        """Remove the goal tools."""
        if not hasattr(agent, "ability_manager"):
            self._goal_tools = []
            self.attachment_manager = None
            return
        for tool in self._goal_tools:
            name = getattr(tool.card, "name", None)
            if not name:
                continue
            try:
                agent.ability_manager.remove_ability(name)
            except Exception:
                logger.debug(
                    "TaskCompletionRail: failed to remove goal tool %s",
                    name,
                    exc_info=True,
                )
        self._goal_tools = []
        self.attachment_manager = None

    # -- lifecycle hooks --

    def reset_budget_cycle(self) -> None:
        """Reset the iteration budget at a CYCLE boundary.

        周期边界 = 一次完整的 agent 调用周期：
        - 非团队 DeepAgent：一次 task-loop invoke（``_run_task_loop`` 起点）；
        - 专家团（NativeHarness）：一次 coordination.start 周期（leader 一条
          用户消息 / 成员一次派发），由 coordination kernel 在 start 时调用。

        刻意不在 ``before_invoke`` 里重置：团队链路的 BEFORE_INVOKE 是
        **每个外层轮**触发一次（native_harness._drive_round），在那里重置会让
        main/total 预算退化为"每轮一份"，收尾轮入口的标志也会被清零。
        """
        self._budget_cycle_seq += 1
        if self._budget_cycle_seq % 10 == 0:
            logger.info(
                "[TaskCompletionRail] iteration budget cycle reset: "
                "agent=%s cycle=%d prev_iterations=%d",
                self._agent_name,
                self._budget_cycle_seq,
                self._iteration_count,
            )
        self._iteration_count = 0
        self._iteration_cap_hit = False
        self._budget_total_exhausted = False
        self._member_budget_exhausted = False
        self._budget_finish_armed = False
        self._wrap_up_consumed = False
        self._in_wrap_up_round = False
        self._wrap_up_iterations = 0
        self._wrap_up_repooled = []
        self._wrap_up_task_counts = {}

    @property
    def iteration_cap_hit(self) -> bool:
        """Whether the per-invoke iteration budget was exhausted."""
        return self._iteration_cap_hit

    @property
    def total_budget_exhausted(self) -> bool:
        """Whether the shared round-budget ledger is exhausted."""
        return self._budget_total_exhausted

    @property
    def _budget_stop_hit(self) -> bool:
        """Absolute total hit, or the member pool hit (leader reserve)."""
        return bool(
            self._budget_total_exhausted or self._member_budget_exhausted
        )

    def mark_member_budget_exhausted(self) -> None:
        """Leader rail hook: member pool reached its cap (reserve remains).

        Set by the coordination kernel on the ledger's exhaustion edge so
        the leader winds down into the wrap-up round even though it still
        holds the reserved share for the current round.
        """
        self._member_budget_exhausted = True

    @property
    def budget_wrap_up_pending(self) -> bool:
        """True when the wrap-up round is owed but not yet entered."""
        return bool(
            self.enable_budget_wrap_up
            and not self._wrap_up_consumed
            and (self._iteration_cap_hit or self._budget_stop_hit)
        )

    @property
    def budget_wrap_up_consumed(self) -> bool:
        """Whether the one budget wrap-up round has already been entered."""
        return self._wrap_up_consumed

    def should_suppress_budget_result(self, result: Any) -> bool:
        """Whether this round result is a pre-wrap-up control payload.

        The round in which a cap is hit ends with the rail's canned
        ``budget_stop`` answer. When a wrap-up round will follow, that
        control payload must not reach the user stream: the wrap-up
        summary is the round's user-visible answer.
        """
        return bool(
            isinstance(result, dict)
            and result.get("budget_stop")
            and self.enable_budget_wrap_up
            and not self._wrap_up_consumed
        )

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Stage goal-round dynamic context before the first user message."""
        # 周期重置不在这里（见 reset_budget_cycle）：团队链路每外层轮触发
        # 一次 BEFORE_INVOKE。这里只做提示段同步——新周期首个模型调用前
        # 清掉上一周期残留的"最后一轮"收尾提示。
        # 但 total 止血按外层轮重新武装：账本耗尽后每个新轮的首个成功
        # 迭代都要能就地收场，否则事件驱动的新轮会一路跑完。
        self._budget_finish_armed = False
        if not self._in_wrap_up_round:
            self._sync_iteration_budget_prompt(ctx)
        builder = getattr(ctx.agent, "system_prompt_builder", None)
        await self._sync_goal_protocol_prompt(ctx, builder)

    async def before_model_call(
            self, ctx: AgentCallbackContext,
    ) -> None:
        """Inject prompt sections.

        - Always: completion-signal section (when promise is configured).
        - Goal support: goal protocol as a PromptAttachment on goal rounds
          only; cleared on non-goal rounds to avoid wasting context.
        """
        builder = getattr(
            ctx.agent, "system_prompt_builder", None
        )

        if self.completion_promise and builder is not None:
            language = getattr(builder, "language", "cn")
            section = build_completion_signal_section(
                language,
                self.completion_promise,
            )
            builder.add_section(section)

        await self._sync_goal_protocol_prompt(ctx, builder)

    async def _sync_goal_protocol_prompt(
            self,
            ctx: AgentCallbackContext,
            builder: Any,
    ) -> None:
        """Upsert or clear goal protocol based on run_kind."""
        from openjiuwen.harness.prompts.sections.goal import (
            build_goal_protocol_section,
        )

        run_kind = self._get_run_kind(ctx)
        want_goal = (
                run_kind == "goal" and self._goal_manager is not None
        )
        language = getattr(
            builder, "language", self._goal_language
        ) if builder is not None else self._goal_language

        manager = self.attachment_manager
        if manager is None:
            if want_goal:
                logger.warning(
                    "TaskCompletionRail: prompt attachment manager "
                    "is unavailable; skip goal protocol attachment"
                )
            return

        writer = manager.bind_context(ctx)
        if want_goal:
            section = build_goal_protocol_section(language)
            try:
                await writer.add_from_prompt_section(
                    prompt_section=section,
                    kind=PromptAttachmentKind.RUNTIME,
                    source="agent_core.task_completion_rail",
                    language=language,
                    content_kind="text/markdown",
                )
            except ValueError as exc:
                logger.warning(
                    "TaskCompletionRail: skip goal protocol "
                    "prompt attachment: %s",
                    exc,
                )
            return

        try:
            await writer.clear_section(SectionName.GOAL_PROTOCOL)
        except ValueError as exc:
            logger.warning(
                "TaskCompletionRail: skip clearing goal "
                "protocol prompt attachment: %s",
                exc,
            )

    async def before_task_iteration(
            self, ctx: AgentCallbackContext,
    ) -> None:
        """Apply task_instruction template or goal query replacement.

        For goal rounds: validate goal state and replace query with
        <goal_task> XML.
        For normal rounds: apply task_instruction on the first iteration.
        """
        # Wrap-up entry lives at the OUTER round boundary;
        # iteration counting/ticking lives in after_react_iteration (inner
        # ReAct iteration = every model call). Sync first so a stale hint
        # from the previous round cannot leak into the first model call.
        if not self._in_wrap_up_round:
            self._sync_iteration_budget_prompt(ctx)
        await self._maybe_enter_wrap_up_round(ctx)
        run_kind = self._get_run_kind(ctx)
        if run_kind == "goal" and self._goal_manager is not None:
            self._do_goal_before_iteration(ctx)
            return

        self._is_goal_round = False

        if not self.task_instruction:
            return
        inputs = ctx.inputs
        query = getattr(inputs, "query", None)
        if not query:
            return
        if getattr(inputs, "is_follow_up", False):
            return
        inputs.query = self.task_instruction.format(
            query=query,
        )

    async def after_react_iteration(self, ctx: AgentCallbackContext) -> None:
        """Count one inner ReAct iteration (every model call).

        循环轮次=内层迭代。外层
        轮内 ReAct 在 task loop 模式下不封顶（sys.maxsize），轮内死循环
        只能在这里拦——触顶即 request_force_finish，在下个迭代边界收场。
        """
        if self.iteration_budget is None and self.budget_tick is None:
            # 仍要走一次 section 同步：预算被热清除时摘除残留提示
            self._sync_iteration_budget_prompt(ctx)
            return
        self._iteration_count += 1
        if self._in_wrap_up_round:
            self._wrap_up_iterations += 1
            if self._wrap_up_iterations > _WRAP_UP_GRACE_ITERATIONS:
                self._request_budget_force_finish(ctx, "wrap_up_grace")
            return
        if (
                self.iteration_budget is not None
                and not self._iteration_cap_hit
                and self._iteration_count > self.iteration_budget
        ):
            self._iteration_cap_hit = True
            self._request_budget_force_finish(ctx, "iteration_budget")
            if self.on_iteration_cap is not None:
                try:
                    await self.on_iteration_cap()
                except Exception:  # noqa: BLE001 - 收口回调失败不阻断收场
                    logger.warning(
                        "[TaskCompletionRail] iteration-cap callback failed",
                        exc_info=True,
                    )
        self._tick_round_budget(ctx)
        self._sync_iteration_budget_prompt(ctx)

    def _request_budget_force_finish(
            self, ctx: AgentCallbackContext, reason: str,
    ) -> None:
        """Ask the ReAct loop to finish at the next iteration boundary."""
        if ctx.has_force_finish_request:
            return
        language = getattr(
            getattr(ctx.agent, "system_prompt_builder", None),
            "language",
            "cn",
        )
        if language == "cn":
            text = "迭代预算已耗尽，本轮提前结束。"
        else:
            text = "Iteration budget exhausted; round ended early."
        ctx.request_force_finish({
            "output": text,
            "result_type": "answer",
            "budget_stop": reason,
        })
        logger.warning(
            "[TaskCompletionRail] iteration budget force finish: "
            "agent=%s reason=%s count=%d",
            getattr(getattr(ctx.agent, "card", None), "name", "?"),
            reason,
            self._iteration_count,
        )

    def _tick_round_budget(
            self, ctx: AgentCallbackContext,
    ) -> None:
        """Tick the shared team budget ledger once per inner iteration.

        Fail-open: a ledger failure must never kill the loop; exhaustion
        is surfaced via ``_budget_total_exhausted`` which the
        RoundBudgetExhaustedEvaluator reads at the next loop boundary.
        触顶同时在下个内层迭代边界 force-finish（就地止血）——只靠外层
        评估器的话，当前外层轮仍可能再烧完整一圈。
        """
        if self.budget_tick is None:
            return
        try:
            remaining = self.budget_tick()
        except Exception:
            logger.warning(
                "[TaskCompletionRail] budget tick failed", exc_info=True,
            )
            return
        if remaining is not None and remaining <= 0:
            self._budget_total_exhausted = True
            # 每个外层轮至多发起一次止血（before_invoke 重新武装）。
            # 只按“首次 False→True”的话，耗尽后新起的轮不会收场。
            if not self._budget_finish_armed:
                self._budget_finish_armed = True
                self._request_budget_force_finish(ctx, "budget_total")

    def refresh_budget_state(self) -> None:
        """Settle-time probe: refresh total exhaustion without ticking.

        纯文本轮（模型直接出答案、零工具）不会触发
        ``after_react_iteration``；只靠 tick 的话，team 总量早已耗尽也无法
        在结算点感知，收尾轮会被无限推迟。结算点用非变更
        探针补一次判定，只置位标志、不请求 force finish（轮已结束）。
        """
        if self.budget_probe is None:
            return
        try:
            remaining = self.budget_probe()
        except Exception:  # noqa: BLE001 - 探测失败不得打断结算
            logger.warning(
                "[TaskCompletionRail] budget probe failed", exc_info=True,
            )
            return
        if remaining is not None and remaining <= 0:
            self._budget_total_exhausted = True

    def _sync_iteration_budget_prompt(
            self, ctx: AgentCallbackContext,
    ) -> None:
        """Attach/detach the soft-hint section based on remaining rounds.

        Section channel only (SystemPromptBuilder in-memory, rebuilt per
        model call) — never touches query text, so checkpoint/rewind
        history stays clean.
        """
        builder = getattr(ctx.agent, "system_prompt_builder", None)
        if builder is None:
            return
        has_hint = builder.has_section(SectionName.ITERATION_BUDGET)
        if self.iteration_budget is None:
            if has_hint:
                builder.remove_section(SectionName.ITERATION_BUDGET)
            return
        remaining = self.iteration_budget - self._iteration_count
        if remaining > _BUDGET_HINT_THRESHOLD:
            if has_hint:
                builder.remove_section(SectionName.ITERATION_BUDGET)
            return
        language = getattr(builder, "language", "cn")
        agent_name = getattr(
            getattr(ctx.agent, "card", None), "name", "?",
        )
        if remaining <= 0:
            section = build_iteration_budget_section(
                language, remaining, self.iteration_budget, final_round=True,
            )
            logger.warning(
                "[TaskCompletionRail] iteration budget final stretch: agent=%s, forcing wrap-up",
                agent_name,
            )
        else:
            section = build_iteration_budget_section(
                language, remaining, self.iteration_budget,
            )
            logger.info(
                "[TaskCompletionRail] iteration budget warning: agent=%s remaining=%d/%d",
                agent_name,
                remaining,
                self.iteration_budget,
            )
        builder.add_section(section)

    async def _maybe_enter_wrap_up_round(
            self, ctx: AgentCallbackContext,
    ) -> None:
        """Enter the granted wrap-up round on budget exhaustion.

        Fires once per invoke, on the OUTER round AFTER a budget cap is hit
        (main/member cap: ``_iteration_cap_hit``; total: ledger exhausted).
        Attaches the mandatory-wrap-up section (force, even when the
        iteration budget alone would not), then invokes the team-layer
        callback so members are stopped and tasks repooled BEFORE the
        summary round runs.
        """
        if not self.enable_budget_wrap_up or self._wrap_up_consumed:
            return
        if not (self._iteration_cap_hit or self._budget_stop_hit):
            return
        agent_name = getattr(
            getattr(ctx.agent, "card", None), "name", "?",
        )
        payload = await self.prepare_wrap_up_round()
        if payload is None:
            return
        logger.warning(
            "[TaskCompletionRail] iteration budget wrap-up round: agent=%s "
            "(iteration_cap=%s, total_exhausted=%s)",
            agent_name,
            self._iteration_cap_hit,
            self._budget_total_exhausted,
        )
        # Fallback path only: the primary trigger (supervisor / kernel)
        # calls ``prepare_wrap_up_round`` before starting the round and
        # carries the same details in the ``budget-wrap-up`` event body.
        builder = getattr(ctx.agent, "system_prompt_builder", None)
        if builder is not None:
            language = getattr(builder, "language", "cn")
            builder.add_section(
                build_iteration_budget_section(
                    language,
                    0,
                    self.iteration_budget or 0,
                    wrap_up=True,
                    repooled_tasks=self._wrap_up_repooled,
                    task_counts=self._wrap_up_task_counts,
                )
            )

    async def prepare_wrap_up_round(self) -> Optional[dict]:
        """Consume the wrap-up slot and return the task-board payload.

        Called by the supervisor (wrap-up grant) or the coordination kernel
        (idle-leader wake) BEFORE the wrap-up round starts, so the
        ``budget-wrap-up`` event can carry the unfinished-task list/counts.
        The round is still marked as the wrap-up round here (tool gate and
        inner grace apply). Returns None when the wrap-up is not owed.
        """
        if not self.enable_budget_wrap_up or self._wrap_up_consumed:
            return None
        if not (self._iteration_cap_hit or self._budget_stop_hit):
            return None
        self._wrap_up_consumed = True
        self._in_wrap_up_round = True
        self._wrap_up_iterations = 0
        # Team layer first: stop members and put unfinished tasks back to
        # the to-do list BEFORE the summary round runs.
        self._wrap_up_repooled = []
        self._wrap_up_task_counts = {}
        payload: Any = None
        if self.on_budget_wrap_up is not None:
            try:
                payload = await self.on_budget_wrap_up()
            except Exception:
                logger.warning(
                    "TaskCompletionRail: budget wrap-up callback failed",
                    exc_info=True,
                )
        self._consume_wrap_up_payload(payload)
        return {
            "repooled": list(self._wrap_up_repooled),
            "counts": dict(self._wrap_up_task_counts),
        }

    def _consume_wrap_up_payload(self, payload: Any) -> None:
        """Normalize the team callback result (legacy list or dict)."""
        if isinstance(payload, dict):
            self._wrap_up_repooled = [
                str(t) for t in payload.get("repooled", [])
            ]
            counts = payload.get("counts")
            if isinstance(counts, dict):
                self._wrap_up_task_counts = {
                    str(k): int(v) for k, v in counts.items()
                }
        elif isinstance(payload, (list, tuple)):
            self._wrap_up_repooled = [str(t) for t in payload]

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Block expansion once the team's work pool is closed.

        Active in the wrap-up round AND in the endgame window after the
        member pool hit its cap (``_member_budget_exhausted``): the leader
        may finish its current round with the reserved budget, but it must
        not create/reassign tasks or wake members — those only produce
        claim-then-stop churn on exhausted members.
        """
        if not (self._in_wrap_up_round or self._member_budget_exhausted):
            return
        inputs = ctx.inputs
        tool_name = getattr(inputs, "tool_name", "") or ""
        if not tool_name and getattr(inputs, "tool_call", None) is not None:
            tool_name = getattr(inputs.tool_call, "name", "") or ""
        blocked = tool_name in _WRAP_UP_BLOCKED_TOOLS
        if not blocked and tool_name == "update_task":
            args = getattr(inputs, "tool_args", None)
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (ValueError, TypeError):
                    args = None
            # 任务图变更同样算扩张：派/换人、加/去依赖、加/换评审。
            # 只改状态（含 cancelled）仍放行，便于显式放弃任务。
            if isinstance(args, dict):
                for key in _TASK_GRAPH_EXPANSION_ARGS:
                    if args.get(key):
                        blocked = True
                        break
        if not blocked:
            return
        language = getattr(
            getattr(ctx.agent, "system_prompt_builder", None),
            "language",
            "cn",
        )
        if language == "cn":
            reason = (
                f"[预算用完] 迭代预算已耗尽，禁止调用 {tool_name} "
                "等扩张类工具；请直接输出最终交付总结。"
            )
        else:
            reason = (
                f"[Budget used up] Iteration budget exhausted; expansion "
                f"tool {tool_name} is blocked. Produce the final summary now."
            )
        tool_call = getattr(inputs, "tool_call", None)
        tool_call_id = tool_call.id if tool_call else ""
        ctx.extra["_skip_tool"] = True
        inputs.tool_result = {"error": reason}
        inputs.tool_msg = ToolMessage(
            content=reason, tool_call_id=tool_call_id,
        )
        logger.warning(
            "[TaskCompletionRail] endgame blocked expansion tool: %s", tool_name,
        )

    async def after_model_call(
            self, ctx: AgentCallbackContext,
    ) -> None:
        """Accumulate token usage for goal rounds."""
        if not self._is_goal_round or self._goal_manager is None:
            return

        self._capture_attempt_model_context(ctx)

        usage = self._extract_usage(ctx)
        if usage:
            await self._goal_manager.accumulate_usage(
                goal_id=str(self._current_goal_id),
                revision=int(self._current_revision),
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                cached_input_tokens=usage.get("cached_input_tokens", 0),
            )

    async def after_task_iteration(
            self, ctx: AgentCallbackContext,
    ) -> None:
        """Detect completion promise and/or consume goal report.

        For goal rounds: consume report, run assessment, manage lifecycle.
        For normal rounds: detect promise tag.
        """
        if self._is_goal_round and self._goal_manager is not None:
            await self._do_goal_after_iteration(ctx)
            return

        if not self.completion_promise:
            return
        content = self._extract_output(ctx)
        if not content:
            return
        promise_block = extract_promise_block(content)
        if promise_block is None:
            return
        matched = _normalize(promise_block)
        expected = _normalize(self.completion_promise)
        if matched != expected:
            if not self.allow_promise_details:
                return
            if not promise_matches(
                    promise_block,
                    self.completion_promise,
            ):
                return
            matched = expected
        logger.info(
            "TaskCompletionRail: promise fulfilled: %r",
            matched,
        )
        self._notify_evaluator(ctx, matched)

    # ================================================================
    # Goal lifecycle (integrated from former GoalCompletionDriver)
    # ================================================================

    def _do_goal_before_iteration(
            self, ctx: AgentCallbackContext,
    ) -> None:
        """Validate goal and replace query with <goal_task> XML."""
        run_context = self._get_run_context(ctx)
        goal_id = run_context.get("goal_id")
        revision = run_context.get("revision")
        session_id = run_context.get("session_id", "default")

        manager = self._goal_manager
        if manager is None:
            return
        store = manager.get_store(session_id)
        record = store.load()

        if not self._validate_goal_state(record, goal_id, revision):
            self._is_goal_round = False
            self._current_attempt_messages = []
            return

        self._is_goal_round = True
        self._current_goal_id = goal_id
        self._current_session_id = session_id

        # DeepAgent increments attempt_count before it starts the task-loop
        # round.  Rails only consume that committed generation.
        self._current_revision = record.revision

        if self._goal_report_sink is not None:
            self._goal_report_sink.begin_attempt(
                session_id=record.session_id,
                goal_id=record.goal_id,
                revision=record.revision,
                attempt_index=record.attempt_count,
            )

        from openjiuwen.harness.prompts.sections.goal import (
            build_goal_task_query,
        )

        self._current_attempt_messages = []
        inputs = ctx.inputs
        if hasattr(inputs, "query"):
            inputs.query = build_goal_task_query(record, self._goal_language)

    async def _do_goal_after_iteration(
            self, ctx: AgentCallbackContext,
    ) -> None:
        """Consume the goal report and run assessment."""
        run_context = self._get_run_context(ctx)
        session_id = run_context.get("session_id", "default")

        manager = self._goal_manager
        if manager is None:
            return
        store = manager.get_store(session_id)
        record = store.load()

        if not self._validate_goal_state(
                record,
                self._current_goal_id,
                self._current_revision,
                allow_paused=True,
        ):
            logger.info(
                "[GoalLifecycle] Goal state invalid after iteration, "
                "discarding results"
            )
            return

        # Permission / HITL pause: react_agent.invoke returns result_type=interrupt
        # and the task loop still fires AFTER_TASK_ITERATION (for cleanup rails).
        # That is not a finished goal attempt — skip transcript assessor and
        # apply_assessment so we do not burn an LLM call or mutate GoalRecord
        # while the frontend is still answering the interrupt.
        result = getattr(getattr(ctx, "inputs", None), "result", None)
        if isinstance(result, dict) and result.get("result_type") == "interrupt":
            logger.info(
                "[GoalLifecycle] skip assessment on interrupt "
                "(HITL/permission pause; attempt not finished)"
            )
            return

        # Only when invoke *raised* out of the task loop (frontend already sees
        # failure). TaskLoopEventExecutor then sets this iteration's
        # ``ctx.exception`` and fires AFTER_TASK_ITERATION. Do not confuse with
        # ``on_model_exception``: that uses a different AgentCallbackContext
        # inside the model-call @rail wrapper; retries clear it and never
        # surface here unless the exception is re-raised past invoke.
        from openjiuwen.harness.goal.schema import GoalAssessment, GoalAssessmentStatus

        round_error = self._extract_goal_round_error(ctx)
        if round_error is not None:
            logger.warning(
                "[GoalLifecycle] Goal attempt failed; blocking goal: %s",
                round_error[:300],
            )
            await manager.apply_assessment(
                goal_id=str(self._current_goal_id),
                revision=int(self._current_revision),
                assessment=GoalAssessment(
                    status=GoalAssessmentStatus.BLOCKED,
                    evidence=f"round_execution_error: {round_error}",
                    remaining_work=(
                        "Resolve the execution error before resuming the goal."
                    ),
                    next_instruction=(
                        "Fix the underlying failure (for example model or API "
                        "configuration), then resume the goal."
                    ),
                ),
            )
            return

        agent_report = (
            self._goal_report_sink.consume()
            if self._goal_report_sink is not None
            else None
        )
        transcript_response = await self._maybe_invoke_transcript_assessor(
            record, agent_report, ctx,
        )
        if self._goal_evaluator is None:
            logger.warning("[GoalLifecycle] Goal evaluator unavailable")
            return

        assessment = self._goal_evaluator.assess(
            record=record,
            agent_report=agent_report,
            transcript_response=transcript_response,
        )

        await manager.apply_assessment(
            goal_id=str(self._current_goal_id),
            revision=int(self._current_revision),
            assessment=assessment,
        )

    @staticmethod
    def _extract_goal_round_error(ctx: AgentCallbackContext) -> Optional[str]:
        """Return a message only when this task-iteration ctx has a thrown error.

        ``ctx`` here is the *outer* TaskLoopEventExecutor context, created fresh
        each iteration. It is not the model-call context used by
        ``ON_MODEL_EXCEPTION``. Model retries that succeed never set
        ``exception`` on this object; only the executor's ``except`` after
        ``react_agent.invoke`` re-raises does.
        """
        exc = getattr(ctx, "exception", None)
        if exc is None:
            return None
        text = str(exc).strip()
        return text or type(exc).__name__

    async def _maybe_invoke_transcript_assessor(
            self,
            record: GoalRecord,
            agent_report: Optional[GoalAssessment],
            ctx: AgentCallbackContext,
    ) -> Optional[str]:
        """Invoke transcript assessment only when the configured strategy needs it."""
        from openjiuwen.harness.goal.schema import GoalAssessmentStatus, GoalStopStrategy

        if self._goal_evaluator is None:
            return None

        strategy = self._goal_evaluator.strategy
        if strategy is GoalStopStrategy.AGENT_REPORT:
            return None
        if strategy is GoalStopStrategy.TRANSCRIPT:
            return await self._invoke_transcript_assessor(record, ctx)

        should_invoke = agent_report is None
        if agent_report is not None:
            should_invoke = agent_report.status in (
                GoalAssessmentStatus.COMPLETE,
                GoalAssessmentStatus.BLOCKED,
            )
            if not should_invoke:
                try:
                    should_invoke = bool(self._goal_evaluator.should_spot_check(record))
                except Exception:
                    should_invoke = False
        if not should_invoke:
            return None

        return await self._invoke_transcript_assessor(record, ctx)

    async def _invoke_transcript_assessor(
            self,
            record: GoalRecord,
            ctx: AgentCallbackContext,
    ) -> Optional[str]:
        """Run a no-tool LLM call that judges the goal attempt transcript."""
        from openjiuwen.core.foundation.llm import SystemMessage, UserMessage
        from openjiuwen.harness.prompts.sections.goal import (
            TRANSCRIPT_ASSESSOR_SYSTEM,
            build_goal_current_instruction,
            build_transcript_assessor_prompt,
        )

        model = self._resolve_transcript_model(ctx)
        if model is None:
            logger.warning(
                "[GoalLifecycle] Transcript assessor skipped: model unavailable"
            )
            return None

        language = self._goal_language
        system_prompt = TRANSCRIPT_ASSESSOR_SYSTEM.get(
            language, TRANSCRIPT_ASSESSOR_SYSTEM["cn"],
        )
        user_prompt = build_transcript_assessor_prompt(
            record.objective,
            build_goal_current_instruction(record, language),
            self._extract_attempt_context(ctx),
            language,
        )
        try:
            response = await model.invoke(
                [
                    SystemMessage(content=system_prompt),
                    UserMessage(content=user_prompt),
                ],
                tools=[],
                temperature=0.0,
                top_p=1.0,
            )
        except TypeError:
            # Some custom model objects accept invoke() without sampling kwargs.
            try:
                response = await model.invoke(
                    [
                        SystemMessage(content=system_prompt),
                        UserMessage(content=user_prompt),
                    ],
                    tools=[],
                )
            except Exception:
                logger.exception(
                    "[GoalLifecycle] Transcript assessor invocation failed"
                )
                return None
        except Exception:
            logger.exception("[GoalLifecycle] Transcript assessor invocation failed")
            return None

        content = getattr(response, "content", None)
        return content if isinstance(content, str) else str(content or "")

    @staticmethod
    def _resolve_transcript_model(ctx: AgentCallbackContext) -> Optional[Any]:
        """Resolve the model used for isolated transcript assessment."""
        agent = ctx.agent
        deep_config = getattr(agent, "deep_config", None)
        model = getattr(deep_config, "model", None)
        if model is not None:
            return model

        react_agent = getattr(agent, "react_agent", None)
        react_config = getattr(react_agent, "config", None)
        model_client_config = getattr(react_config, "model_client_config", None)
        model_config = getattr(react_config, "model_config_obj", None)
        if model_client_config is None or model_config is None:
            return None

        from openjiuwen.core.foundation.llm import Model

        try:
            return Model(model_client_config, model_config)
        except Exception:
            logger.exception("[GoalLifecycle] Failed to create transcript model")
            return None

    # -- validation / extraction helpers --

    @staticmethod
    def _validate_goal_state(
            record: Optional[GoalRecord],
            expected_goal_id: Optional[str],
            expected_revision: Optional[int],
            *,
            allow_paused: bool = False,
    ) -> bool:
        from openjiuwen.harness.goal.schema import GoalStatus

        if record is None:
            return False
        allowed = {GoalStatus.ACTIVE}
        if allow_paused:
            allowed.add(GoalStatus.PAUSED)
        if record.status not in allowed:
            return False
        if expected_goal_id is not None and record.goal_id != expected_goal_id:
            return False
        if expected_revision is not None and record.revision != expected_revision:
            return False
        return True

    @staticmethod
    def _get_run_kind(ctx: AgentCallbackContext) -> Optional[str]:
        inputs = ctx.inputs
        run_kind = getattr(inputs, "run_kind", None)
        if run_kind is not None:
            return str(run_kind.value) if hasattr(run_kind, "value") else str(run_kind)
        metadata = getattr(inputs, "metadata", None)
        if isinstance(metadata, dict):
            return metadata.get("run_kind")
        return None

    @staticmethod
    def _get_run_context(ctx: AgentCallbackContext) -> Dict[str, Any]:
        inputs = ctx.inputs
        run_context = getattr(inputs, "run_context", None)
        if isinstance(run_context, dict):
            result = dict(run_context)
            extra = result.pop("extra", None)
            if isinstance(extra, dict):
                result.update(extra)
            return result
        if run_context is not None and hasattr(run_context, "__dict__"):
            result = dict(vars(run_context))
            extra = result.pop("extra", None)
            if isinstance(extra, dict):
                result.update(extra)
            return result
        metadata = getattr(inputs, "metadata", None)
        if isinstance(metadata, dict):
            return metadata.get("run_context", {})
        return {}

    @staticmethod
    def _extract_usage(ctx: AgentCallbackContext) -> Optional[Dict[str, int]]:
        """Extract per-call token usage.

        ``after_model_call`` receives ``ModelCallInputs`` whose ``response`` is
        the ``AssistantMessage`` carrying ``usage_metadata`` (fields:
        ``input_tokens`` / ``output_tokens`` / ``total_tokens`` /
        ``cache_tokens``).  Map it onto the GoalRecord ``TokenUsage`` shape
        (which uses ``cached_input_tokens``).  Falls back to the legacy
        ``result["usage"]`` dict for other input shapes.
        """
        inputs = ctx.inputs

        response = getattr(inputs, "response", None)
        usage_meta = getattr(response, "usage_metadata", None)
        if usage_meta is not None:
            get = (
                usage_meta.get
                if isinstance(usage_meta, dict)
                else lambda key, default=0: getattr(usage_meta, key, default)
            )
            input_tokens = int(get("input_tokens", 0) or 0)
            output_tokens = int(get("output_tokens", 0) or 0)
            cached = int(get("cache_tokens", 0) or 0)
            if input_tokens or output_tokens or cached:
                return {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cached_input_tokens": cached,
                }

        result = getattr(inputs, "result", None)
        if isinstance(result, dict):
            return result.get("usage")
        return None

    def _extract_attempt_context(
            self, ctx: AgentCallbackContext,
    ) -> str:
        """Serialize the current attempt's model context for assessment."""
        if self._current_attempt_messages:
            return self._format_context_messages(self._current_attempt_messages)

        context = getattr(ctx, "context", None)
        messages_getter = getattr(context, "get_messages", None)
        if not callable(messages_getter):
            return ""
        try:
            messages = messages_getter(with_history=False)
        except TypeError:
            messages = messages_getter()
        return self._format_context_messages(messages)

    def _capture_attempt_model_context(
            self, ctx: AgentCallbackContext,
    ) -> None:
        """Keep the latest actual LLM request/response for goal assessment."""
        inputs = ctx.inputs
        messages = getattr(inputs, "messages", None)
        if not isinstance(messages, list):
            return

        captured = list(messages)
        response = getattr(inputs, "response", None)
        if response is not None:
            captured.append(response)
        self._current_attempt_messages = captured

    @classmethod
    def _format_context_messages(cls, messages: Any) -> str:
        if not isinstance(messages, list):
            return ""
        lines: List[str] = []
        for index, message in enumerate(messages, start=1):
            data = cls._message_to_assessment_dict(message)
            if not data:
                continue
            lines.append(
                f"[{index}] "
                + json.dumps(data, ensure_ascii=False, default=str)
            )
        text = "\n".join(lines)
        if len(text) <= _ATTEMPT_CONTEXT_MAX_CHARS:
            return text
        # Keep the tail (latest model window) for assessment relevance.
        omitted = len(text) - _ATTEMPT_CONTEXT_MAX_CHARS
        return (
                f"...[truncated {omitted} earlier chars]...\n"
                + text[-_ATTEMPT_CONTEXT_MAX_CHARS:]
        )

    @staticmethod
    def _message_to_assessment_dict(message: Any) -> Dict[str, Any]:
        if isinstance(message, dict):
            return dict(message)

        dump = getattr(message, "model_dump", None)
        if callable(dump):
            data = dump()
            return data if isinstance(data, dict) else {}

        if hasattr(message, "__dict__"):
            data = {
                key: value
                for key, value in vars(message).items()
                if value is not None and value != ""
            }
            return data if isinstance(data.get("role"), str) else {}

        return {}

    # -- private helpers (promise detection) --

    def _notify_evaluator(
            self, ctx: AgentCallbackContext, text: str,
    ) -> None:
        coordinator = getattr(
            ctx.agent, "loop_coordinator", None,
        )
        if coordinator is None:
            return
        ev = coordinator.get_completion_promise_evaluator()
        if ev is not None:
            ev.notify_fulfilled(text)

    @staticmethod
    def _extract_output(
            ctx: AgentCallbackContext,
    ) -> Optional[str]:
        inputs = ctx.inputs
        result = getattr(inputs, "result", None)
        if not isinstance(result, dict):
            return None
        output = result.get("output")
        return str(output) if output is not None else None


def _normalize(text: str) -> str:
    """Collapse whitespace for promise comparison."""
    return " ".join(text.split())


def extract_promise_block(text: str) -> Optional[str]:
    """Extract the first promise block body from text."""
    if not text:
        return None
    match = PROMISE_TAG_PATTERN.search(text)
    if match is None:
        return None
    return match.group(1).strip()


def promise_matches(block: str, expected: str) -> bool:
    """Return True when a promise block starts with expected."""
    if not block or not expected:
        return False
    expected_norm = _normalize(expected)
    block_lines = [
        line.strip()
        for line in block.splitlines()
        if line.strip()
    ]
    first_line = block_lines[0] if block_lines else block.strip()
    first_norm = _normalize(first_line)
    if first_norm == expected_norm:
        return True
    return first_norm.startswith(f"{expected_norm} ")


__all__ = [
    "TaskCompletionRail",
    "extract_promise_block",
    "promise_matches",
]
