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
- Injecting the goal protocol prompt section (before_model_call)
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
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from openjiuwen.core.foundation.tool import Tool
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    ToolCallInputs,
)
from openjiuwen.harness.prompts.sections.task_completion import (
    build_completion_signal_section,
)
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.schema.stop_condition import (
    CompletionPromiseEvaluator,
    MaxRoundsEvaluator,
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
        timeout_seconds: Optional[float] = None,
        evaluators: Optional[
            List[StopConditionEvaluator]
        ] = None,
        goal_manager: Optional[GoalManager] = None,
        goal_language: str = "cn",
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

    # -- lifecycle hooks --

    async def before_model_call(
        self, ctx: AgentCallbackContext,
    ) -> None:
        """Inject prompt sections.

        - Always: completion-signal section (when promise is configured).
        - Goal support: static goal protocol section.
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

        if self._goal_manager is not None and builder is not None:
            from openjiuwen.harness.prompts.sections.goal import (
                build_goal_protocol_section,
            )

            language = getattr(builder, "language", self._goal_language)
            section = build_goal_protocol_section(language)
            builder.add_section(section)

    async def before_task_iteration(
        self, ctx: AgentCallbackContext,
    ) -> None:
        """Apply task_instruction template or goal query replacement.

        For goal rounds: validate goal state and replace query with
        <goal_task> XML.
        For normal rounds: apply task_instruction on the first iteration.
        """
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
