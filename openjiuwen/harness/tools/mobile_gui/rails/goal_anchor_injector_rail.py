# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, List, Optional

from openjiuwen.core.foundation.llm import BaseMessage
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentRail,
    ModelCallInputs,
)
from openjiuwen.harness.tools.mobile_gui.rails.vlm_rail_utils import (
    GOAL_ANCHOR_INJECTOR_STATE_KEY,
    GOAL_ANCHOR_KEY,
)


def _merge_after_leading_system_messages(
    window_messages: List[Any],
    insert: List[Any],
) -> List[Any]:
    if not insert:
        return window_messages
    index = 0
    while index < len(window_messages) and getattr(window_messages[index], "role", None) == "system":
        index += 1
    return window_messages[:index] + insert + window_messages[index:]


class _GoalAnchorWindowMerge:
    __slots__ = ("_inner", "_anchors")

    def __init__(
        self,
        inner: Any,
        anchors: List[Any],
    ) -> None:
        self._inner = inner
        self._anchors = anchors

    def get_messages(self) -> List[Any]:
        return _merge_after_leading_system_messages(
            self._inner.get_messages(),
            self._anchors,
        )

    def get_tools(self) -> Any:
        return self._inner.get_tools()

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)


@dataclass
class _GoalAnchorInjectorState:
    model_ctx: Any
    original_get_context_window: Any


class GoalAnchorInjectorRail(AgentRail):
    """Inject the pinned user goal into the context window when it falls out of the round window."""

    priority: int = 80

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        if not isinstance(ctx.inputs, ModelCallInputs):
            return
        if ctx.context is None:
            return

        anchors = self._collect_anchors(ctx)
        if not anchors:
            return

        model_ctx = ctx.context
        orig_gcw = model_ctx.get_context_window

        async def _gcw_with_goal_anchor(*args: Any, **kwargs: Any) -> Any:
            cw = await orig_gcw(*args, **kwargs)
            return _GoalAnchorWindowMerge(cw, anchors)

        model_ctx.get_context_window = _gcw_with_goal_anchor  # type: ignore[method-assign]
        ctx.extra[GOAL_ANCHOR_INJECTOR_STATE_KEY] = _GoalAnchorInjectorState(
            model_ctx=model_ctx,
            original_get_context_window=orig_gcw,
        )

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        self._restore_context_window(ctx)

    async def on_model_exception(self, ctx: AgentCallbackContext) -> None:
        self._restore_context_window(ctx)

    @staticmethod
    def _collect_anchors(ctx: AgentCallbackContext) -> List[BaseMessage]:
        anchors: List[BaseMessage] = []
        goal_anchor = ctx.extra.get(GOAL_ANCHOR_KEY)

        if goal_anchor is not None:
            anchors.append(copy.deepcopy(goal_anchor))
        return anchors

    @staticmethod
    def _restore_context_window(ctx: AgentCallbackContext) -> None:
        state: Optional[_GoalAnchorInjectorState] = ctx.extra.pop(
            GOAL_ANCHOR_INJECTOR_STATE_KEY, None
        )
        if state is None:
            return
        state.model_ctx.get_context_window = state.original_get_context_window  # type: ignore[method-assign]
