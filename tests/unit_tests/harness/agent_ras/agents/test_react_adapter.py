# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ReActAgentAdapter unit tests — member lifecycle and degrade paths."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.harness.agent_ras.agents.react_adapter import (
    ReActAgentAdapter,
    _extract_invoke_payload,
)


def _fake_agent() -> MagicMock:
    agent = MagicMock()
    agent.invoke = AsyncMock(
        return_value={"output": json.dumps({"abnormal": True, "primary_fault": "text_degradation"})}
    )
    return agent


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def test_init_stores_model_and_empty_members() -> None:
    model = MagicMock()
    adapter = ReActAgentAdapter(model=model)
    assert adapter._model is model
    assert adapter._members == {}


def test_init_no_model() -> None:
    adapter = ReActAgentAdapter()
    assert adapter._model is None


# ---------------------------------------------------------------------------
# _get_or_create_member caching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_or_create_member_caches_agent() -> None:
    with patch(
        "openjiuwen.harness.agent_ras.agents.react_adapter.ReliabilityJudgeAgent",
    ) as mock_cls:
        agent_a = _fake_agent()
        mock_cls.return_value = agent_a

        adapter = ReActAgentAdapter(model=MagicMock())
        member1 = await adapter._get_or_create_member("detection")
        member2 = await adapter._get_or_create_member("detection")

        assert member1 is member2
        assert mock_cls.call_count == 1


@pytest.mark.asyncio
async def test_get_or_create_member_returns_none_when_no_model() -> None:
    adapter = ReActAgentAdapter()
    member = await adapter._get_or_create_member("detection")
    assert member is None


# ---------------------------------------------------------------------------
# run (integration through adapter)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_returns_parsed_payload() -> None:
    with patch(
        "openjiuwen.harness.agent_ras.agents.react_adapter.ReliabilityJudgeAgent",
    ) as mock_cls:
        agent = _fake_agent()
        mock_cls.return_value = agent

        adapter = ReActAgentAdapter(model=MagicMock())
        result = await adapter.run(
            role="detection",
            skill_name="llm-loop-detection",
            query="test",
        )
        assert isinstance(result, dict)
        assert result["abnormal"] is True
        assert result["primary_fault"] == "text_degradation"


@pytest.mark.asyncio
async def test_run_no_model_returns_empty_json() -> None:
    adapter = ReActAgentAdapter()
    result = await adapter.run(
        role="detection",
        skill_name="llm-loop-detection",
        query="test",
    )
    assert result == "{}"


# ---------------------------------------------------------------------------
# warmup_members
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_warmup_members_creates_members() -> None:
    with patch(
        "openjiuwen.harness.agent_ras.agents.react_adapter.ReliabilityJudgeAgent",
    ) as mock_cls:
        agent = _fake_agent()
        mock_cls.return_value = agent

        adapter = ReActAgentAdapter(model=MagicMock())
        await adapter.warmup_members(("detection", "recovery"))

        assert "detection" in adapter._members
        assert "recovery" in adapter._members
        assert mock_cls.call_count == 2


@pytest.mark.asyncio
async def test_warmup_members_survives_exception() -> None:
    with patch(
        "openjiuwen.harness.agent_ras.agents.react_adapter.ReliabilityJudgeAgent",
    ) as mock_cls:
        mock_cls.side_effect = RuntimeError("init failed")

        adapter = ReActAgentAdapter(model=MagicMock())
        await adapter.warmup_members(("detection",))

        assert "detection" not in adapter._members


# ---------------------------------------------------------------------------
# _extract_invoke_payload fallbacks
# ---------------------------------------------------------------------------


def test_extract_invoke_payload_from_dict_with_output_key() -> None:
    result = _extract_invoke_payload({"output": json.dumps({"abnormal": False})})
    assert result == {"abnormal": False}


def test_extract_invoke_payload_from_string() -> None:
    result = _extract_invoke_payload('{"x": 1}')
    assert result == {"x": 1}


def test_extract_invoke_payload_unparseable_returns_empty() -> None:
    result = _extract_invoke_payload(MagicMock())
    assert result == "{}"
