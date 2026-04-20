# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""TaskCompletionRail — unified task-completion strategy Rail.

Responsibilities:
1. Carry loop-strategy parameters (replacing StopCondition).
2. before_model_call: inject completion-signal section into
   SystemPromptBuilder.
3. before_task_iteration: apply task_instruction template to
   the first-round query.
4. after_task_iteration: detect completion promise in output
   and notify CompletionPromiseEvaluator.

Only takes effect when ``enable_task_loop=True``.
"""
from __future__ import annotations

import logging
import re
from typing import List, Optional

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
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

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------
# Promise tag pattern
# ----------------------------------------------------------------
PROMISE_TAG_PATTERN = re.compile(
    r"<promise>\s*(.*?)\s*</promise>",
    re.DOTALL | re.IGNORECASE,
)


class TaskCompletionRail(DeepAgentRail):
    """Task-completion strategy Rail.

    Carries loop-strategy parameters and implements the three
    lifecycle hooks that drive completion detection and prompt
    injection.

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
    """

    priority = 10

    def __init__(
        self,
        task_instruction: Optional[str] = None,
        completion_promise: Optional[str] = None,
        max_rounds: Optional[int] = None,
        timeout_seconds: Optional[float] = None,
        evaluators: Optional[
            List[StopConditionEvaluator]
        ] = None,
    ) -> None:
        super().__init__()
        self.task_instruction = task_instruction
        self.completion_promise = completion_promise
        self.max_rounds = max_rounds
        self.timeout_seconds = timeout_seconds
        self._extra_evaluators: List[StopConditionEvaluator] = (
            evaluators or []
        )

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
                    self.completion_promise
                )
            )
        result.extend(self._extra_evaluators)
        return result

    # -- lifecycle hooks --

    async def before_model_call(
        self, ctx: AgentCallbackContext,
    ) -> None:
        """Inject completion-signal section into SystemPromptBuilder.

        Follows the same pattern as TaskPlanningRail and
        SkillUseRail: ``add_section`` is idempotent (same name
        overwrites), so repeated calls per model call are safe.
        """
        if not self.completion_promise:
            return
        builder = getattr(
            ctx.agent, "system_prompt_builder", None
        )
        if builder is None:
            return
        language = getattr(builder, "language", "cn")
        section = build_completion_signal_section(
            language,
            self.completion_promise,
        )
        builder.add_section(section)

    async def before_task_iteration(
        self, ctx: AgentCallbackContext,
    ) -> None:
        """Apply task_instruction template to the iteration query.

        The template is only applied on the first, non-follow-up
        iteration.  Follow-up queries (``is_follow_up=True``) are
        passed through unchanged.
        """
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

    async def after_task_iteration(
        self, ctx: AgentCallbackContext,
    ) -> None:
        """Detect completion promise in the iteration output.

        When the promise tag is found and the token matches,
        notifies the ``CompletionPromiseEvaluator`` so the outer
        loop stops before the next round.
        """
        if not self.completion_promise:
            return
        content = self._extract_output(ctx)
        if not content:
            return
        match = PROMISE_TAG_PATTERN.search(content)
        if match is None:
            return
        matched = _normalize(match.group(1))
        expected = _normalize(self.completion_promise)
        if matched != expected:
            return
        logger.info(
            "TaskCompletionRail: promise fulfilled: %r",
            matched,
        )
        self._notify_evaluator(ctx, matched)

    # -- private helpers --

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


__all__ = ["TaskCompletionRail"]
