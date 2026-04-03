# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import os
from unittest.mock import patch

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.rails import AskUserRail
from tests.unit_tests.agent.react_agent.interrupt.test_base import (
    assert_answer_result,
    assert_interrupt_result,
)
from tests.unit_tests.fixtures.mock_llm import (
    create_text_response,
    create_tool_call_response,
    MockLLMModel,
)


@pytest.mark.asyncio
async def test_hitl_ask_user_rail():
    """AskUserRail: ask_user tool interrupts, user responds, execution continues

    Flow: ask_user intercepted -> user provides answer -> complete
    """
    os.environ.setdefault("LLM_SSL_VERIFY", "false")
    await Runner.start()
    try:
        agent = ReActAgent(card=AgentCard(id="ask_user_agent"))
        config = ReActAgentConfig()
        config.configure_model_client(
            provider="OpenAI",
            api_key="sk-fake",
            api_base="https://api.openai.com/v1",
            model_name="gpt-3.5-turbo",
            verify_ssl=False,
        )
        config.configure_prompt_template([
            {"role": "system", "content": ""}
        ])
        agent.configure(config)

        rail = AskUserRail()
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("ask_user", '{"question": "What is the filename?"}'),
            create_text_response("Got it, the filename is user_answer.txt"),
        ])

        with patch("openjiuwen.core.foundation.llm.model.Model.stream", side_effect=mock_llm.stream), \
                patch("openjiuwen.core.foundation.llm.model.Model.invoke", side_effect=mock_llm.invoke):
            result1 = await Runner.run_agent(
                agent=agent,
                inputs={"query": "Please ask the user for the filename they want. use ask_user tool",
                        "conversation_id": "496"},
            )
            interrupt_ids, state_list = assert_interrupt_result(result1, expected_count=1)
            tool_call_id = interrupt_ids[0]

            payload = state_list[0].payload.value if hasattr(state_list[0], 'payload') else None
            assert payload is not None, "Payload should not be None"
            assert hasattr(payload, 'message'), "Payload should have 'message' attribute"

            from openjiuwen.core.session import InteractiveInput
            interactive_input = InteractiveInput()
            user_answer = {"answer": "user_answer.txt"}
            interactive_input.update(tool_call_id, user_answer)

            result2 = await Runner.run_agent(
                agent=agent,
                inputs={"query": interactive_input, "conversation_id": "496"},
            )

            assert_answer_result(result2)

    finally:
        await Runner.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
