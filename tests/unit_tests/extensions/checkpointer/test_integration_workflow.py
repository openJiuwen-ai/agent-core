# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Integration tests for Workflow with Redis checkpointer.
"""

import uuid

import pytest
import pytest_asyncio

from openjiuwen.core.session import (
    InteractionOutput,
    InteractiveInput,
)
from openjiuwen.core.session.checkpointer.checkpointer import (
    CheckpointerConfig,
    CheckpointerFactory,
    default_inmemory_checkpointer,
)
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.session.workflow import (
    create_workflow_session,
)
from openjiuwen.core.workflow import (
    Workflow,
    WorkflowCard,
    WorkflowExecutionState,
    WorkflowOutput,
)
from openjiuwen.extensions.checkpointer.redis.checkpointer import RedisCheckpointerProvider
from tests.unit_tests.core.workflow.mock_nodes import (
    InteractiveNode4Cp,
    MockEndNode,
    MockStartNode4Cp,
)


@pytest_asyncio.fixture(autouse=True)
async def setup_and_teardown():
    """Setup and teardown Redis checkpointer for each test."""
    # setup
    RedisCheckpointerProvider()
    checkpointer = await CheckpointerFactory.create(
        CheckpointerConfig(type="redis", conf={"connection": {"url": "redis://localhost:6379"}}))
    CheckpointerFactory.set_default_checkpointer(checkpointer)
    yield
    # teardown
    CheckpointerFactory.set_default_checkpointer(default_inmemory_checkpointer)
    # Close Redis connection if it was created from URL
    if hasattr(checkpointer, '_redis_store') and checkpointer._redis_store:
        await checkpointer._redis_store.redis.aclose()


pytestmark = pytest.mark.asyncio


async def test_simple_interactive_workflow():
    """
    Test simple interactive workflow with Redis checkpointer.
    
    Graph: start->a->end
    """
    start_node = MockStartNode4Cp("start")
    flow = Workflow(card=WorkflowCard(id="test_simple_interactive_workflow"))
    flow.set_start_comp("start", start_node,
                        inputs_schema={
                            "a": "${inputs.a}",
                            "b": "${inputs.b}",
                            "c": 1,
                            "d": [1, 2, 3]})
    flow.add_workflow_comp("a", InteractiveNode4Cp("a"),
                           inputs_schema={
                               "aa": "${start.a}",
                               "ac": "${start.c}"})
    flow.set_end_comp("end", MockEndNode("end"),
                      inputs_schema={
                          "result": "${a.aa}"})
    flow.add_connection("start", "a")
    flow.add_connection("a", "end")

    session_id = uuid.uuid4().hex

    res = await flow.invoke({"inputs": {"a": 1, "b": "haha"}}, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': '__interaction__', 'index': 0,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'a', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})
    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate(
            {'index': 1, 'payload': InteractionOutput.model_validate({'id': 'a', 'value': 'Please enter any key'}),
             'type': '__interaction__'})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    assert start_node.runtime == 1


async def test_simple_interactive_workflow_raw_input():
    """
    Test simple interactive workflow with raw input.
    
    Graph: start->a->end
    """
    start_node = MockStartNode4Cp("start")
    flow = Workflow(
        card=WorkflowCard(id="test_simple_interactive_workflow_raw_input"))
    flow.set_start_comp("start", start_node,
                        inputs_schema={
                            "a": "${inputs.a}",
                            "b": "${inputs.b}",
                            "c": 1,
                            "d": [1, 2, 3]})
    flow.add_workflow_comp("a", InteractiveNode4Cp("a"),
                           inputs_schema={
                               "aa": "${start.a}",
                               "ac": "${start.c}"})
    flow.set_end_comp("end", MockEndNode("end"),
                      inputs_schema={
                          "result": "${a.aa}"})
    flow.add_connection("start", "a")
    flow.add_connection("a", "end")

    session_id = uuid.uuid4().hex

    res = await flow.invoke({"inputs": {"a": 1, "b": "haha"}}, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': '__interaction__', 'index': 0,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'a', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput({"aa": "any key"})
    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate(
            {'index': 1, 'payload': InteractionOutput.model_validate({'id': 'a', 'value': 'Please enter any key'}),
             'type': '__interaction__'})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    assert start_node.runtime == 1
    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result={'result': 'any key'},
        state=WorkflowExecutionState.COMPLETED)


async def test_simple_interactive_workflow_checkpointer():
    """
    Test simple interactive workflow with checkpointer verification.
    
    Graph: start->a->end
    """
    start_node = MockStartNode4Cp("start")
    flow = Workflow(
        card=WorkflowCard(id="test_simple_interactive_workflow_checkpointer"))
    flow.set_start_comp("start", start_node,
                        inputs_schema={
                            "a": "${inputs.a}",
                            "b": "${inputs.b}",
                            "c": 1,
                            "d": [1, 2, 3]})
    flow.add_workflow_comp("a", InteractiveNode4Cp("a"),
                           inputs_schema={
                               "aa": "${start.a}",
                               "ac": "${start.c}"})
    flow.set_end_comp("end", MockEndNode("end"),
                      inputs_schema={
                          "result": "${a.aa}"})
    flow.add_connection("start", "a")
    flow.add_connection("a", "end")

    session_id = uuid.uuid4().hex

    res = await flow.invoke({"inputs": {"a": 1, "b": "haha"}}, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': '__interaction__', 'index': 0,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'a', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    config = {"configurable": {"thread_id": f"{session_id}:test_simple_interactive_workflow_checkpointer"}}
    default_checkpointer = CheckpointerFactory.get_checkpointer()
    # Parse thread_id to get session_id and workflow_id (ns)
    thread_id = config["configurable"]["thread_id"]
    session_id_from_thread, workflow_id = thread_id.split(":", 1)
    checkpoint = await default_checkpointer.graph_store().get(session_id_from_thread, workflow_id)
    assert checkpoint is not None
    # Verify workflow storage exists using checkpointer's workflow_storage
    from openjiuwen.core.session.internal.workflow import WorkflowSession
    from openjiuwen.core.session.internal.agent import AgentSession
    from openjiuwen.core.session.config.base import Config
    test_session = WorkflowSession(
        workflow_id=workflow_id,
        parent=AgentSession(session_id=session_id_from_thread, config=Config()),
        session_id=session_id_from_thread
    )
    assert await default_checkpointer._workflow_storage.exists(test_session) is True

    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})
    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate(
            {'index': 1, 'payload': InteractionOutput.model_validate({'id': 'a', 'value': 'Please enter any key'}),
             'type': '__interaction__'})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    assert start_node.runtime == 1
    checkpoint = await default_checkpointer.graph_store().get(session_id_from_thread, workflow_id)
    assert checkpoint is not None
    assert await default_checkpointer._workflow_storage.exists(test_session) is True

    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result={'result': "any key"},
        state=WorkflowExecutionState.COMPLETED)
    # checkpoint will be deleted when completed
    checkpoint = await default_checkpointer.graph_store().get(session_id_from_thread, workflow_id)
    assert checkpoint is None
    # Workflow storage should also be cleared when workflow completes
    assert await default_checkpointer._workflow_storage.exists(test_session) is False
