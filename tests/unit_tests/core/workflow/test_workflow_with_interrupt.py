import asyncio
import sys
import types
import uuid
from unittest.mock import Mock

import pytest

from openjiuwen.core.common.constants.constant import INTERACTION
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.workflow import BranchComponent, WorkflowCard
from openjiuwen.core.workflow import ArrayCondition
from openjiuwen.core.workflow.components.flow.loop.callback.intermediate_loop_var import IntermediateLoopVarCallback
from openjiuwen.core.workflow.components.flow.loop.callback.output import OutputCallback
from openjiuwen.core.workflow import LoopGroup, LoopComponent
from openjiuwen.core.workflow import LoopSetVariableComponent
from openjiuwen.core.workflow.components.flow.workflow_comp import SubWorkflowComponent
from openjiuwen.core.session import FORCE_DEL_WORKFLOW_STATE_KEY
from openjiuwen.core.session import get_default_inmemory_checkpointer
from openjiuwen.core.session import InteractionOutput
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.workflow import create_workflow_session
from openjiuwen.core.session.stream import BaseStreamMode, TraceSchema, OutputSchema
from openjiuwen.core.workflow import Workflow, WorkflowExecutionState, WorkflowOutput
from openjiuwen.core.workflow import ComponentAbility
from openjiuwen.core.workflow.components.flow.loop.loop_comp import AdvancedLoopComponent
from tests.unit_tests.core.workflow.mock_nodes import (
    InteractiveNode4StreamCp,
    MockStartNode,
    MockEndNode,
    Node4Cp,
    MockStartNode4Cp,
    InteractiveNode4Cp,
    AddTenNode4Cp,
    CommonNode,
    AddTenNode,
    MockStreamNode,
    InteractiveNode4Collect,
)

fake_base = types.ModuleType("base")
fake_base.logger = Mock()

fake_exception_module = types.ModuleType("base")
fake_exception_module.BaseError = Mock()

sys.modules["openjiuwen.core.common.logging.base"] = fake_base
sys.modules["openjiuwen.core.common.exception.base"] = fake_exception_module
pytestmark = pytest.mark.asyncio


async def test_simple_workflow():
    """
    graph : start->a->end
    """
    flow, mock_node, mock_start = create_simple_workflow()
    session_id = uuid.uuid4().hex
    with pytest.raises(BaseError) as e:
        await flow.invoke({"inputs": {"a": 1, "b": "haha"}}, create_workflow_session(session_id=session_id))
    print(str(e.value))
    assert e.value.code == StatusCode.WORKFLOW_COMPONENT_EXECUTION_ERROR.code
    assert "component 'a' execute 'invoke' error, reason='value < 20', workflow='simple_workflow'" in e.value.message
    assert mock_start.runtime == 1
    assert mock_node.runtime == 1
    flow2, mock_node2, mock_start2 = create_simple_workflow()
    with pytest.raises(BaseError) as e:
        await flow2.invoke(InteractiveInput(), create_workflow_session(session_id=session_id))
    assert e.value.code == StatusCode.WORKFLOW_COMPONENT_EXECUTION_ERROR.code
    assert "component 'a' execute 'invoke' error, reason='value < 20', workflow='simple_workflow'" in e.value.message
    assert mock_start2.runtime == 0
    assert mock_node2.runtime == 1


def create_simple_workflow():
    mock_start = MockStartNode4Cp("start")
    mock_node = Node4Cp("a")
    flow = Workflow(card=WorkflowCard(id="simple_workflow"))
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
    subflow = Workflow(card=WorkflowCard(id="test_workflow_comp"))
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
    with pytest.raises(BaseError) as e:
        await flow.invoke({"inputs": {"a": 1, "b": "haha"}}, create_workflow_session(session_id=session_id))
    assert e.value.code == StatusCode.WORKFLOW_COMPONENT_EXECUTION_ERROR.code
    assert "component 'a2' execute 'invoke' error, reason='value < 20', workflow='test_workflow_comp'" in str(e.value)
    assert mock_start.runtime == 1
    assert mock_node.runtime == 1

    await asyncio.sleep(0.1)
    with pytest.raises(BaseError) as e:
        await flow.invoke(InteractiveInput(), create_workflow_session(session_id=session_id))
    assert e.value.code == StatusCode.WORKFLOW_COMPONENT_EXECUTION_ERROR.code
    assert "component 'a2' execute 'invoke' error, reason='value < 20', workflow='test_workflow_comp'" in str(e.value)

    assert mock_start.runtime == 1
    assert mock_node.runtime == 2


async def test_workflow_with_loop():
    flow = Workflow(card=WorkflowCard(id="test_workflow_with_loop"))
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
    loop_group.add_workflow_comp("3", LoopSetVariableComponent(
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
    with pytest.raises(BaseError) as e:
        await flow.invoke({"input_array": [1, 2, 3], "input_number": 1},
                          create_workflow_session(session_id=session_id))
    assert e.value.code == StatusCode.WORKFLOW_COMPONENT_EXECUTION_ERROR.code
    assert "component '2' execute 'invoke' error, reason='inner error: 1', workflow='" in str(
        e.value)

    with pytest.raises(BaseError) as e:
        result = await flow.invoke(InteractiveInput(), create_workflow_session(session_id=session_id))

    assert e.value.code == StatusCode.WORKFLOW_COMPONENT_EXECUTION_ERROR.code
    assert "component '2' execute 'invoke' error, reason='inner error: 11', workflow='" in str(
        e.value)

    with pytest.raises(BaseError) as e:
        result = await flow.invoke(InteractiveInput(), create_workflow_session(session_id=session_id))
    assert e.value.code == StatusCode.WORKFLOW_COMPONENT_EXECUTION_ERROR.code
    assert "component '2' execute 'invoke' error, reason='inner error: 21', workflow='" in str(
        e.value)

    result = await flow.invoke(InteractiveInput(), create_workflow_session(session_id=session_id))
    assert result == WorkflowOutput(result={"array_result": [11, 12, 13], "user_var": 31},
                                    state=WorkflowExecutionState.COMPLETED)

    with pytest.raises(BaseError) as e:
        expect_e = Exception()
        result = await flow.invoke({"input_array": [4, 5], "input_number": 2},
                                   create_workflow_session(session_id=session_id))
    assert e.value.code == StatusCode.WORKFLOW_COMPONENT_EXECUTION_ERROR.code
    assert "component '2' execute 'invoke' error, reason='inner error: 2', workflow=" in str(
        e.value)

    with pytest.raises(BaseError) as e:
        result = await flow.invoke(InteractiveInput(), create_workflow_session(session_id=session_id))
    assert e.value.code == StatusCode.WORKFLOW_COMPONENT_EXECUTION_ERROR.code
    assert "component '2' execute 'invoke' error, reason='inner error: 12', workflow=" in str(
        e.value)

    result = await flow.invoke(InteractiveInput(), create_workflow_session(session_id=session_id))
    assert result == WorkflowOutput(result={"array_result": [14, 15], "user_var": 22},
                                    state=WorkflowExecutionState.COMPLETED)


async def test_workflow_with_loop_interactive():
    flow = Workflow(card=WorkflowCard(id="test_workflow_with_loop_interactive"))
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
    loop_group.add_workflow_comp("3", LoopSetVariableComponent(
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

    # 每次节点2有两个等待用户输入，索引为：0、1，循环三次，共6个输入
    res = await flow.invoke({"input_array": [1, 2, 3], "input_number": 1},
                            create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': INTERACTION, 'index': 0,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'l.2', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})

    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': INTERACTION, 'index': 1,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'l.2', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})

    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': INTERACTION, 'index': 0,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'l.2', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})

    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': INTERACTION, 'index': 1,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'l.2', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})

    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': INTERACTION, 'index': 0,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'l.2', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})

    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': INTERACTION, 'index': 1,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'l.2', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})

    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(result={"array_result": [11, 12, 13], "user_var": None},
                                 state=WorkflowExecutionState.COMPLETED)

    # 重复执行
    res = await flow.invoke({"input_array": [4, 5], "input_number": 2}, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': INTERACTION, 'index': 0,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'l.2', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})

    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': INTERACTION, 'index': 1,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'l.2', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})

    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': INTERACTION, 'index': 0,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'l.2', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})

    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': INTERACTION, 'index': 1,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'l.2', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})

    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(result={"array_result": [14, 15], "user_var": None},
                                 state=WorkflowExecutionState.COMPLETED)


async def test_workflow_with_loop_comp_interactive():
    flow = Workflow(card=WorkflowCard(id="test_workflow_with_loop_interactive"))
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
    loop_group.add_workflow_comp("3", LoopSetVariableComponent(
        {"${l.user_var}": "${2.result}"}))
    loop_group.start_comp("1")
    loop_group.end_comp("3")
    loop_group.add_connection("1", "2")
    loop_group.add_connection("2", "3")

    loop = LoopComponent(loop_group, output_schema={"results": "${1.result}", "user_var": "${l.user_var}"})

    flow.add_workflow_comp("l", loop, inputs_schema={"input_number": "${input_number}",
                                                     "loop_type": "array",
                                                     "loop_array": {
                                                         "item": "${a.array}"
                                                     },
                                                     "intermediate_var": {"user_var": "${input_number}"}})

    # s->a->(1->2->3)->b->e
    flow.add_connection("s", "a")
    flow.add_connection("a", "l")
    flow.add_connection("l", "b")
    flow.add_connection("b", "e")

    session_id = uuid.uuid4().hex

    # 每次节点2有两个等待用户输入，索引为：0、1，循环三次，共6个输入
    res = await flow.invoke({"input_array": [1, 2, 3], "input_number": 1},
                            create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': INTERACTION, 'index': 0,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'l.2', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})

    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': INTERACTION, 'index': 1,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'l.2', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})

    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': INTERACTION, 'index': 0,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'l.2', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})

    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': INTERACTION, 'index': 1,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'l.2', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})

    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': INTERACTION, 'index': 0,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'l.2', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})

    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': INTERACTION, 'index': 1,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'l.2', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})

    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(result={"array_result": [11, 12, 13], "user_var": None},
                                 state=WorkflowExecutionState.COMPLETED)

    # 重复执行
    res = await flow.invoke({"input_array": [4, 5], "input_number": 2}, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': INTERACTION, 'index': 0,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'l.2', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})

    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': INTERACTION, 'index': 1,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'l.2', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})

    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': INTERACTION, 'index': 0,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'l.2', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})

    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': INTERACTION, 'index': 1,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'l.2', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})

    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(result={"array_result": [14, 15], "user_var": None},
                                 state=WorkflowExecutionState.COMPLETED)


async def test_simple_interactive_workflow():
    """
    graph : start->a->end
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
        result=[OutputSchema.model_validate({'type': INTERACTION, 'index': 0,
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
             'type': INTERACTION})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    assert start_node.runtime == 1


async def test_simple_stream_interactive_workflow():
    """
    graph : start->a->end
    """
    start_node = MockStartNode4Cp("start")
    flow = Workflow(card=WorkflowCard(id="test_simple_stream_interactive_workflow"))
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

    async for res in flow.stream({"inputs": {"a": 1, "b": "haha"}}, create_workflow_session(session_id=session_id)):
        if res.type == INTERACTION:
            interaction_node = res.payload.id
            interaction_msg = res.payload.value
    assert interaction_node == "a"
    assert interaction_msg == "Please enter any key"
    user_input = InteractiveInput()
    user_input.update(interaction_node, {"aa": "any key"})
    result = None
    async for res in flow.stream(user_input, create_workflow_session(session_id=session_id),
                                 stream_modes=[BaseStreamMode.OUTPUT]):
        if res.type == "output":
            assert res.payload[0] == "a"
            result = res.payload[1]
    assert result == {"aa": "any key"}
    assert start_node.runtime == 1


async def test_collect_node_interactive_workflow():
    """
    graph : start->a->b->end
    """
    flow = Workflow()
    flow.set_start_comp(
        "start", MockStartNode("start"), inputs_schema={"a": "${inputs.a}", "b": "${inputs.b}", "c": 1, "d": [1, 2, 3]}
    )
    flow.add_workflow_comp(
        "a",
        MockStreamNode(),
        inputs_schema={"aa": "${start.a}", "ac": "${start.c}"},
        wait_for_all=True,
        comp_ability=[ComponentAbility.STREAM],
    )
    flow.add_workflow_comp(
        "b",
        InteractiveNode4Collect("b"),
        inputs_schema={"aa1": "${a.aa}", "ac1": "${a.ac}"},
        stream_inputs_schema={"aa": "${a.aa}", "ac": "${a.ac}"},
        wait_for_all=True,
        comp_ability=[ComponentAbility.COLLECT],
    )
    flow.set_end_comp("end", MockEndNode("end"), inputs_schema={"result": "${b.aa}"})
    flow.add_connection("start", "a")
    flow.add_stream_connection("a", "b")
    flow.add_connection("b", "end")
    session_id = uuid.uuid4().hex
    with pytest.raises(BaseError) as e:
        res = await flow.invoke({"inputs": {"a": 1, "b": "haha"}}, create_workflow_session(session_id=session_id))
    assert e.value.code == StatusCode.COMP_SESSION_INTERACT_ERROR.code


async def test_simple_concurrent_interactive_workflow():
    """
    graph : start->a->end
                 ->b->end
    """
    start_node = MockStartNode4Cp("start")
    flow = Workflow(card=WorkflowCard(id="test_simple_concurrent_interactive_workflow"))
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

    res = await flow.invoke({"inputs": {"a": 1, "b": "haha"}}, create_workflow_session(session_id=session_id))
    assert sorted(res.result, key=lambda x: x.payload.id) == sorted([
        OutputSchema.model_validate({'type': INTERACTION, 'index': 0, 'payload': InteractionOutput.model_validate(
            {'id': 'a', 'value': 'Please enter any key'})}),
        OutputSchema.model_validate({'type': INTERACTION, 'index': 0, 'payload': InteractionOutput.model_validate(
            {'id': 'b', 'value': 'Please enter any key'})})
    ], key=lambda x: x.payload.id)
    user_input = InteractiveInput()
    user_input.update("a", {"aa": "any key a"})
    user_input.update("b", {"aa": "any key b"})
    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert sorted(res.result, key=lambda x: x.payload.id) == sorted([
        OutputSchema.model_validate(
            {'index': 1, 'payload': InteractionOutput.model_validate({'id': 'a', 'value': 'Please enter any key'}),
             'type': INTERACTION}),
        OutputSchema.model_validate(
            {'index': 1, 'payload': InteractionOutput.model_validate({'id': 'b', 'value': 'Please enter any key'}),
             'type': INTERACTION})
    ], key=lambda x: x.payload.id)
    assert start_node.runtime == 1
    user_input = InteractiveInput()
    user_input.update("a", {"aa": "any key a"})
    user_input.update("b", {"aa": "any key b"})
    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(result={"result": ["any key a", "any key b"]}, state=WorkflowExecutionState.COMPLETED)


async def test_workflow_with_branch():
    flow = Workflow(card=WorkflowCard(id="test_workflow_with_branch"))
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

    async for chuck in flow.stream({"a": 2}, create_workflow_session()):
        if isinstance(chuck, TraceSchema):
            print(chuck.model_dump_json(indent=4))
        elif isinstance(chuck, OutputSchema):
            assert chuck.payload.get("b") == 12

    async for chuck in flow.stream({"a": 15}, create_workflow_session()):
        if isinstance(chuck, TraceSchema):
            print(chuck.model_dump_json(indent=4))
        elif isinstance(chuck, OutputSchema):
            assert chuck.payload.get("a") == 15


async def test_simple_interactive_workflow_raw_input():
    """
    graph : start->a->end
    """
    start_node = MockStartNode4Cp("start")
    flow = Workflow(card=WorkflowCard(id="test_simple_interactive_workflow_raw_input"))
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
        result=[OutputSchema.model_validate({'type': INTERACTION, 'index': 0,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'a', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    user_input = InteractiveInput({"aa": "any key"})
    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate(
            {'index': 1, 'payload': InteractionOutput.model_validate({'id': 'a', 'value': 'Please enter any key'}),
             'type': INTERACTION})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    assert start_node.runtime == 1
    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result={'result': 'any key'},
        state=WorkflowExecutionState.COMPLETED)


async def test_simple_interactive_workflow_both_raw_input_update():
    """ graph : start->a->end """
    start_node = MockStartNode4Cp("start")
    flow = Workflow(card=WorkflowCard(id="test_simple_interactive_workflow_both_raw_input_update"))
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
        result=[OutputSchema.model_validate({'type': INTERACTION, 'index': 0,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'a', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)

    user_input = InteractiveInput({"aa": "any key"})
    with pytest.raises(BaseError) as exc_info:
        user_input.update("a", {"aa": "abc"})
    assert exc_info.value.code == StatusCode.INTERACTION_INPUT_INVALID.code

    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate(
            {'index': 1, 'payload': InteractionOutput.model_validate({'id': 'a', 'value': 'Please enter any key'}),
             'type': INTERACTION})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    assert start_node.runtime == 1
    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result={'result': 'any key'},
        state=WorkflowExecutionState.COMPLETED)


async def test_simple_interactive_workflow_raw_inputs_empty_str_list():
    """
    graph : start->a->end
    """
    start_node = MockStartNode4Cp("start")
    flow = Workflow(card=WorkflowCard(id="test_simple_interactive_workflow_raw_inputs_empty_str_list"))
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
                          "result": "${a}"})
    flow.add_connection("start", "a")
    flow.add_connection("a", "end")

    for raw_inputs in [[], ""]:
        session_id = uuid.uuid4().hex
        start_node.runtime = 0

        res = await flow.invoke({"inputs": {"a": 1, "b": "haha"}}, create_workflow_session(session_id=session_id))
        assert res == WorkflowOutput(
            result=[OutputSchema.model_validate({'type': INTERACTION, 'index': 0,
                                                 'payload': InteractionOutput.model_validate(
                                                     {'id': 'a', 'value': 'Please enter any key'})})],
            state=WorkflowExecutionState.INPUT_REQUIRED)

        user_input = InteractiveInput(raw_inputs)

        res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
        assert res == WorkflowOutput(
            result=[OutputSchema.model_validate(
                {'index': 1, 'payload': InteractionOutput.model_validate({'id': 'a', 'value': 'Please enter any key'}),
                 'type': INTERACTION})],
            state=WorkflowExecutionState.INPUT_REQUIRED)
        assert start_node.runtime == 1
        res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
        assert res == WorkflowOutput(
            result={'result': raw_inputs},
            state=WorkflowExecutionState.COMPLETED)


async def test_simple_interactive_workflow_update_empty_str_list():
    """
    graph : start->a->end
    """
    start_node = MockStartNode4Cp("start")
    flow = Workflow(card=WorkflowCard(id="test_simple_interactive_workflow_update_empty_str_list"))
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
                          "result": "${a}"})
    flow.add_connection("start", "a")
    flow.add_connection("a", "end")

    for raw_inputs in [[], ""]:
        session_id = uuid.uuid4().hex
        start_node.runtime = 0

        res = await flow.invoke({"inputs": {"a": 1, "b": "haha"}}, create_workflow_session(session_id=session_id))
        assert res == WorkflowOutput(
            result=[OutputSchema.model_validate({'type': INTERACTION, 'index': 0,
                                                 'payload': InteractionOutput.model_validate(
                                                     {'id': 'a', 'value': 'Please enter any key'})})],
            state=WorkflowExecutionState.INPUT_REQUIRED)

        user_input = InteractiveInput()
        user_input.update("a", raw_inputs)

        res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
        assert res == WorkflowOutput(
            result=[OutputSchema.model_validate(
                {'index': 1, 'payload': InteractionOutput.model_validate({'id': 'a', 'value': 'Please enter any key'}),
                 'type': INTERACTION})],
            state=WorkflowExecutionState.INPUT_REQUIRED)
        assert start_node.runtime == 1
        res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
        assert res == WorkflowOutput(
            result={'result': raw_inputs},
            state=WorkflowExecutionState.COMPLETED)


async def test_simple_interactive_workflow_none():
    """
    graph : start->a->end
    """
    start_node = MockStartNode4Cp("start")
    flow = Workflow(card=WorkflowCard(id="test_simple_interactive_workflow_none"))
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
                          "result": "${a}"})
    flow.add_connection("start", "a")
    flow.add_connection("a", "end")

    session_id = uuid.uuid4().hex

    res = await flow.invoke({"inputs": {"a": 1, "b": "haha"}}, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate({'type': INTERACTION, 'index': 0,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'a', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)

    user_input = InteractiveInput()

    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate(
            {'index': 0, 'payload': InteractionOutput.model_validate({'id': 'a', 'value': 'Please enter any key'}),
             'type': INTERACTION})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    assert start_node.runtime == 1
    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate(
            {'index': 0, 'payload': InteractionOutput.model_validate({'id': 'a', 'value': 'Please enter any key'}),
             'type': INTERACTION})],
        state=WorkflowExecutionState.INPUT_REQUIRED)


async def test_simple_interactive_workflow_checkpointer():
    """
    graph : start->a->end
    """
    workflow_id = "test_simple_interactive_workflow_checkpointer"
    start_node = MockStartNode4Cp("start")
    flow = Workflow(card=WorkflowCard(id=workflow_id))
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
        result=[OutputSchema.model_validate({'type': INTERACTION, 'index': 0,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'a', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    state = await get_default_inmemory_checkpointer().graph_store().get(session_id, workflow_id)
    assert state is not None
    stores = getattr(get_default_inmemory_checkpointer(), "_workflow_stores", None)
    first_time_workflow_store = stores.get(session_id)
    assert first_time_workflow_store is not None

    user_input = InteractiveInput()
    interaction_id = res.result[0].payload.id
    user_input.update(interaction_id, {"aa": "any key"})
    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate(
            {'index': 1, 'payload': InteractionOutput.model_validate({'id': 'a', 'value': 'Please enter any key'}),
             'type': INTERACTION})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    assert start_node.runtime == 1
    state = await get_default_inmemory_checkpointer().graph_store().get(session_id, workflow_id)
    assert state is not None

    stores = getattr(get_default_inmemory_checkpointer(), "_workflow_stores", None)
    workflow_store = stores.get(session_id)

    assert workflow_store is not None
    assert workflow_store is first_time_workflow_store

    res = await flow.invoke(user_input, create_workflow_session(session_id=session_id))
    assert res == WorkflowOutput(
        result={'result': "any key"},
        state=WorkflowExecutionState.COMPLETED)
    # checkpoint will be deleted when completed
    state = await get_default_inmemory_checkpointer().graph_store().get(session_id, workflow_id)
    assert state is None
    stores = getattr(get_default_inmemory_checkpointer(), "_workflow_stores", None)
    workflow_store = stores.get(session_id)
    assert workflow_store is None


async def test_simple_interactive_workflow_checkpointer_manual_release():
    """
    graph : start->a->end
    """
    workflow_id = "test_simple_interactive_workflow_checkpointer"
    start_node = MockStartNode4Cp("start")
    flow = Workflow(card=WorkflowCard(id=workflow_id))
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
        result=[OutputSchema.model_validate({'type': INTERACTION, 'index': 0,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'a', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    state = await get_default_inmemory_checkpointer().graph_store().get(session_id, workflow_id)
    assert state is not None
    first_time_workflow_store = getattr(get_default_inmemory_checkpointer(), "_workflow_stores").get(session_id)
    assert first_time_workflow_store is not None

    # manually clear the checkpointer
    await get_default_inmemory_checkpointer().release(session_id)
    state = await get_default_inmemory_checkpointer().graph_store().get(session_id, workflow_id)
    assert state is None
    first_time_workflow_store = getattr(get_default_inmemory_checkpointer(), "_workflow_stores").get(session_id)
    assert first_time_workflow_store is None


async def test_simple_interactive_workflow_clear_checkpointer():
    """
    graph : start->a->end
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
        result=[OutputSchema.model_validate({'type': INTERACTION, 'index': 0,
                                             'payload': InteractionOutput.model_validate(
                                                 {'id': 'a', 'value': 'Please enter any key'})})],
        state=WorkflowExecutionState.INPUT_REQUIRED)

    session = create_workflow_session(session_id=session_id, envs={FORCE_DEL_WORKFLOW_STATE_KEY: True})
    # will clean checkpointer of the session when input is not interactive input, workflow reinvoke from start node
    res = await flow.invoke({"inputs": {"a": 1, "b": "haha"}}, session)
    assert res == WorkflowOutput(
        result=[OutputSchema.model_validate(
            {'index': 0, 'payload': InteractionOutput.model_validate({'id': 'a', 'value': 'Please enter any key'}),
             'type': INTERACTION})],
        state=WorkflowExecutionState.INPUT_REQUIRED)
    assert start_node.runtime == 2
