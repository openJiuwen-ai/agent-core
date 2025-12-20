#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import sys
import types
import uuid
from unittest.mock import Mock

import pytest

from jiuwen.core.common.constants.constant import INTERACTION
from jiuwen.core.common.exception.exception import JiuWenBaseException
from jiuwen.core.common.exception.status_code import StatusCode
from jiuwen.core.component.branch_comp import BranchComponent
from jiuwen.core.component.condition.array import ArrayCondition
from jiuwen.core.component.loop_callback.intermediate_loop_var import IntermediateLoopVarCallback
from jiuwen.core.component.loop_callback.output import OutputCallback
from jiuwen.core.component.loop_comp import LoopGroup, AdvancedLoopComponent
from jiuwen.core.component.set_variable_comp import SetVariableComponent
from jiuwen.core.component.workflow_comp import SubWorkflowComponent
from jiuwen.core.runtime.interaction.interaction import InteractionOutput
from jiuwen.core.runtime.interaction.interactive_input import InteractiveInput
from jiuwen.core.runtime.workflow import WorkflowRuntime
from jiuwen.core.stream.base import BaseStreamMode, OutputSchema
from jiuwen.core.workflow.base import Workflow, WorkflowExecutionState, WorkflowOutput
from test_node import AddTenNode, CommonNode
from tests.unit_tests.workflow.test_mock_node import InteractiveNode4StreamCp, MockStartNode, MockEndNode, Node4Cp, \
    MockStartNode4Cp, InteractiveNode4Cp, AddTenNode4Cp

fake_base = types.ModuleType("base")
fake_base.logger = Mock()

fake_exception_module = types.ModuleType("base")
fake_exception_module.JiuWenBaseException = Mock()

sys.modules["jiuwen.core.common.logging.base"] = fake_base
sys.modules["jiuwen.core.common.exception.base"] = fake_exception_module
pytestmark = pytest.mark.asyncio


async def test_simple_workflow():
    """
    graph : start->a->end
    """
    flow, mock_node, mock_start = create_simple_workflow()
    session_id = uuid.uuid4().hex
    try:
        await flow.invoke({"inputs": {"a": 1, "b": "haha"}}, WorkflowRuntime(session_id=session_id))
    except Exception as e:
        assert str(e) == 'value < 20'
    assert mock_start.runtime == 1
    assert mock_node.runtime == 1
    flow2, mock_node2, mock_start2 = create_simple_workflow()
    try:
        await flow2.invoke(InteractiveInput(), WorkflowRuntime(session_id=session_id))
    except Exception as e:
        assert str(e) == 'value < 20'
    assert mock_start2.runtime == 0
    assert mock_node2.runtime == 1


def create_simple_workflow():
    mock_start = MockStartNode4Cp("start")
    mock_node = Node4Cp("a")
    flow = Workflow()
    flow.set_start_comp("start", mock_start,
                        inputs_schema={
                            "a": "${inputs.a}",
                            "b": "${inputs.b}",
                            "c": 1,
                            "d": [1, 2, 3]})
    flow.add_workflow_comp("a", mock_node,
                           inputs_schema={
                               "aa": "${start.a}",
                               "ac": "${start.c}"})
    flow.set_end_comp("end", MockEndNode("end"),
                      inputs_schema={
                          "result": "${a.aa}"})
    flow.add_connection("start", "a")
    flow.add_connection("a", "end")
    return flow, mock_node, mock_start


async def test_workflow_comp():
    """
    graph : start->a(start->a1->a2->a3->end)->end
    """
    mock_start = MockStartNode4Cp("a1")
    mock_node = Node4Cp("a2")
    subflow = Workflow()
    subflow.set_start_comp("a1", mock_start,
                           inputs_schema={
                               "a": "${a}",
                               "b": "${b}",
                               "c": 1,
                               "d": [1, 2, 3]})
    subflow.add_workflow_comp("a2", mock_node,
                              inputs_schema={
                                  "aa": "${start.a}",
                                  "ac": "${start.c}"})
    subflow.set_end_comp("a3", MockEndNode("a3"),
                         inputs_schema={
                             "result": "${a.aa}"})
    subflow.add_connection("a1", "a2")
    subflow.add_connection("a2", "a3")

    flow = Workflow()
    flow.set_start_comp("start", MockStartNode("start"),
                        inputs_schema={
                            "a": "${inputs.a}",
                            "b": "${inputs.b}",
                            "c": 1,
                            "d": [1, 2, 3]})
    flow.add_workflow_comp("a", SubWorkflowComponent(subflow),
                           inputs_schema={
                               "aa": "${start.a}",
                               "ac": "${start.c}"})
    flow.set_end_comp("end", MockEndNode("end"),
                      inputs_schema={
                          "result": "${a.aa}"})
    flow.add_connection("start", "a")
    flow.add_connection("a", "end")
    session_id = uuid.uuid4().hex
    try:
        await flow.invoke({"inputs": {"a": 1, "b": "haha"}}, WorkflowRuntime(session_id=session_id))
    except Exception as e:
        assert str(e) == 'value < 20'
    assert mock_start.runtime == 1
    assert mock_node.runtime == 1

    await asyncio.sleep(0.1)
    try:
        await flow.invoke(InteractiveInput(), WorkflowRuntime(session_id=session_id))
    except Exception as e:
        assert str(e) == 'value < 20'
    assert mock_start.runtime == 1
    assert mock_node.runtime == 2


async def test_workflow_with_loop():
    flow = Workflow()
    flow.set_start_comp("s", MockStartNode("s"))
    flow.set_end_comp("e", MockEndNode("e"),
                      inputs_schema={"array_result": "${b.array_result}", "user_var": "${b.user_var}"})
    flow.add_workflow_comp("a", CommonNode("a"),
                           inputs_schema={"array": "${input_array}"})
    flow.add_workflow_comp("b", CommonNode("b"),
                           inputs_schema={"array_result": "${l.results}", "user_var": "${l.user_var}"})

    # create  loop: (1->2->3)
    loop_group = LoopGroup()
    loop_group.add_workflow_comp("1", AddTenNode("1"), inputs_schema={"source": "${l.item}"})
    loop_group.add_workflow_comp("2", AddTenNode4Cp("2"),
                                 inputs_schema={"source": "${l.user_var}"})
    loop_group.add_workflow_comp("3", SetVariableComponent(
        {"${l.user_var}": "${2.result}"}))
    loop_group.start_comp("1")
    loop_group.end_comp("3")
    loop_group.add_connection("1", "2")
    loop_group.add_connection("2", "3")
    output_callback = OutputCallback(
        {"results": "${1.result}", "user_var": "${l.user_var}"})
    intermediate_callback = IntermediateLoopVarCallback({"user_var": "${input_number}"})

    loop = AdvancedLoopComponent(loop_group, ArrayCondition({"item": "${a.array}"}),
                                 callbacks=[output_callback, intermediate_callback])

    flow.add_workflow_comp("l", loop, inputs_schema={"input_number": "${input_number}"})

    # s->a->(1->2->3)->b->e
    flow.add_connection("s", "a")
    flow.add_connection("a", "l")
    flow.add_connection("l", "b")
    flow.add_connection("b", "e")

    session_id = uuid.uuid4().hex
    try:
        expect_e = Exception()
        result = await flow.invoke({"input_array": [1, 2, 3], "input_number": 1},
                                   WorkflowRuntime(session_id=session_id))
    except Exception as e:
        expect_e = e
    assert str(expect_e) == "inner error"
    try:
        expect_e = Exception()
        result = await flow.invoke(InteractiveInput(), WorkflowRuntime(session_id=session_id))
    except Exception as e:
        expect_e = e
    assert str(expect_e) == "inner error"
    try:
        expect_e = Exception()
        result = await flow.invoke(InteractiveInput(), WorkflowRuntime(session_id=session_id))
    except Exception as e:
        expect_e = e
    assert str(expect_e) == "inner error"
    try:
        expect_e = Exception()
        result = await flow.invoke(InteractiveInput(), WorkflowRuntime(session_id=session_id))
        assert result == WorkflowOutput(result={"array_result": [11, 12, 13], "user_var": 31},
                                        state=WorkflowExecutionState.COMPLETED)
    except Exception as e:
        assert True
        expect_e = e
    assert str(expect_e) == ""

    try:
        expect_e = Exception()
        result = await flow.invoke({"input_array": [4, 5], "input_number": 2}, WorkflowRuntime(session_id=session_id))
    except Exception as e:
        expect_e = e
    assert str(expect_e) == "inner error"
    try:
        expect_e = Exception()
        result = await flow.invoke(InteractiveInput(), WorkflowRuntime(session_id=session_id))
    except Exception as e:
        expect_e = e
    assert str(expect_e) == "inner error"
    try:
        expect_e = Exception()
        result = await flow.invoke(InteractiveInput(), WorkflowRuntime(session_id=session_id))
        assert result == WorkflowOutput(result={"array_result": [14, 15], "user_var": 22},
                                        state=WorkflowExecutionState.COMPLETED)
    except Exception as e:
        assert True
        expect_e = e
    assert str(expect_e) == ""


async def test_workflow_with_loop_interactive():
    flow = Workflow()
    flow.set_start_comp("s", MockStartNode("s"))
    flow.set_end_comp("e", MockEndNode("e"),
                      inputs_schema={"array_result": "${b.array_result}", "user_var": "${b.user_var}"})
    flow.add_workflow_comp("a", CommonNode("a"),
                           inputs_schema={"array": "${input_array}"})
    flow.add_workflow_comp("b", CommonNode("b"),
                           inputs_schema={"array_result": "${l.results}", "user_var": "${l.user_var}"})

    # create  loop: (1->2->3)
    loop_group = LoopGroup()
    loop_group.add_workflow_comp("1", AddTenNode("1"), inputs_schema={"source": "${l.item}"})
    loop_group.add_workflow_comp("2", InteractiveNode4Cp("2"),
                                 inputs_schema={"source": "${l.user_var}"})
    loop_group.add_workflow_comp("3", SetVariableComponent(
        {"${l.user_var}": "${2.result}"}))
    loop_group.start_comp("1")
    loop_group.end_comp("3")
    loop_group.add_connection("1", "2")
    loop_group.add_connection("2", "3")
    output_callback = OutputCallback({"results": "${1.result}", "user_var": "${l.user_var}"})
    intermediate_callback = IntermediateLoopVarCallback({"user_var": "${input_number}"})

    loop = AdvancedLoopComponent(loop_group, ArrayCondition({"item": "${a.array}"}),
                                 callbacks=[output_callback, intermediate_callback])

    flow.add_workflow_comp("l", loop, inputs_schema={"input_number": "${input_number}"})

    # s->a->(1->2->3)->b->e
    flow.add_connection("s", "a")
    flow.add_connection("a", "l")
    flow.add_connection("l", "b")
    flow.add_connection("b", "e")

    session_id = uuid.uuid4().hex

    res = await flow.invoke({"input_array": [1, 2, 3], "input_number": 1}, WorkflowRuntime(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': '__interaction__', 'index': 0,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'l.2', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})

    res = await flow.invoke(user_input, WorkflowRuntime(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': '__interaction__', 'index': 1,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'l.2', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})

    res = await flow.invoke(user_input, WorkflowRuntime(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': '__interaction__', 'index': 0,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'l.2', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})

    res = await flow.invoke(user_input, WorkflowRuntime(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': '__interaction__', 'index': 1,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'l.2', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})

    res = await flow.invoke(user_input, WorkflowRuntime(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': '__interaction__', 'index': 0,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'l.2', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})

    res = await flow.invoke(user_input, WorkflowRuntime(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': '__interaction__', 'index': 1,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'l.2', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})

    res = await flow.invoke(user_input, WorkflowRuntime(session_id=session_id))
    assert res == WorkflowOutput(result={"array_result": [11, 12, 13], "user_var": None},
                                 state=WorkflowExecutionState.COMPLETED)

    res = await flow.invoke({"input_array": [4, 5], "input_number": 2}, WorkflowRuntime(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': '__interaction__', 'index': 0,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'l.2', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})

    res = await flow.invoke(user_input, WorkflowRuntime(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': '__interaction__', 'index': 1,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'l.2', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})

    res = await flow.invoke(user_input, WorkflowRuntime(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': '__interaction__', 'index': 0,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'l.2', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})

    res = await flow.invoke(user_input, WorkflowRuntime(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': '__interaction__', 'index': 1,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'l.2', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})

    res = await flow.invoke(user_input, WorkflowRuntime(session_id=session_id))
    assert res == WorkflowOutput(result={"array_result": [14, 15], "user_var": None},
                                 state=WorkflowExecutionState.COMPLETED)


async def test_simple_interactive_workflow():
    """
    graph : start->a->end
    """
    start_node = MockStartNode4Cp("start")
    flow = Workflow()
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

    res = await flow.invoke({"inputs": {"a": 1, "b": "haha"}}, WorkflowRuntime(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': '__interaction__', 'index': 0,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'a', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})
    res = await flow.invoke(user_input, WorkflowRuntime(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate(
            {'index': 1, 'payload': InteractionOutput.model_validate({'id': 'a', 'value': 'Please enter any key'}),
             'type': '__interaction__'})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    assert start_node.runtime == 1


async def test_simple_stream_interactive_workflow():
    """
    graph : start->a->end
    """
    start_node = MockStartNode4Cp("start")
    flow = Workflow()
    flow.set_start_comp("start", start_node,
                        inputs_schema={
                            "a": "${inputs.a}",
                            "b": "${inputs.b}",
                            "c": 1,
                            "d": [1, 2, 3]})
    flow.add_workflow_comp("a", InteractiveNode4StreamCp("a"),
                           inputs_schema={
                               "aa": "${start.a}",
                               "ac": "${start.c}"})
    flow.set_end_comp("end", MockEndNode("end"),
                      inputs_schema={
                          "result": "${a.aa}"})
    flow.add_connection("start", "a")
    flow.add_connection("a", "end")
    interaction_node = None
    interaction_msg = None

    session_id = uuid.uuid4().hex

    async for res in flow.stream({"inputs": {"a": 1, "b": "haha"}}, WorkflowRuntime(session_id=session_id)):
        if res.type == INTERACTION:
            interaction_node = res.payload.id
            interaction_msg = res.payload.value
    assert interaction_node == "a"
    assert interaction_msg == "Please enter any key"
    user_input = InteractiveInput()
    user_input.update(interaction_node, {"aa": "any key"})
    result = None
    async for res in flow.stream(user_input, WorkflowRuntime(session_id=session_id),
                                 stream_modes=[BaseStreamMode.OUTPUT]):
        if res.type == "output":
            assert res.payload[0] == "a"
            result = res.payload[1]
    assert result == {"aa": "any key"}
    assert start_node.runtime == 1


async def test_simple_concurrent_interactive_workflow():
    """
    graph : start->a->end
                 ->b->end
    """
    start_node = MockStartNode4Cp("start")
    flow = Workflow()
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
    flow.add_workflow_comp("b", InteractiveNode4Cp("b"),
                           inputs_schema={
                               "aa": "${start.a}",
                               "ac": "${start.c}"})
    flow.set_end_comp("end", MockEndNode("end"),
                      inputs_schema={
                          "result": ["${a.aa}", "${b.aa}"]})
    flow.add_connection("start", "a")
    flow.add_connection("start", "b")
    flow.add_connection("a", "end")
    flow.add_connection("b", "end")

    session_id = uuid.uuid4().hex

    res = await flow.invoke({"inputs": {"a": 1, "b": "haha"}}, WorkflowRuntime(session_id=session_id))
    assert sorted(res.result, key=lambda x: x.payload.id) == sorted([
        OutputSchema.model_validate({'type': '__interaction__', 'index': 0, 'payload': InteractionOutput.model_validate(
            {'id': 'a', 'value': 'Please enter any key'})}),
        OutputSchema.model_validate({'type': '__interaction__', 'index': 0, 'payload': InteractionOutput.model_validate(
            {'id': 'b', 'value': 'Please enter any key'})})
    ], key=lambda x: x.payload.id)
    user_input = InteractiveInput()
    user_input.update("a", {"aa": "any key a"})
    user_input.update("b", {"aa": "any key b"})
    res = await flow.invoke(user_input, WorkflowRuntime(session_id=session_id))
    assert sorted(res.result, key=lambda x: x.payload.id) == sorted([
        OutputSchema.model_validate(
            {'index': 1, 'payload': InteractionOutput.model_validate({'id': 'a', 'value': 'Please enter any key'}),
             'type': '__interaction__'}),
        OutputSchema.model_validate(
            {'index': 1, 'payload': InteractionOutput.model_validate({'id': 'b', 'value': 'Please enter any key'}),
             'type': '__interaction__'})
    ], key=lambda x: x.payload.id)
    assert start_node.runtime == 1
    user_input = InteractiveInput()
    user_input.update("a", {"aa": "any key a"})
    user_input.update("b", {"aa": "any key b"})
    res = await flow.invoke(user_input, WorkflowRuntime(session_id=session_id))
    assert res == WorkflowOutput(result={"result": ["any key a", "any key b"]}, state=WorkflowExecutionState.COMPLETED)


async def test_workflow_with_branch():
    flow = Workflow()
    flow.set_start_comp("start", MockStartNode("start"))
    flow.set_end_comp("end", MockEndNode("end"),
                      inputs_schema={"a": "${a.result}", "b": "${b.result}"})

    sw = BranchComponent()
    sw.add_branch("${a} <= 10", ["b"], "1")
    sw.add_branch("${a} > 10", ["a"], "2")

    flow.add_workflow_comp("sw", sw)

    flow.add_workflow_comp("a", CommonNode("a"),
                           inputs_schema={"result": "${a}"})

    flow.add_workflow_comp("b", AddTenNode("b"),
                           inputs_schema={"source": "${a}"})

    flow.add_connection("start", "sw")
    flow.add_connection("a", "end")
    flow.add_connection("b", "end")

    async for chuck in flow.stream({"a": 2}, WorkflowRuntime()):
        if isinstance(chuck, OutputSchema):
            assert chuck.payload.get("b") == 12

    async for chuck in flow.stream({"a": 15}, WorkflowRuntime()):
        if isinstance(chuck, OutputSchema):
            assert chuck.payload.get("a") == 15


async def test_simple_interactive_workflow_raw_input():
    """
    graph : start->a->end
    """
    start_node = MockStartNode4Cp("start")
    flow = Workflow()
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

    res = await flow.invoke({"inputs": {"a": 1, "b": "haha"}}, WorkflowRuntime(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': '__interaction__', 'index': 0,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'a', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput({"aa": "any key"})
    res = await flow.invoke(user_input, WorkflowRuntime(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate(
            {'index': 1, 'payload': InteractionOutput.model_validate({'id': 'a', 'value': 'Please enter any key'}),
             'type': '__interaction__'})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    assert start_node.runtime == 1
    res = await flow.invoke(user_input, WorkflowRuntime(session_id=session_id))
    assert res == WorkflowOutput(
        result={'result': 'any key'},
        state=WorkflowExecutionState.COMPLETED)
