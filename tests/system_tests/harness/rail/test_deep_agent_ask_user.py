# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import pytest

from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness import DeepAgent, DeepAgentConfig
from openjiuwen.harness.rails import AskUserRail, AskUserPayload

from tests.system_tests.agent.react_agent.interrupt.test_base import (
    assert_interrupt_result,
    assert_answer_result,
    API_KEY,
    API_BASE,
    MODEL_PROVIDER,
    MODEL_NAME,
)


def create_model():
    """Create Model instance for testing."""
    client_config = ModelClientConfig(
        client_provider=MODEL_PROVIDER,
        api_key=API_KEY,
        api_base=API_BASE,
        verify_ssl=False,
    )
    model_config = ModelRequestConfig(model=MODEL_NAME)
    return Model(model_client_config=client_config, model_config=model_config)


@pytest.mark.asyncio
@pytest.mark.skipif(not API_KEY or not API_BASE, reason="API_KEY and API_BASE required")
async def test_hitl_ask_user_rail_multi_question():
    """AskUserRail: ask_user tool with multi-question, user responds, execution continues

    Flow: ask_user intercepted with solution options A/B/C -> user provides answer -> complete
    
    This test verifies multi-question support where LLM asks user to choose from 
    solution options (Plan A, B, C) using ask_user tool.
    """
    await Runner.start()
    try:
        card = AgentCard(id="ask_user_deep_agent_multi", name="AskUserDeepAgentMulti")
        agent = DeepAgent(card)

        model = create_model()

        config = DeepAgentConfig(
            model=model,
            enable_task_loop=True,
            max_iterations=5,
        )
        agent.configure(config)

        rail = AskUserRail()
        agent.add_rail(rail)

        # LLM asks user to choose from solution A/B/C
        result1 = await Runner.run_agent(
            agent=agent,
            inputs={"query": "We have three implementation plans. Please use ask_user tool to let the user choose: Plan A (fastest), Plan B (most stable), Plan C (most scalable)",
                    "conversation_id": "500"},
        )
        interrupt_ids, state_list = assert_interrupt_result(result1, expected_count=1)
        tool_call_id = interrupt_ids[0]

        payload = state_list[0].payload.value if hasattr(state_list[0], 'payload') else None
        assert payload is not None, "Payload should not be None"
        assert hasattr(payload, 'questions'), "Payload should have 'questions' attribute"
        assert hasattr(payload, 'payload_schema'), "Payload should have 'payload_schema' attribute"

        questions = payload.questions
        assert questions is not None and len(questions) >= 1, "Should have at least one question"
        first_question = questions[0]
        assert "question" in first_question, "Question should have 'question' field"
        assert "header" in first_question, "Question should have 'header' field"
        assert "options" in first_question, "Question should have 'options' field"
        
        # Verify options contain Plan A/B/C
        options = first_question.get("options", [])
        assert len(options) >= 3, "Should have at least 3 options (Plan A, B, C)"
        option_labels = [opt.get("label", "") for opt in options]
        assert any("A" in label for label in option_labels), "Should have Plan A option"
        assert any("B" in label for label in option_labels), "Should have Plan B option"
        assert any("C" in label for label in option_labels), "Should have Plan C option"
        
        question_text = first_question.get("question", "")

        # User selects Plan B
        interactive_input = InteractiveInput()
        user_answer = {"answers": {question_text: "Plan B"}}
        interactive_input.update(tool_call_id, user_answer)

        result2 = await Runner.run_agent(
            agent=agent,
            inputs={"query": interactive_input, "conversation_id": "500"},
        )

        assert_answer_result(result2)

    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.skipif(not API_KEY or not API_BASE, reason="API_KEY and API_BASE required")
async def test_hitl_ask_user_rail_with_payload_object():
    """AskUserRail: ask_user tool interrupts, user responds with AskUserPayload object, execution continues

    Flow: ask_user intercepted -> user provides AskUserPayload object -> complete
    
    This test verifies that AskUserRail supports both dict and AskUserPayload object formats,
    similar to ConfirmInterruptRail.
    """
    await Runner.start()
    try:
        card = AgentCard(id="ask_user_deep_agent_payload", name="AskUserDeepAgentPayload")
        agent = DeepAgent(card)

        model = create_model()

        config = DeepAgentConfig(
            model=model,
            enable_task_loop=True,
            max_iterations=5,
        )
        agent.configure(config)

        rail = AskUserRail()
        agent.add_rail(rail)

        result1 = await Runner.run_agent(
            agent=agent,
            inputs={"query": "We have three implementation plans. Please use ask_user tool to let the user choose: Plan A (fastest), Plan B (most stable), Plan C (most scalable)",
                    "conversation_id": "501"},
        )
        interrupt_ids, state_list = assert_interrupt_result(result1, expected_count=1)
        tool_call_id = interrupt_ids[0]

        payload = state_list[0].payload.value if hasattr(state_list[0], 'payload') else None
        assert payload is not None, "Payload should not be None"
        assert hasattr(payload, 'questions'), "Payload should have 'questions' attribute"
        assert hasattr(payload, 'payload_schema'), "Payload should have 'payload_schema' attribute"

        questions = payload.questions
        assert questions is not None and len(questions) >= 1, "Should have at least one question"
        first_question = questions[0]
        assert "question" in first_question, "Question should have 'question' field"
        assert "header" in first_question, "Question should have 'header' field"
        question_text = first_question.get("question", "")

        # Use AskUserPayload object instead of dict
        interactive_input = InteractiveInput()
        user_answer = AskUserPayload(answers={question_text: "user_answer_payload.txt"})
        interactive_input.update(tool_call_id, user_answer)

        result2 = await Runner.run_agent(
            agent=agent,
            inputs={"query": interactive_input, "conversation_id": "497"},
        )

        assert_answer_result(result2)

    finally:
        await Runner.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
