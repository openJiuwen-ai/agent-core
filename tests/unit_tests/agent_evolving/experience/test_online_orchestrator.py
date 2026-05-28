# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for online evolution orchestration result semantics."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.agent_evolving.experience.online_orchestrator import OnlineEvolutionOrchestrator
from openjiuwen.agent_evolving.signal import make_evolution_signal


def _make_signal(skill_name: str = "skill-a"):
    return make_evolution_signal(
        signal_type="tool_failure",
        section="Troubleshooting",
        excerpt="command failed",
        skill_name=skill_name,
        source="passive_conversation",
    )


def _make_orchestrator(*, skill_exists: bool = True, process_result=None):
    store = Mock()
    store.skill_exists.return_value = skill_exists
    store.read_skill_content = AsyncMock(return_value="# skill")
    store.get_pending_records = AsyncMock(side_effect=[[], [], []])

    updater = Mock()
    updater.bind = Mock()
    updater.process = AsyncMock(return_value={} if process_result is None else process_result)

    manager = Mock()
    manager.build_local_apply_preview.return_value = SimpleNamespace(records=[], apply_results=[])
    manager.stage_apply_results = Mock()
    manager.approve_request = AsyncMock()

    orchestrator = OnlineEvolutionOrchestrator(
        store=store,
        updater=updater,
        manager=manager,
        skill_ops={},
        stage_source="experience_updater",
    )
    return orchestrator, store, updater, manager


@pytest.mark.asyncio
async def test_evolve_returns_no_records_result_when_preview_has_no_records():
    orchestrator, _, _, manager = _make_orchestrator()

    result = await orchestrator.evolve(
        skill_name="skill-a",
        signals=[_make_signal()],
        requires_approval=True,
    )

    assert result.status == "no_evolution_no_records"
    assert result.skill_name == "skill-a"
    assert result.request is None
    assert "no applied updates" in result.message
    manager.stage_apply_results.assert_not_called()


@pytest.mark.asyncio
async def test_evolve_returns_staged_result_when_records_are_generated():
    orchestrator, _, _, manager = _make_orchestrator()
    request = SimpleNamespace(request_id="req-1")
    manager.stage_apply_results.return_value = request
    orchestrator._generate_local_apply_preview = AsyncMock(
        return_value=SimpleNamespace(records=["record"], apply_results=["apply-result"])
    )

    result = await orchestrator.evolve(
        skill_name="skill-a",
        signals=[_make_signal()],
        requires_approval=True,
    )

    assert result.status == "staged"
    assert result.request is request
    manager.approve_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_evolve_returns_auto_approved_result_when_approval_is_not_required():
    orchestrator, _, _, manager = _make_orchestrator()
    request = SimpleNamespace(request_id="req-1")
    manager.stage_apply_results.return_value = request
    orchestrator._generate_local_apply_preview = AsyncMock(
        return_value=SimpleNamespace(records=["record"], apply_results=["apply-result"])
    )

    result = await orchestrator.evolve(
        skill_name="skill-a",
        signals=[_make_signal()],
        requires_approval=False,
    )

    assert result.status == "auto_approved"
    assert result.request is request
    manager.approve_request.assert_awaited_once_with("req-1")


@pytest.mark.asyncio
async def test_evolve_returns_skipped_no_input_without_skill_or_signals():
    orchestrator, _, updater, _ = _make_orchestrator()

    result = await orchestrator.evolve(
        skill_name="",
        signals=[],
        requires_approval=True,
    )

    assert result.status == "skipped_no_input"
    assert result.request is None
    updater.process.assert_not_awaited()


@pytest.mark.asyncio
async def test_evolve_returns_skipped_skill_not_found():
    orchestrator, store, updater, _ = _make_orchestrator(skill_exists=False)

    result = await orchestrator.evolve(
        skill_name="missing-skill",
        signals=[_make_signal("missing-skill")],
        requires_approval=True,
    )

    assert result.status == "skipped_skill_not_found"
    assert result.skill_name == "missing-skill"
    assert result.request is None
    store.skill_exists.assert_called_once_with("missing-skill")
    updater.process.assert_not_awaited()
