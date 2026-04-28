# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""DeepAgent + PermissionInterruptRail：工具防护 ASK → interrupt → 恢复。

使用 ``MockLLMModel`` 注入**原生** ``tool_calls``（与 ``test_deep_agent_ask_user`` 单元测试
同套路）。仅依赖真实 API 时，许多模型会输出 ``<tool_call>``/markdown 等伪工具语法，
运行时不会变成 ``ToolCall``，护栏 ``before_tool_call`` 不会触发，首轮会得到 ``answer``
而非 ``interrupt``，故本文件不再绑定 ``API_KEY``。
"""

import json
import os
import uuid
from unittest.mock import patch

import pytest

from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.interrupt.response import ToolCallInterruptRequest
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.rails.interrupt.confirm_rail import ConfirmPayload

from tests.system_tests.agent.react_agent.interrupt.test_base import (
    assert_answer_result,
    assert_interrupt_result,
)
from tests.unit_tests.fixtures.mock_llm import (
    MockLLMModel,
    create_text_response,
    create_tool_call_response,
)


def _permissions_ask_read_file() -> dict:
    """Legacy permissions：仅 read_file 为 ask，其余 allow。"""
    return {
        "enabled": True,
        "defaults": {"*": "allow"},
        "tools": {
            "read_file": {"*": "ask"},
        },
    }


def _fake_model() -> Model:
    return Model(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI",
            api_key="sk-fake-permission-st",
            api_base="https://api.openai.com/v1",
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(model="gpt-4o-mini"),
    )


@pytest.mark.asyncio
async def test_hitl_tool_permission_interrupt_read_file_ask(tmp_path):
    """read_file=ask → interrupt（ConfirmPayload schema）→ dict 批准 → answer。"""
    os.environ.setdefault("LLM_SSL_VERIFY", "false")
    await Runner.start()
    try:
        fname = "hello_permission_st.txt"
        (tmp_path / fname).write_text("permission-system-test\n", encoding="utf-8")

        card = AgentCard(
            id=f"perm_read_{uuid.uuid4().hex[:12]}",
            name="PermDeepAgentRead",
        )
        model = _fake_model()
        mock_llm = MockLLMModel()
        tc_id = "tool_call_read_perm_001"
        mock_llm.set_responses(
            [
                create_tool_call_response(
                    "read_file",
                    json.dumps({"file_path": fname}),
                    tool_call_id=tc_id,
                ),
                create_text_response("已读，第一行是 permission-system-test。"),
                create_text_response("fallback"),
            ]
        )

        agent = create_deep_agent(
            model=model,
            card=card,
            workspace=str(tmp_path),
            permissions=_permissions_ask_read_file(),
            enable_task_loop=False,
            max_iterations=10,
            system_prompt="测试：按 mock 返回执行工具即可。",
        )

        cid = f"deep_perm_mock_{uuid.uuid4().hex[:10]}"
        with patch(
            "openjiuwen.core.foundation.llm.model.Model.stream",
            side_effect=mock_llm.stream,
        ), patch(
            "openjiuwen.core.foundation.llm.model.Model.invoke",
            side_effect=mock_llm.invoke,
        ):
            result1 = await Runner.run_agent(
                agent=agent,
                inputs={"query": f"请读取 {fname}", "conversation_id": cid},
            )
        interrupt_ids, state_list = assert_interrupt_result(result1, expected_count=1)
        tool_call_id = interrupt_ids[0]

        raw = state_list[0].payload.value if hasattr(state_list[0], "payload") else None
        assert raw is not None
        assert isinstance(raw, ToolCallInterruptRequest)
        assert raw.tool_name == "read_file"
        props = (raw.payload_schema or {}).get("properties") or {}
        for key in ("approved", "feedback", "auto_confirm"):
            assert key in props, f"payload_schema.properties missing {key!r}"

        interactive_input = InteractiveInput()
        interactive_input.update(
            tool_call_id,
            {"approved": True, "feedback": "", "auto_confirm": False},
        )

        with patch(
            "openjiuwen.core.foundation.llm.model.Model.stream",
            side_effect=mock_llm.stream,
        ), patch(
            "openjiuwen.core.foundation.llm.model.Model.invoke",
            side_effect=mock_llm.invoke,
        ):
            result2 = await Runner.run_agent(
                agent=agent,
                inputs={"query": interactive_input, "conversation_id": cid},
            )
        assert_answer_result(result2)

    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_hitl_tool_permission_interrupt_resume_with_confirm_payload_object(tmp_path):
    """恢复阶段使用 ``ConfirmPayload`` 对象（与 ConfirmInterruptRail 一致）。"""
    os.environ.setdefault("LLM_SSL_VERIFY", "false")
    await Runner.start()
    try:
        fname = "hello_permission_st_obj.txt"
        (tmp_path / fname).write_text("permission-object-resume\n", encoding="utf-8")

        card = AgentCard(
            id=f"perm_read_obj_{uuid.uuid4().hex[:12]}",
            name="PermDeepAgentReadObj",
        )
        model = _fake_model()
        mock_llm = MockLLMModel()
        tc_id = "tool_call_read_perm_002"
        mock_llm.set_responses(
            [
                create_tool_call_response(
                    "read_file",
                    json.dumps({"file_path": fname}),
                    tool_call_id=tc_id,
                ),
                create_text_response("OK"),
                create_text_response("fallback"),
            ]
        )

        agent = create_deep_agent(
            model=model,
            card=card,
            workspace=str(tmp_path),
            permissions=_permissions_ask_read_file(),
            enable_task_loop=False,
            max_iterations=10,
            system_prompt="测试：按 mock 返回执行工具即可。",
        )

        cid = f"deep_perm_obj_{uuid.uuid4().hex[:10]}"
        with patch(
            "openjiuwen.core.foundation.llm.model.Model.stream",
            side_effect=mock_llm.stream,
        ), patch(
            "openjiuwen.core.foundation.llm.model.Model.invoke",
            side_effect=mock_llm.invoke,
        ):
            result1 = await Runner.run_agent(
                agent=agent,
                inputs={"query": f"读取 {fname}", "conversation_id": cid},
            )
        interrupt_ids, _state_list = assert_interrupt_result(result1, expected_count=1)
        tool_call_id = interrupt_ids[0]

        interactive_input = InteractiveInput()
        interactive_input.update(tool_call_id, ConfirmPayload(approved=True, feedback=""))

        with patch(
            "openjiuwen.core.foundation.llm.model.Model.stream",
            side_effect=mock_llm.stream,
        ), patch(
            "openjiuwen.core.foundation.llm.model.Model.invoke",
            side_effect=mock_llm.invoke,
        ):
            result2 = await Runner.run_agent(
                agent=agent,
                inputs={"query": interactive_input, "conversation_id": cid},
            )
        assert_answer_result(result2)

    finally:
        await Runner.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
