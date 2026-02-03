# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Integration tests for Runner with Redis checkpointer.
"""

import asyncio
import copy
import os

import pytest
import pytest_asyncio

from openjiuwen.core.application.workflow_agent import WorkflowAgent
from openjiuwen.core.foundation.llm import (
    ModelClientConfig,
    ModelRequestConfig,
)
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.runner_config import DEFAULT_RUNNER_CONFIG
from openjiuwen.core.session.checkpointer.checkpointer import (
    CheckpointerConfig,
    CheckpointerFactory,
)
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent.legacy import WorkflowAgentConfig
from openjiuwen.core.workflow import (
    End,
    FieldInfo,
    IntentDetectionCompConfig,
    IntentDetectionComponent,
    QuestionerComponent,
    QuestionerConfig,
    Start,
    Workflow,
    WorkflowCard,
)
from openjiuwen.core.workflow.base import generate_workflow_key

# Environment configuration
API_BASE = os.getenv("API_BASE", "https://api.deepseek.com")
API_KEY = os.getenv("API_KEY", "")
MODEL_NAME = os.getenv("MODEL_NAME", "deepseek-chat")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "SiliconFlow")
os.environ.setdefault("LLM_SSL_VERIFY", "false")

CONVERSATION_ID = "c123"


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def model_request_config() -> ModelRequestConfig:
    """Create model request configuration."""
    return ModelRequestConfig(
        model=MODEL_NAME,
        temperature=0.7,
        top_p=0.9,
    )


@pytest.fixture
def model_client_config() -> ModelClientConfig:
    """Create model client configuration."""
    return ModelClientConfig(
        client_provider=MODEL_PROVIDER,
        api_key=API_KEY,
        api_base=API_BASE,
        verify_ssl=False
    )


@pytest.fixture
def intent_detection_component(
        model_request_config: ModelRequestConfig,
        model_client_config: ModelClientConfig
) -> IntentDetectionComponent:
    """Create intent detection component."""
    config = IntentDetectionCompConfig(
        user_prompt="请判断用户意图",
        category_name_list=["查询某地天气"],
        model_config=model_request_config,
        model_client_config=model_client_config,
    )
    component = IntentDetectionComponent(config)
    component.add_branch("${intent.classification_id} == 0", ["end"], "默认分支")
    component.add_branch("${intent.classification_id} == 1", ["questioner"], "查询天气分支")
    return component


@pytest.fixture
def questioner_component(
        model_request_config: ModelRequestConfig,
        model_client_config: ModelClientConfig
) -> QuestionerComponent:
    """Create questioner component."""
    key_fields = [
        FieldInfo(field_name="location", description="地点", required=True),
        FieldInfo(
            field_name="date",
            description="时间",
            required=True,
            default_value="today",
        ),
    ]
    config = QuestionerConfig(
        model_config=model_request_config,
        model_client_config=model_client_config,
        question_content="",
        extract_fields_from_response=True,
        field_names=key_fields,
        with_chat_history=False,
    )
    return QuestionerComponent(config)


@pytest.fixture
def interrupt_workflow(
        intent_detection_component: IntentDetectionComponent,
        questioner_component: QuestionerComponent
) -> Workflow:
    """Build workflow with interactive components for testing interrupt recovery."""
    # Create workflow configuration
    workflow_card = WorkflowCard(
        name="interrupt_test",
        id="test_interrupt_workflow",
        version="1.0",
    )

    workflow = Workflow(card=workflow_card)

    # Create components
    start = Start()
    end = End({"responseTemplate": "{{output}}"})

    # Register components
    workflow.set_start_comp(
        "start",
        start,
        inputs_schema={"query": "${query}"},
    )
    workflow.add_workflow_comp(
        "intent",
        intent_detection_component,
        inputs_schema={"query": "${start.query}"},
    )
    workflow.add_workflow_comp(
        "questioner",
        questioner_component,
        inputs_schema={"query": "${start.query}"}
    )
    workflow.set_end_comp("end", end, inputs_schema={"output": "${questioner.location}"})

    # Connect topology
    workflow.add_connection("start", "intent")
    workflow.add_connection("questioner", "end")

    return workflow


@pytest.fixture
def workflow_agent() -> WorkflowAgent:
    """Create workflow agent for testing."""
    # Use original workflow IDs (not the modified workflow_key)
    workflow_card = WorkflowCard(
        id="test_interrupt_workflow",
        name="interrupt_test",
        description="天气查询工作流",
        version="1.0",
        input_params={"query": {"type": "string"}}
    )

    config = WorkflowAgentConfig(
        id="test_weather_agent",
        version="0.1.0",
        description="测试用天气 agent",
        workflows=[workflow_card],
    )

    return WorkflowAgent(config)


@pytest_asyncio.fixture
async def runner_with_redis(interrupt_workflow: Workflow):
    """Setup Runner with Redis checkpointer and cleanup after test."""
    # Set workflow card id to workflow_key format for resource_mgr lookup
    workflow_key = generate_workflow_key(
        interrupt_workflow.card.id,
        interrupt_workflow.card.version
    )
    interrupt_workflow.card.id = workflow_key

    # Register workflow
    Runner.resource_mgr.add_workflow(
        interrupt_workflow.card,
        lambda: interrupt_workflow
    )

    # Configure Redis checkpointer
    runner_config = copy.deepcopy(DEFAULT_RUNNER_CONFIG)
    runner_config.checkpointer_config = CheckpointerConfig(
        type="redis",
        conf={"connection": {"url": "redis://localhost:6379"}}
    )
    Runner.set_config(runner_config)

    # Start runner
    await Runner.start()

    # Provide workflow to test
    yield interrupt_workflow

    # Cleanup
    Runner.resource_mgr.remove_workflow(interrupt_workflow.card.id)
    Runner.set_config(DEFAULT_RUNNER_CONFIG)
    await CheckpointerFactory.get_checkpointer().release(CONVERSATION_ID)
    await Runner.stop()


# ============================================================================
# Tests
# ============================================================================

@pytest.mark.asyncio
async def test_workflow_agent_invoke_with_interrupt_recovery(
        runner_with_redis: Workflow,
        workflow_agent: WorkflowAgent
):
    """
    Test workflow agent invoke with interrupt and recovery.

    This test verifies:
    1. First invocation triggers an interaction request (interrupt)
    2. Second invocation resumes from checkpoint and completes successfully
    3. Redis checkpointer correctly saves and restores workflow state
    """
    # First invocation - should trigger interaction request
    result = await asyncio.wait_for(
        Runner.run_agent(
            workflow_agent,
            {"query": "查询天气", "conversation_id": CONVERSATION_ID}
        ),
        timeout=50.0
    )

    # Verify first invocation returns interaction request
    assert isinstance(result, list), "First invocation should return a list"
    assert len(result) > 0, "Result list should not be empty"
    assert isinstance(result[0], OutputSchema), "First element should be OutputSchema"
    assert result[0].type == '__interaction__', "Should return interaction type"

    # Second invocation - resume from checkpoint with user input
    result2 = await asyncio.wait_for(
        Runner.run_agent(
            workflow_agent,
            {"query": "上海", "conversation_id": CONVERSATION_ID}
        ),
        timeout=30.0
    )

    # Verify second invocation completes successfully
    assert isinstance(result2, dict), "Second invocation should return a dict"
    assert result2['result_type'] == 'answer', "Should return answer type"
    assert result2['output'].state.value == 'COMPLETED', "Workflow should be completed"
    assert result2['output'].result['response'] == '上海', "Should return '上海'"


@pytest.mark.asyncio
async def test_redis_checkpointer_initialization(runner_with_redis: Workflow):
    """Test that Redis checkpointer is properly initialized."""
    checkpointer = CheckpointerFactory.get_checkpointer()
    assert checkpointer is not None, "Checkpointer should be initialized"
    assert checkpointer.__class__.__name__ == "RedisCheckpointer", \
        "Should use RedisCheckpointer"


@pytest.mark.asyncio
async def test_workflow_registration(runner_with_redis: Workflow):
    """Test that workflow is properly registered in resource manager."""
    workflow_key = generate_workflow_key(
        "test_interrupt_workflow",
        "1.0"
    )

    # Verify workflow is registered by trying to get it
    workflow = await Runner.resource_mgr.get_workflow(workflow_key)
    assert workflow is not None, "Workflow should be registered"
    assert workflow.card.name == "interrupt_test", "Workflow name should match"
