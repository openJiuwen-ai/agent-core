# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for goal protocol PromptAttachment injection."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from openjiuwen.harness.prompts.prompt_attachment_manager import (
    PromptAttachmentManager,
)
from openjiuwen.harness.prompts.sections.goal import (
    build_goal_protocol_section,
)
from openjiuwen.harness.rails.task_completion_rail import TaskCompletionRail


class _PromptBuilder:
    def __init__(self, language: str = "cn") -> None:
        self.language = language
        self.added_sections: list = []

    def add_section(self, section) -> None:
        self.added_sections.append(section)


def _make_ctx(
    *,
    run_kind: str | None,
    attachment_manager: PromptAttachmentManager | None,
    builder: _PromptBuilder | None = None,
):
    builder = builder or _PromptBuilder()
    agent = SimpleNamespace(
        system_prompt_builder=builder,
        prompt_attachment_manager=attachment_manager,
    )

    inputs = SimpleNamespace(run_kind=run_kind, metadata={})
    ctx = SimpleNamespace(
        agent=agent,
        session=SimpleNamespace(session_id="sess1", get_session_id=lambda: "sess1"),
        inputs=inputs,
        extra={},
    )
    return ctx, agent, builder


def _expected_protocol_content(language: str = "cn") -> str:
    return build_goal_protocol_section(language).render(language)


@pytest.mark.asyncio
async def test_goal_round_upserts_protocol_attachment_not_system_section() -> None:
    manager = PromptAttachmentManager()
    rail = TaskCompletionRail(goal_manager=object())
    rail.attachment_manager = manager
    ctx, _, builder = _make_ctx(run_kind="goal", attachment_manager=manager)

    await rail.before_model_call(ctx)

    assert builder.added_sections == []
    items = await manager.collect_for_session("sess1")
    assert [item.id for item in items] == ["session.sess1.goal_protocol"]
    assert items[0].kind.value == "runtime"
    assert items[0].source == "agent_core.task_completion_rail"
    expected = _expected_protocol_content("cn")
    assert items[0].content == expected
    assert "submit_goal_report" in expected
    assert "goal_task" in expected


@pytest.mark.asyncio
async def test_normal_round_clears_protocol_attachment() -> None:
    manager = PromptAttachmentManager()
    rail = TaskCompletionRail(goal_manager=object())
    rail.attachment_manager = manager
    ctx_goal, _, _ = _make_ctx(run_kind="goal", attachment_manager=manager)
    await rail.before_model_call(ctx_goal)
    assert await manager.get_by_id("session.sess1.goal_protocol") is not None

    ctx_normal, _, builder = _make_ctx(
        run_kind="normal", attachment_manager=manager
    )
    await rail.before_model_call(ctx_normal)

    assert builder.added_sections == []
    assert await manager.get_by_id("session.sess1.goal_protocol") is None
    assert await manager.collect_for_session("sess1") == []


@pytest.mark.asyncio
async def test_second_goal_round_upserts_same_section_id() -> None:
    manager = PromptAttachmentManager()
    rail = TaskCompletionRail(goal_manager=object())
    rail.attachment_manager = manager

    ctx1, _, _ = _make_ctx(run_kind="goal", attachment_manager=manager)
    await rail.before_model_call(ctx1)
    first = await manager.get_by_id("session.sess1.goal_protocol")
    assert first is not None

    ctx_normal, _, _ = _make_ctx(run_kind="normal", attachment_manager=manager)
    await rail.before_model_call(ctx_normal)

    ctx2, _, _ = _make_ctx(run_kind="goal", attachment_manager=manager)
    await rail.before_model_call(ctx2)
    second = await manager.get_by_id("session.sess1.goal_protocol")
    assert second is not None
    assert second.id == first.id
    assert second.content == _expected_protocol_content("cn")


@pytest.mark.asyncio
async def test_skips_protocol_when_no_attachment_manager() -> None:
    rail = TaskCompletionRail(goal_manager=object())
    rail.attachment_manager = None
    ctx, agent, builder = _make_ctx(run_kind="goal", attachment_manager=None)
    agent.prompt_attachment_manager = None

    await rail.before_model_call(ctx)

    assert builder.added_sections == []


@pytest.mark.asyncio
async def test_no_goal_manager_skips_protocol_even_on_goal_run_kind() -> None:
    manager = PromptAttachmentManager()
    rail = TaskCompletionRail(goal_manager=None)
    rail.attachment_manager = manager
    ctx, _, builder = _make_ctx(run_kind="goal", attachment_manager=manager)

    await rail.before_model_call(ctx)

    assert builder.added_sections == []
    assert await manager.collect_for_session("sess1") == []


def test_build_goal_reminder_section_removed() -> None:
    import openjiuwen.harness.prompts.sections.goal as goal_prompts

    assert not hasattr(goal_prompts, "build_goal_reminder_section")
    assert "build_goal_reminder_section" not in goal_prompts.__all__
