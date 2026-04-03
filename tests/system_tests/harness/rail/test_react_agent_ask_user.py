# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import pytest

from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.rails import AskUserRail

from tests.system_tests.agent.react_agent.interrupt.test_base import (
    assert_interrupt_result,
    assert_answer_result,
    API_KEY,
    API_BASE,
    MODEL_PROVIDER,
    MODEL_NAME,
)


@pytest.mark.asyncio
@pytest.mark.skipif(not API_KEY or not API_BASE, reason="API_KEY and API_BASE required")
async def test_hitl_ask_user_rail():
    """AskUserRail: ask_user tool interrupts, user responds, execution continues

    Flow: ask_user intercepted -> user provides answer -> complete
    """
    await Runner.start()
    try:
        agent = ReActAgent(card=AgentCard(id="ask_user_agent"))
        config = ReActAgentConfig()
        config.configure_model_client(
            provider=MODEL_PROVIDER,
            api_key=API_KEY,
            api_base=API_BASE,
            model_name=MODEL_NAME,
        )
        config.configure_prompt_template([
            {"role": "system", "content": ""}
        ])
        agent.configure(config)

        rail = AskUserRail()
        await agent.register_rail(rail)

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
