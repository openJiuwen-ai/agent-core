# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""DeepAgentAdapter / RASAgents unit tests for L3 skill sandbox kwargs."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.harness.agent_ras.agents.base import skills_dir_for_role
from openjiuwen.harness.agent_ras.agents.deep_agent_adapter import (
    AdapterConfig,
    DeepAgentAdapter,
)
from openjiuwen.harness.agent_ras.agents.ras_agents import RASAgents


class TestDeepAgentAdapterCreateKwargs:
    @pytest.mark.asyncio
    async def test_create_deep_agent_disables_ras_and_inlines_skill(self):
        captured: dict = {}

        def fake_create_deep_agent(**kwargs):
            captured.update(kwargs)
            return MagicMock(name="reliability_detection")

        adapter = DeepAgentAdapter(cfg=AdapterConfig(), model=MagicMock())
        with patch(
            "openjiuwen.harness.factory.create_deep_agent",
            side_effect=fake_create_deep_agent,
        ):
            member = await adapter.get_or_create_member("detection")

        assert member is not None
        skills_dir = str(skills_dir_for_role("detection").resolve())
        assert captured.get("agent_ras") is False
        assert "allowed_paths" not in captured
        assert captured.get("rails") == []
        assert str(captured["workspace"].root_path) == skills_dir
        prompt = captured.get("system_prompt") or ""
        assert "禁止调用 skill_tool" in prompt
        assert "已由宿主内联提供" in prompt


class TestDeepAgentAdapterInvokeIsolation:
    @pytest.mark.asyncio
    async def test_invoke_passes_unique_conversation_id_and_rebinds_cwd(self):
        captured_inputs: list[dict] = []
        member = MagicMock()
        member.is_initialized = MagicMock(return_value=True)

        async def _fake_invoke(inputs):
            captured_inputs.append(inputs)
            return {
                "result_type": "answer",
                "output": (
                    '{"abnormal": false, "primary_fault": "none", '
                    '"confidence": 0.9, "rationale": "ok"}'
                ),
            }

        member.invoke = AsyncMock(side_effect=_fake_invoke)

        adapter = DeepAgentAdapter(cfg=AdapterConfig(), model=MagicMock())
        adapter._members["detection"] = member
        skills_dir = str(skills_dir_for_role("detection").resolve())

        with patch(
            "openjiuwen.harness.agent_ras.agents.deep_agent_adapter.init_cwd"
        ) as mock_init_cwd:
            result = await adapter.invoke_skill(
                role="detection",
                skill_name="llm-loop-detection",
                payload="excerpt-text",
                timeout=5.0,
            )

        assert result.get("abnormal") is False
        assert result.get("fail_open_reason") is None
        assert len(captured_inputs) == 1
        cid = captured_inputs[0].get("conversation_id")
        assert isinstance(cid, str)
        assert cid.startswith("ras-detection-")
        query = captured_inputs[0].get("query") or ""
        assert "已内联" in query
        assert "llm-loop-detection" in query
        assert "excerpt-text" in query
        assert "primary_fault" in query  # SKILL body field
        assert "禁止调用 skill_tool" in query
        mock_init_cwd.assert_called_once_with(skills_dir, workspace=skills_dir)

    @pytest.mark.asyncio
    async def test_timeout_fail_open(self, caplog):
        member = MagicMock()
        member.is_initialized = MagicMock(return_value=False)

        async def _hang(_inputs):
            await asyncio.sleep(10)

        member.invoke = _hang
        adapter = DeepAgentAdapter(cfg=AdapterConfig(), model=MagicMock())
        adapter._members["detection"] = member

        with patch(
            "openjiuwen.harness.agent_ras.agents.deep_agent_adapter.init_cwd"
        ), caplog.at_level("WARNING"):
            result = await adapter.invoke_skill(
                role="detection",
                skill_name="llm-loop-detection",
                payload="excerpt",
                timeout=0.05,
            )

        assert result == {}
        assert any(
            "timed out" in r.message and "fail_open=True" in r.message
            for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_cancelled_propagates(self):
        member = MagicMock()
        member.is_initialized = MagicMock(return_value=True)
        entered = asyncio.Event()

        async def _block(_inputs):
            entered.set()
            await asyncio.sleep(30)

        member.invoke = _block
        adapter = DeepAgentAdapter(cfg=AdapterConfig(), model=MagicMock())
        adapter._members["detection"] = member

        with patch(
            "openjiuwen.harness.agent_ras.agents.deep_agent_adapter.init_cwd"
        ):
            task = asyncio.create_task(
                adapter.invoke_skill(
                    role="detection",
                    skill_name="llm-loop-detection",
                    payload="excerpt",
                    timeout=5.0,
                )
            )
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    @pytest.mark.asyncio
    async def test_warmup_creates_member(self):
        member = MagicMock()
        adapter = DeepAgentAdapter(cfg=AdapterConfig(), model=MagicMock())
        with patch.object(
            adapter,
            "get_or_create_member",
            new=AsyncMock(return_value=member),
        ) as mock_create:
            await adapter.warmup_members(("detection",))
        mock_create.assert_awaited_once_with("detection")


class TestRASAgentsFailOpen:
    @pytest.mark.asyncio
    async def test_timeout_returns_empty(self):
        member = MagicMock()
        member.is_initialized = MagicMock(return_value=True)

        async def _hang(_inputs):
            await asyncio.sleep(10)

        member.invoke = _hang
        adapter = DeepAgentAdapter(cfg=AdapterConfig(), model=MagicMock())
        adapter._members["detection"] = member
        agents = RASAgents(adapter=adapter)

        with patch(
            "openjiuwen.harness.agent_ras.agents.deep_agent_adapter.init_cwd"
        ):
            result = await agents.invoke_skill(
                role="detection",
                skill_name="llm-loop-detection",
                payload="excerpt",
                timeout=0.05,
            )

        assert result == {}

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        adapter = MagicMock()
        adapter.invoke_skill = AsyncMock(side_effect=RuntimeError("boom"))
        agents = RASAgents(adapter=adapter)

        result = await agents.invoke_skill(
            role="detection",
            skill_name="llm-loop-detection",
            payload="excerpt",
            timeout=1.0,
        )

        assert result == {}
