# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for the unknown-tool-name self-correcting hint.

Verifies that ``AbilityManager._execute_single_tool_call`` appends a
retry hint to the ``Ability not found in resource_mgr`` error (both in
the exception message and in the ``tool_message`` returned to the LLM),
so the model re-checks the tool name spelling instead of mistaking the
failure for an environment issue and silently falling back.

Field case: ``skill_acceleration_exec`` was misspelled as
``skill_accelation_exec``; the bare error made the model declare the
acceleration channel "not available in this environment" and permanently
diverted a PPT task to the slower standard pipeline.
"""

from __future__ import annotations

import asyncio

from openjiuwen.core.foundation.llm import ToolCall
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.ability_manager import (
    _UNKNOWN_TOOL_NAME_HINT,
    AbilityExecutionError,
    AbilityManager,
)


def _tool_call(name: str, *, args: str = "{}") -> ToolCall:
    return ToolCall(id=f"tc-{name}", type="function", name=name, arguments=args)


def _run_async(coro):
    return asyncio.run(coro)


def test_unknown_tool_error_appends_self_correcting_hint() -> None:
    """The fallback not-found error keeps its stable prefix (log/search
    fingerprint) and carries the typo hint in both the exception message
    and the tool_message handed back to the LLM.
    """

    async def _run():
        await Runner.start()
        am = AbilityManager(owner_id="unknown-tool-hint-1")
        try:
            # Reproduce the field typo: skill_acceleration_exec misspelled.
            tc = _tool_call("skill_accelation_exec")
            raised: AbilityExecutionError | None = None
            try:
                await asyncio.wait_for(
                    am._execute_single_tool_call(tc, session=None),
                    timeout=5.0,
                )
            except AbilityExecutionError as e:
                raised = e
            assert raised is not None
            # Stable log/search fingerprint stays first.
            assert raised.message.startswith("Ability not found in resource_mgr: skill_accelation_exec")
            # Self-correcting hint is appended for the LLM.
            assert _UNKNOWN_TOOL_NAME_HINT.strip() in raised.message
            # The tool_message returned to the model carries the same hint.
            assert raised.tool_message is not None
            assert _UNKNOWN_TOOL_NAME_HINT.strip() in str(raised.tool_message.content)
            assert raised.tool_message.tool_call_id == "tc-skill_accelation_exec"
        finally:
            await Runner.stop()

    _run_async(_run())


def test_registered_tool_unaffected_by_hint_change() -> None:
    """Regression guard: the hint only touches the not-found error path;
    a registered tool still executes normally.
    """

    async def _run():
        await Runner.start()
        am = AbilityManager(owner_id="unknown-tool-hint-2")
        card = ToolCard(
            id="quick_ok",
            name="quick_ok",
            description="quick tool",
            stateless=False,
            idempotent=True,
        )

        async def _func(**_):  # noqa: ANN202
            return "quick_ok:ok"

        tool = LocalFunction(card=card, func=_func)
        try:
            am.add_ability(tool.card, tool)
            tc = _tool_call("quick_ok")
            result, _ = await asyncio.wait_for(
                am._execute_single_tool_call(tc, session=None),
                timeout=5.0,
            )
            assert result == "quick_ok:ok"
        finally:
            am.remove_ability("quick_ok")
            await Runner.stop()

    _run_async(_run())
