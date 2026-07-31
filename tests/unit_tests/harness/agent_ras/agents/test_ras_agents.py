# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""RASAgents unit tests — platform-agnostic invoke_skill orchestration."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.harness.agent_ras.agents.base import NoOpAgentAdapter
from openjiuwen.harness.agent_ras.agents.ras_agents import RASAgents
from openjiuwen.harness.agent_ras.detectors.skill_verdicts import (
    LlmLoopDetectionVerdict,
    ThinkingLoopFault,
)

_DETECTION_JSON = '{"abnormal":true,"primary_fault":"semantic_deadlock","confidence":0.9,"rationale":"looping"}'
_RECOVERY_JSON = '{"abnormal":false,"primary_fault":"none","confidence":0.0,"rationale":"review done"}'


def _valid_detection_verdict() -> LlmLoopDetectionVerdict:
    return LlmLoopDetectionVerdict(
        abnormal=True,
        primary_fault=ThinkingLoopFault.SEMANTIC_DEADLOCK,
        confidence=0.9,
        rationale="looping",
        raw={"abnormal": True, "primary_fault": "semantic_deadlock", "confidence": 0.9, "rationale": "looping"},
    )


def _valid_recovery_verdict() -> LlmLoopDetectionVerdict:
    return LlmLoopDetectionVerdict(
        abnormal=False,
        primary_fault=ThinkingLoopFault.NONE,
        confidence=0.0,
        rationale="review done",
        raw={"abnormal": False, "primary_fault": "none", "confidence": 0.0, "rationale": "review done"},
    )


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def test_init_defaults_to_noop_adapter() -> None:
    ras = RASAgents()
    assert isinstance(ras._adapter, NoOpAgentAdapter)


def test_init_accepts_custom_adapter() -> None:
    adapter = MagicMock()
    ras = RASAgents(adapter=adapter)
    assert ras._adapter is adapter


# ---------------------------------------------------------------------------
# timeout <= 0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_skill_non_positive_timeout_returns_empty() -> None:
    adapter = AsyncMock()
    ras = RASAgents(adapter=adapter)
    result = await ras.invoke_skill(
        role="detection",
        skill_name="llm-loop-detection",
        payload="payload",
        timeout=0,
    )
    assert result == {}
    adapter.run.assert_not_called()


# ---------------------------------------------------------------------------
# asyncio.TimeoutError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_skill_timeout_returns_empty() -> None:
    async def _slow(_role, _skill_name, _query):
        await asyncio.sleep(999)
        return _DETECTION_JSON

    adapter = AsyncMock()
    adapter.run = _slow
    ras = RASAgents(adapter=adapter)
    result = await ras.invoke_skill(
        role="detection",
        skill_name="llm-loop-detection",
        payload="payload",
        timeout=0.001,
    )
    assert result == {}


# ---------------------------------------------------------------------------
# adapter exception
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_skill_adapter_exception_returns_empty() -> None:
    adapter = AsyncMock()
    adapter.run.side_effect = RuntimeError("boom")
    ras = RASAgents(adapter=adapter)
    result = await ras.invoke_skill(
        role="detection",
        skill_name="llm-loop-detection",
        payload="payload",
        timeout=5.0,
    )
    assert result == {}


# ---------------------------------------------------------------------------
# empty / None results
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["{}", "", None, {}])
async def test_invoke_skill_empty_result_returns_empty(raw) -> None:
    adapter = AsyncMock()
    adapter.run.return_value = raw
    ras = RASAgents(adapter=adapter)
    result = await ras.invoke_skill(
        role="detection",
        skill_name="llm-loop-detection",
        payload="payload",
        timeout=5.0,
    )
    assert result == {}


# ---------------------------------------------------------------------------
# normal detection path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_skill_detection_normal_returns_verdict_dict() -> None:
    adapter = AsyncMock()
    adapter.run.return_value = _DETECTION_JSON
    ras = RASAgents(adapter=adapter)
    result = await ras.invoke_skill(
        role="detection",
        skill_name="llm-loop-detection",
        payload="excerpt text",
        timeout=5.0,
    )
    assert result.get("abnormal") is True
    assert result["primary_fault"] == "semantic_deadlock"


# ---------------------------------------------------------------------------
# normal recovery path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_skill_recovery_normal_returns_verdict_dict() -> None:
    adapter = AsyncMock()
    adapter.run.return_value = _RECOVERY_JSON
    ras = RASAgents(adapter=adapter)
    result = await ras.invoke_skill(
        role="recovery",
        skill_name="llm-loop-review",
        payload="recovery payload",
        timeout=5.0,
    )
    assert result.get("abnormal") is False
    assert result["primary_fault"] == "none"


# ---------------------------------------------------------------------------
# task_block differs between detection and recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detection_uses_judgment_task_block() -> None:
    adapter = AsyncMock()
    adapter.run.return_value = _DETECTION_JSON
    ras = RASAgents(adapter=adapter)
    await ras.invoke_skill(
        role="detection",
        skill_name="llm-loop-detection",
        payload="text",
        timeout=5.0,
    )
    call_args = adapter.run.call_args
    assert "待判定 excerpt" in call_args.kwargs["query"]


@pytest.mark.asyncio
async def test_recovery_uses_recovery_task_block() -> None:
    adapter = AsyncMock()
    adapter.run.return_value = _RECOVERY_JSON
    ras = RASAgents(adapter=adapter)
    await ras.invoke_skill(
        role="recovery",
        skill_name="llm-loop-review",
        payload="text",
        timeout=5.0,
    )
    call_args = adapter.run.call_args
    assert "恢复材料" in call_args.kwargs["query"]


# ---------------------------------------------------------------------------
# warmup_members delegates to adapter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_warmup_members_delegates_to_adapter() -> None:
    adapter = AsyncMock()
    ras = RASAgents(adapter=adapter)
    await ras.warmup_members(("detection", "recovery"))
    adapter.warmup_members.assert_called_once_with(("detection", "recovery"))
