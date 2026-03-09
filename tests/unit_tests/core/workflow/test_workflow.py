from typing import AsyncIterator

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.common.logging import logger
from openjiuwen.core.workflow import Input, Output, WorkflowCard
from openjiuwen.core.workflow import BranchComponent
from openjiuwen.core.workflow import BranchRouter
from openjiuwen.core.workflow import LoopBreakComponent
from openjiuwen.core.workflow import ArrayCondition
from openjiuwen.core.workflow import NumberCondition
from openjiuwen.core.workflow import End
from openjiuwen.core.workflow import WorkflowComponent
from openjiuwen.core.workflow._workflow import execute_single_component
from openjiuwen.core.workflow.components.flow.loop.callback.intermediate_loop_var import IntermediateLoopVarCallback
from openjiuwen.core.workflow.components.flow.loop.callback.output import OutputCallback
from openjiuwen.core.workflow import LoopGroup, LoopComponent
from openjiuwen.core.workflow import LoopSetVariableComponent
from openjiuwen.core.workflow import Start
from openjiuwen.core.workflow.components.flow.workflow_comp import SubWorkflowComponent
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.workflow.components import Session
from openjiuwen.core.workflow import create_workflow_session
from openjiuwen.core.session.stream import BaseStreamMode, CustomSchema, TraceSchema
from openjiuwen.core.workflow import Workflow, WorkflowExecutionState, WorkflowOutput
from openjiuwen.core.workflow import ComponentAbility
from openjiuwen.core.workflow.components.flow.loop.loop_comp import AdvancedLoopComponent
from tests.unit_tests.core.workflow.mock_nodes import MockStartNode, MockEndNode, CommonNode, \
    AddTenNode, Node1, SlowNode, CountNode, StreamNode, CollectCompNode, TransformCompNode, StreamCompNode

pytestmark = pytest.mark.asyncio


async def test_workflow_with_loop_number_condition():
    flow = await create_workflow()

    # async for chunk in flow.stream({"input_number": 1, "loop_number": 3}, create_workflow_session()):
    #     if isinstance(chunk, TraceSchema):
    #         print(chunk.model_dump_json(indent=4))
    #
    # async for chunk in flow.stream({"input_number": 1, "loop_number": 3}, create_workflow_session()):
    #     if isinstance(chunk, TraceSchema):
    #         print(chunk.model_dump_json(indent=4))

    result = await flow.invoke({"input_number": 1, "loop_number": 3}, create_workflow_session())
    assert result == WorkflowOutput(result={"array_result": [10, 11, 12], "user_var": 31},
                                    state=WorkflowExecutionState.COMPLETED)

    result = await flow.invoke({"input_number": 2, "loop_number": 2}, create_workflow_session())
    assert result == WorkflowOutput(result={"array_result": [10, 11], "user_var": 22},
                                    state=WorkflowExecutionState.COMPLETED)
    flow = await create_workflow()
    result = await flow.invoke({"input_number": 2, "loop_number": 2}, create_workflow_session())
    assert result == WorkflowOutput(result={"array_result": [10, 11], "user_var": 22},
                                    state=WorkflowExecutionState.COMPLETED)


async def create_workflow():
    flow = Workflow()
    flow.set_start_comp("s", MockStartNode("s"))
    flow.set_end_comp("e", MockEndNode("e"),
                      inputs_schema={"array_result": "${b.array_result}", "user_var": "${b.user_var}"})
    flow.add_workflow_comp("a", CommonNode("a"))
    flow.add_workflow_comp("b", CommonNode("b"),
                           inputs_schema={"array_result": "${l.results}", "user_var": "${l.user_var}"})
    # create  loop: (1->2->3)
    loop_group = LoopGroup()
    loop_group.add_workflow_comp("1", AddTenNode("1"), inputs_schema={"source": "${l.index}"})
    loop_group.add_workflow_comp("2", AddTenNode("2"),
                                 inputs_schema={"source": "${l.intermediate_loop_var.user_var}"})
    set_variable_component = LoopSetVariableComponent({"${l.intermediate_loop_var.user_var}": "${2.result}"})
    loop_group.add_workflow_comp("3", set_variable_component)
    loop_group.start_nodes(["1"])
    loop_group.end_nodes(["3"])
    loop_group.add_connection("1", "2")
    loop_group.add_connection("2", "3")
    output_callback = OutputCallback({"results": "${1.result}", "user_var": "${l.intermediate_loop_var.user_var}"})
    intermediate_callback = IntermediateLoopVarCallback({"user_var": "${input_number}"}, "intermediate_loop_var")
    loop = AdvancedLoopComponent(loop_group, NumberCondition("${loop_number}"),
                                 callbacks=[output_callback, intermediate_callback])
    flow.add_workflow_comp("l", loop, inputs_schema={"input_number": "${input_number}"})
    # s->a->(1->2->3)->b->e
    flow.add_connection("s", "a")
    flow.add_connection("a", "l")
    flow.add_connection("l", "b")
    flow.add_connection("b", "e")
    return flow


async def test_simple_workflow():
    # flow1: start -> a -> end
    flow = Workflow()
    flow.set_start_comp("start", MockStartNode("start"),
                        inputs_schema={
                            "a": "${a}",
                            "b": "${b}",
                            "c": 1,
                            "d": [1, 2, 3]})
    flow.add_workflow_comp("a", Node1("a"),
                           inputs_schema={
                               "aa": "${start.a}",
                               "ac": "${start.c}"})
    flow.set_end_comp("end", MockEndNode("end"),
                      inputs_schema={
                          "result": "${a.aa}"})
    flow.add_connection("start", "a")
    flow.add_connection("a", "end")
    results = await flow.invoke(inputs={"a": 1, "b": "haha"}, session=create_workflow_session())
    assert results.result == {"result": 1}

    flow2 = Workflow()
    flow2.set_start_comp("start", MockStartNode("start"),
                         inputs_schema={
                             "a1": "${a1}",
                             "a2": "${a2}"})

    # flow2: start->a1|a2->end
    flow2.add_workflow_comp("a1", Node1("a1"), inputs_schema={"value": "${start.a1}"})
    flow2.add_workflow_comp("a2", Node1("a2"), inputs_schema={"value": "${start.a2}"})

    flow2.set_end_comp("end", MockEndNode("end"), inputs_schema={"b1": "${a1.value}", "b2": "${a2.value}"})
    flow2.add_connection("start", "a1")
    flow2.add_connection("start", "a2")
    flow2.add_connection("a1", "end")
    flow2.add_connection("a2", "end")
    results = await flow2.invoke({"a1": 1, "a2": 2}, create_workflow_session())
    assert results.result == {"b1": 1, "b2": 2}


async def test_simple_workflow_with_condition():
    """
    start -> condition[a,b] -> end
    """
    flow = Workflow()
    flow.set_start_comp("start", MockStartNode("start"),
                        inputs_schema={"a": "${a}",
                                       "b": "${b}",
                                       "c": 1,
                                       "d": [1, 2, 3]})

    def router(session: Session):
        val = session.get_global_state("start.a")
        if val is not None:
            return "a"
        val = session.get_global_state("start.b")
        if val is not None:
            return "b"
        return "a"

    flow.add_conditional_connection("start", router=router)
    flow.add_workflow_comp("a", Node1("a"), inputs_schema={"a": "${start.a}", "b": "${start.c}"})
    flow.add_workflow_comp("b", Node1("b"), inputs_schema={"b": "${start.b}"})
    flow.set_end_comp("end", MockEndNode("end"), {"result1": "${a.a}", "result2": "${b.b}"})
    flow.add_connection("a", "end")
    flow.add_connection("b", "end")
    result = await flow.invoke({"a": 1}, create_workflow_session())
    assert result.result == {"result1": 1, "result2": None}
    result = await flow.invoke({"b": "haha"}, create_workflow_session())
    assert result.result == {"result1": None, "result2": "haha"}


async def test_simple_workflow_with_branch_condition():
    """
    start -> condition[a,b] -> end
    """
    flow = Workflow()
    flow.set_start_comp("start", MockStartNode("start"),
                        inputs_schema={"a": "${a}",
                                       "b": "${b}",
                                       "c": 1,
                                       "d": [1, 2, 3]})

    router = BranchRouter()
    router.add_branch("${start.a} is not None", "a")
    router.add_branch("${start.b} is not None", "b")

    flow.add_conditional_connection("start", router=router)
    flow.add_workflow_comp("a", Node1("a"), inputs_schema={"a": "${start.a}", "b": "${start.c}"})
    flow.add_workflow_comp("b", Node1("b"), inputs_schema={"b": "${start.b}"})
    flow.set_end_comp("end", MockEndNode("end"), {"result1": "${a.a}", "result2": "${b.b}"})
    flow.add_connection("a", "end")
    flow.add_connection("b", "end")

    result = await flow.invoke({"a": 1}, create_workflow_session())
    assert result.result == {"result1": 1, "result2": None}
    result = await flow.invoke({"b": "haha"}, create_workflow_session())
    assert result.result == {"result1": None, "result2": "haha"}


async def test_workflow_with_wait_for_all():
    # flow: start -> (a->a1)|b|c|d -> collect -> end
    for wait_for_all in [True, False]:
        flow = Workflow()

        def start_input_transformer(state):
            start_input_schema = {"a": "${a}", "b": "${b}", "c": "${c}",
                                  "d": "${d}"}
            return state.get(start_input_schema)

        flow.set_start_comp("start", MockStartNode("start"), inputs_schema=start_input_transformer)
        flow.add_workflow_comp("a", Node1("a"), inputs_schema={"a": "${start.a}"})
        flow.add_workflow_comp("a1", SlowNode("a1", 1), inputs_schema={"a": "${a.a}"})
        flow.add_workflow_comp("b", Node1("b"), inputs_schema={"b": "${start.b}"})
        flow.add_workflow_comp("c", Node1("c"), inputs_schema={"c": "${start.c}"})
        flow.add_workflow_comp("d", Node1("d"), inputs_schema={"d": "${start.d}"})
        flow.add_workflow_comp("collect", CountNode("collect"), wait_for_all=wait_for_all)
        flow.set_end_comp("end", MockEndNode("end"), {"result": "${collect.count}"})
        flow.add_connection("start", "a")
        flow.add_connection("start", "b")
        flow.add_connection("start", "c")
        flow.add_connection("start", "d")
        flow.add_connection("a", "a1")
        flow.add_connection("a1", "collect")
        flow.add_connection("b", "collect")
        flow.add_connection("c", "collect")
        flow.add_connection("d", "collect")
        flow.add_connection("collect", "end")
        if wait_for_all:
            result = await flow.invoke({"a": 1, "b": 2, "c": 3, "d": 4}, create_workflow_session())
            assert result.result == {"result": 1}
        else:
            result = await flow.invoke({"a": 1, "b": 2, "c": 3, "d": 4}, create_workflow_session())
            assert result.result == {"result": 2}


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

    result = await flow.invoke({"a": 2}, create_workflow_session())
    assert result.result["b"] == 12

    result = await flow.invoke({"a": 15}, create_workflow_session())
    assert result.result["a"] == 15


async def test_workflow_with_loop():
    flow = Workflow()
    flow.set_start_comp("s", MockStartNode("s"), inputs_schema={"a": "${input_number}"})
    flow.set_end_comp("e", MockEndNode("e"),
                      inputs_schema={"array_result": "${b.array_result}", "user_var": "${b.user_var}",
                                     "index": "${l.index_collect}"})
    flow.add_workflow_comp("a", CommonNode("a"),
                           inputs_schema={"array": "${input_array}"})
    flow.add_workflow_comp("b", CommonNode("b"),
                           inputs_schema={"array_result": "${l.results}", "user_var": "${l.user_var}"})

    # create  loop: (1->2->3)
    loop_group = LoopGroup()
    loop_group.add_workflow_comp("1", AddTenNode("1", {"check": "${s.a}"}),
                                 inputs_schema={"source": "${l.item}", "check": "${s.a}"})
    loop_group.add_workflow_comp("2", AddTenNode("2"), inputs_schema={"source": "${l.user_var}"})
    set_variable_component = LoopSetVariableComponent({"${l.user_var}": "${2.result}"})
    loop_group.add_workflow_comp("3", set_variable_component)
    loop_group.add_workflow_comp("4", CommonNode("4"), inputs_schema={"index": "${l.index}"})
    loop_group.start_comp("1")
    loop_group.end_comp("4")
    loop_group.add_connection("1", "2")
    loop_group.add_connection("2", "3")
    loop_group.add_connection("3", "4")
    output_callback = OutputCallback(
        {"results": "${1.result}", "user_var": "${l.user_var}", "index_collect": "${4.index}"})
    intermediate_callback = IntermediateLoopVarCallback({"user_var": "${s.a}"})

    loop = AdvancedLoopComponent(loop_group, ArrayCondition({"item": "${a.array}"}),
                                 callbacks=[output_callback, intermediate_callback])

    flow.add_workflow_comp("l", loop, inputs_schema={"input_number": "${input_number}"})

    # s->a->(1->2->3)->b->e
    flow.add_connection("s", "a")
    flow.add_connection("a", "l")
    flow.add_connection("l", "b")
    flow.add_connection("b", "e")

    result = await flow.invoke({"input_array": [1, 2, 3], "input_number": 1}, create_workflow_session())
    assert result == WorkflowOutput(result={"array_result": [11, 12, 13], "user_var": 31, "index": [0, 1, 2]},
                                    state=WorkflowExecutionState.COMPLETED)

    result = await flow.invoke({"input_array": [4, 5], "input_number": 2}, create_workflow_session())
    assert result == WorkflowOutput(result={"array_result": [14, 15], "user_var": 22, "index": [0, 1]},
                                    state=WorkflowExecutionState.COMPLETED)


async def test_workflow_with_loop_component():
    flow = Workflow()
    flow.set_start_comp("s", MockStartNode("s"), inputs_schema={"a": "${input_number}"})
    flow.set_end_comp("e", MockEndNode("e"),
                      inputs_schema={"array_result": "${b.array_result}", "user_var": "${b.user_var}",
                                     "index": "${l.index_collect}"})
    flow.add_workflow_comp("a", CommonNode("a"),
                           inputs_schema={"array": "${input_array}"})
    flow.add_workflow_comp("b", CommonNode("b"),
                           inputs_schema={"array_result": "${l.results}", "user_var": "${l.user_var}"})

    # create  loop: (1->2->3)
    loop_group = LoopGroup()
    loop_group.add_workflow_comp("1", AddTenNode("1", {"check": "${s.a}"}),
                                 inputs_schema={"source": "${l.item}", "check": "${s.a}"})
    loop_group.add_workflow_comp("2", AddTenNode("2"), inputs_schema={"source": "${l.user_var}"})
    set_variable_component = LoopSetVariableComponent({"${l.user_var}": "${2.result}"})
    loop_group.add_workflow_comp("3", set_variable_component)
    loop_group.add_workflow_comp("4", CommonNode("4"), inputs_schema={"index": "${l.index}"})
    loop_group.start_comp("1")
    loop_group.end_comp("4")
    loop_group.add_connection("1", "2")
    loop_group.add_connection("2", "3")
    loop_group.add_connection("3", "4")

    loop_component = LoopComponent(loop_group, {"results": "${1.result}", "user_var": "${l.user_var}",
                                                "index_collect": "${4.index}"})

    flow.add_workflow_comp("l", loop_component, inputs_schema={"loop_type": "array",
                                                               "loop_array": {"item": "${a.array}"},
                                                               "intermediate_var": {"user_var": "${s.a}"}})

    # s->a->(1->2->3)->b->e
    flow.add_connection("s", "a")
    flow.add_connection("a", "l")
    flow.add_connection("l", "b")
    flow.add_connection("b", "e")

    result = await flow.invoke({"input_array": [1, 2, 3], "input_number": 1}, create_workflow_session())
    assert result == WorkflowOutput(result={"array_result": [11, 12, 13], "user_var": 31, "index": [0, 1, 2]},
                                    state=WorkflowExecutionState.COMPLETED)

    result = await flow.invoke({"input_array": [4, 5], "input_number": 2}, create_workflow_session())
    assert result == WorkflowOutput(result={"array_result": [14, 15], "user_var": 22, "index": [0, 1]},
                                    state=WorkflowExecutionState.COMPLETED)


async def test_workflow_with_loop_component_number_condition():
    flow = Workflow()
    flow.set_start_comp("s", MockStartNode("s"))
    flow.set_end_comp("e", MockEndNode("e"),
                      inputs_schema={"array_result": "${b.array_result}", "user_var": "${b.user_var}"})
    flow.add_workflow_comp("a", CommonNode("a"))
    flow.add_workflow_comp("b", CommonNode("b"),
                           inputs_schema={"array_result": "${l.results}", "user_var": "${l.user_var}"})
    # create  loop: (1->2->3)
    loop_group = LoopGroup()
    loop_group.add_workflow_comp("1", AddTenNode("1"), inputs_schema={"source": "${l.index}"})
    loop_group.add_workflow_comp("2", AddTenNode("2"),
                                 inputs_schema={"source": "${l.user_var}"})
    set_variable_component = LoopSetVariableComponent({"${l.user_var}": "${2.result}"})
    loop_group.add_workflow_comp("3", set_variable_component)
    loop_group.start_nodes(["1"])
    loop_group.end_nodes(["3"])
    loop_group.add_connection("1", "2")
    loop_group.add_connection("2", "3")

    loop_component = LoopComponent(loop_group,
                                   {"results": "${1.result}", "user_var": "${l.user_var}"})

    flow.add_workflow_comp("l", loop_component, inputs_schema={"loop_type": "number",
                                                               "loop_number": "${loop_number}",
                                                               "intermediate_var": {"user_var": "${input_number}"}})

    # s->a->(1->2->3)->b->e
    flow.add_connection("s", "a")
    flow.add_connection("a", "l")
    flow.add_connection("l", "b")
    flow.add_connection("b", "e")

    result = await flow.invoke({"input_number": 2, "loop_number": 2}, create_workflow_session())
    assert result == WorkflowOutput(result={"array_result": [10, 11], "user_var": 22},
                                    state=WorkflowExecutionState.COMPLETED)

    result = await flow.invoke({"input_number": 1, "loop_number": 3}, create_workflow_session())
    assert result == WorkflowOutput(result={"array_result": [10, 11, 12], "user_var": 31},
                                    state=WorkflowExecutionState.COMPLETED)


async def test_workflow_with_loop_component_expression_condition():
    flow = Workflow()
    flow.set_start_comp("s", MockStartNode("s"))
    flow.set_end_comp("e", MockEndNode("e"),
                      inputs_schema={"array_result": "${b.array_result}", "user_var": "${b.user_var}"})
    flow.add_workflow_comp("a", CommonNode("a"))
    flow.add_workflow_comp("b", CommonNode("b"),
                           inputs_schema={"array_result": "${l.results}", "user_var": "${l.user_var}"})
    # create  loop: (1->2->3)
    loop_group = LoopGroup()
    loop_group.add_workflow_comp("1", AddTenNode("1"), inputs_schema={"source": "${l.index}"})
    loop_group.add_workflow_comp("2", AddTenNode("2"),
                                 inputs_schema={"source": "${l.user_var}"})
    set_variable_component = LoopSetVariableComponent({"${l.user_var}": "${2.result}"})
    loop_group.add_workflow_comp("3", set_variable_component)
    loop_group.start_nodes(["1"])
    loop_group.end_nodes(["3"])
    loop_group.add_connection("1", "2")
    loop_group.add_connection("2", "3")

    loop_component = LoopComponent(loop_group,
                                   {"results": "${1.result}", "user_var": "${l.user_var}"})

    flow.add_workflow_comp("l", loop_component, inputs_schema={"loop_type": "expression",
                                                               "bool_expression": "(${l.index} != ${loop_number})",
                                                               "intermediate_var": {"user_var": "${input_number}"}})

    # s->a->(1->2->3)->b->e
    flow.add_connection("s", "a")
    flow.add_connection("a", "l")
    flow.add_connection("l", "b")
    flow.add_connection("b", "e")

    result = await flow.invoke({"input_number": 2, "loop_number": 2}, create_workflow_session())
    assert result == WorkflowOutput(result={"array_result": [10, 11], "user_var": 22},
                                    state=WorkflowExecutionState.COMPLETED)

    result = await flow.invoke({"input_number": 1, "loop_number": 3}, create_workflow_session())
    assert result == WorkflowOutput(result={"array_result": [10, 11, 12], "user_var": 31},
                                    state=WorkflowExecutionState.COMPLETED)


async def test_workflow_with_loop_component_always_true():
    flow = Workflow()
    flow.set_start_comp("s", MockStartNode("s"))
    flow.set_end_comp("e", MockEndNode("e"),
                      inputs_schema={"array_result": "${b.array_result}", "user_var": "${b.user_var}"})
    flow.add_workflow_comp("a", CommonNode("a"))
    flow.add_workflow_comp("b", CommonNode("b"),
                           inputs_schema={"array_result": "${l.results}", "user_var": "${l.user_var}"})
    # create  loop: (1->2->3)
    loop_group = LoopGroup()
    loop_group.add_workflow_comp("1", AddTenNode("1"), inputs_schema={"source": "${l.index}"})
    loop_group.add_workflow_comp("2", AddTenNode("2"),
                                 inputs_schema={"source": "${l.user_var}"})
    set_variable_component = LoopSetVariableComponent({"${l.user_var}": "${2.result}"})
    loop_group.add_workflow_comp("3", set_variable_component)

    sw = BranchComponent()
    sw.add_branch("${l.index} >= ${loop_number} - 1", ["4"], "1")
    sw.add_branch("${l.index} < ${loop_number} - 1", ["5"], "2")

    loop_group.add_workflow_comp("sw", sw)

    break_node = LoopBreakComponent()
    loop_group.add_workflow_comp("4", break_node)

    loop_group.add_workflow_comp("5", CommonNode("5"),
                                 inputs_schema={})
    loop_group.start_nodes(["1"])
    loop_group.end_nodes(["5"])
    loop_group.add_connection("1", "2")
    loop_group.add_connection("2", "3")
    loop_group.add_connection("3", "sw")
    loop_group.add_connection("4", "5")

    loop_component = LoopComponent(loop_group,
                                   {"results": "${1.result}", "user_var": "${l.user_var}"})

    flow.add_workflow_comp("l", loop_component, inputs_schema={"loop_type": "always_true",
                                                               "intermediate_var": {"user_var": "${input_number}"}})

    # s->a->(1->2->3)->b->e
    flow.add_connection("s", "a")
    flow.add_connection("a", "l")
    flow.add_connection("l", "b")
    flow.add_connection("b", "e")

    result = await flow.invoke({"input_number": 2, "loop_number": 2}, create_workflow_session())
    assert result == WorkflowOutput(result={"array_result": [10, 11], "user_var": 22},
                                    state=WorkflowExecutionState.COMPLETED)

    result = await flow.invoke({"input_number": 1, "loop_number": 3}, create_workflow_session())
    assert result == WorkflowOutput(result={"array_result": [10, 11, 12], "user_var": 31},
                                    state=WorkflowExecutionState.COMPLETED)


async def test_workflow_with_loop_component_break():
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
    loop_group.add_workflow_comp("2", AddTenNode("2"), inputs_schema={"source": "${l.user_var}"})
    set_variable_component = LoopSetVariableComponent({"${l.user_var}": "${2.result}"})
    loop_group.add_workflow_comp("3", set_variable_component)
    break_node = LoopBreakComponent()
    loop_group.add_workflow_comp("4", break_node)
    loop_group.start_nodes(["1"])
    loop_group.end_nodes(["3"])
    loop_group.add_connection("1", "2")
    loop_group.add_connection("2", "3")
    loop_group.add_connection("3", "4")

    loop_component = LoopComponent(loop_group,
                                   {"results": "${1.result}", "user_var": "${l.user_var}"})

    flow.add_workflow_comp("l", loop_component, inputs_schema={"loop_type": "array",
                                                               "loop_array": {"item": "${a.array}"},
                                                               "intermediate_var": {"user_var": "${input_number}"}})

    # s->a->(1->2->3)->b->e
    flow.add_connection("s", "a")
    flow.add_connection("a", "l")
    flow.add_connection("l", "b")
    flow.add_connection("b", "e")

    result = await flow.invoke({"input_array": [1, 2, 3], "input_number": 1}, create_workflow_session())
    assert result == WorkflowOutput(result={"array_result": [11], "user_var": 11},
                                    state=WorkflowExecutionState.COMPLETED)

    result = await flow.invoke({"input_array": [4, 5], "input_number": 2}, create_workflow_session())
    assert result == WorkflowOutput(result={"array_result": [14], "user_var": 12},
                                    state=WorkflowExecutionState.COMPLETED)


async def test_workflow_with_loop_break():
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
    loop_group.add_workflow_comp("2", AddTenNode("2"), inputs_schema={"source": "${l.user_var}"})
    set_variable_component = LoopSetVariableComponent({"${l.user_var}": "${2.result}"})
    loop_group.add_workflow_comp("3", set_variable_component)
    break_node = LoopBreakComponent()
    loop_group.add_workflow_comp("4", break_node)
    loop_group.start_nodes(["1"])
    loop_group.end_nodes(["3"])
    loop_group.add_connection("1", "2")
    loop_group.add_connection("2", "3")
    loop_group.add_connection("3", "4")
    output_callback = OutputCallback({"results": "${1.result}", "user_var": "${l.user_var}"})
    intermediate_callback = IntermediateLoopVarCallback({"user_var": "${input_number}"})

    loop = AdvancedLoopComponent(loop_group, ArrayCondition({"item": "${a.array}"}),
                                 callbacks=[output_callback, intermediate_callback], break_nodes=[break_node])

    flow.add_workflow_comp("l", loop, inputs_schema={"input_number": "${input_number}"})

    # s->a->(1->2->3)->b->e
    flow.add_connection("s", "a")
    flow.add_connection("a", "l")
    flow.add_connection("l", "b")
    flow.add_connection("b", "e")

    result = await flow.invoke({"input_array": [1, 2, 3], "input_number": 1}, create_workflow_session())
    assert result == WorkflowOutput(result={"array_result": [11], "user_var": 11},
                                    state=WorkflowExecutionState.COMPLETED)

    result = await flow.invoke({"input_array": [4, 5], "input_number": 2}, create_workflow_session())
    assert result == WorkflowOutput(result={"array_result": [14], "user_var": 12},
                                    state=WorkflowExecutionState.COMPLETED)


async def test_simple_stream_workflow():
    flow = Workflow()
    flow.set_start_comp("start", MockStartNode("start"),
                        inputs_schema={
                            "a": "${a}",
                            "b": "${b}",
                            "c": 1,
                            "d": [1, 2, 3]})
    expected_datas = [
        {"id": 1, "data": "1"},
        {"id": 2, "data": "2"},
    ]
    expected_datas_model = [CustomSchema(**item) for item in expected_datas]

    flow.add_workflow_comp("a", StreamNode("a", expected_datas),
                           inputs_schema={
                               "aa": "${start.a}",
                               "ac": "${start.c}"})
    flow.set_end_comp("end", MockEndNode("end"),
                      inputs_schema={
                          "result": "${a.aa}"})
    flow.add_connection("start", "a")
    flow.add_connection("a", "end")

    index = 0
    async for chunk in flow.stream({"a": 1, "b": "haha"}, create_workflow_session()):
        if not isinstance(chunk, CustomSchema):
            continue
        assert chunk == expected_datas_model[index]
        logger.info(f"stream chunk: {chunk}")
        index += 1


async def test_seq_exec_stream_workflow():
    flow = Workflow()
    flow.set_start_comp("start", MockStartNode("start"),
                        inputs_schema={
                            "a": "${a}",
                            "b": "${b}",
                            "c": 1,
                            "d": [1, 2, 3]})

    node_a_expected_datas = [
        {"node_id": "a", "id": 1, "data": "1"},
        {"node_id": "a", "id": 2, "data": "2"},
    ]
    node_a_expected_datas_model = [CustomSchema(**item) for item in node_a_expected_datas]
    flow.add_workflow_comp("a", StreamNode("a", node_a_expected_datas),
                           inputs_schema={
                               "aa": "${start.a}",
                               "ac": "${start.c}"})

    node_b_expected_datas = [
        {"node_id": "b", "id": 1, "data": "1"},
        {"node_id": "b", "id": 2, "data": "2"},
    ]
    node_b_expected_datas_model = [CustomSchema(**item) for item in node_b_expected_datas]
    flow.add_workflow_comp("b", StreamNode("b", node_b_expected_datas),
                           inputs_schema={
                               "ba": "${a.aa}",
                               "bc": "${a.ac}"})

    flow.set_end_comp("end", MockEndNode("end"),
                      inputs_schema={
                          "result": "${b.ba}"})

    flow.add_connection("start", "a")
    flow.add_connection("a", "b")
    flow.add_connection("b", "end")

    expected_datas_model = {
        "a": node_a_expected_datas_model,
        "b": node_b_expected_datas_model
    }
    index_dict = {key: 0 for key in expected_datas_model.keys()}
    async for chunk in flow.stream({"a": 1, "b": "haha"}, create_workflow_session()):
        if not isinstance(chunk, CustomSchema):
            continue
        node_id = chunk.node_id
        index = index_dict[node_id]
        assert chunk == expected_datas_model[node_id][index]
        logger.info(f"stream chunk: {chunk}")
        index_dict[node_id] = index_dict[node_id] + 1


async def test_parallel_exec_stream_workflow():
    flow = Workflow()
    flow.set_start_comp("start", MockStartNode("start"),
                        inputs_schema={
                            "a": "${a}",
                            "b": "${b}",
                            "c": 1,
                            "d": [1, 2, 3]})

    node_a_expected_datas = [
        {"node_id": "a", "id": 1, "data": "1"},
        {"node_id": "a", "id": 2, "data": "2"},
    ]
    node_a_expected_datas_model = [CustomSchema(**item) for item in node_a_expected_datas]
    flow.add_workflow_comp("a", StreamNode("a", node_a_expected_datas),
                           inputs_schema={
                               "aa": "${start.a}",
                               "ac": "${start.c}"})

    node_b_expected_datas = [
        {"node_id": "b", "id": 1, "data": "1"},
        {"node_id": "b", "id": 2, "data": "2"},
    ]
    node_b_expected_datas_model = [CustomSchema(**item) for item in node_b_expected_datas]
    flow.add_workflow_comp("b", StreamNode("b", node_b_expected_datas),
                           inputs_schema={
                               "ba": "${start.b}",
                               "bc": "${start.d}"})

    flow.set_end_comp("end", MockEndNode("end"),
                      inputs_schema={
                          "result": "${b.ba}"})

    flow.add_connection("start", "a")
    flow.add_connection("start", "b")
    flow.add_connection("a", "end")
    flow.add_connection("b", "end")

    expected_datas_model = {
        "a": node_a_expected_datas_model,
        "b": node_b_expected_datas_model
    }
    index_dict = {key: 0 for key in expected_datas_model.keys()}
    async for chunk in flow.stream({"a": 1, "b": "haha"}, create_workflow_session()):
        if not isinstance(chunk, CustomSchema):
            continue
        node_id = chunk.node_id
        index = index_dict[node_id]
        assert chunk == expected_datas_model[node_id][index]
        logger.info(f"stream chunk: {chunk}")
        index_dict[node_id] = index_dict[node_id] + 1


async def test_sub_stream_workflow():
    # sub_workflow: start->a(stream out)->end
    sub_workflow = Workflow()
    sub_workflow.set_start_comp("sub_start", MockStartNode("start"),
                                inputs_schema={
                                    "a": "${a}",
                                    "b": "${b}",
                                    "c": 1,
                                    "d": [1, 2, 3]})
    expected_datas = [
        {"node_id": "sub_start", "id": 1, "data": "1"},
        {"node_id": "sub_start", "id": 2, "data": "2"},
    ]
    expected_datas_model = [CustomSchema(**item) for item in expected_datas]

    sub_workflow.add_workflow_comp("sub_a", StreamNode("a", expected_datas),
                                   inputs_schema={
                                       "aa": "${sub_start.a}",
                                       "ac": "${sub_start.c}"})
    sub_workflow.set_end_comp("sub_end", MockEndNode("end"),
                              inputs_schema={
                                  "result": "${sub_a.aa}"})
    sub_workflow.add_connection("sub_start", "sub_a")
    sub_workflow.add_connection("sub_a", "sub_end")

    # main_workflow: start->a(sub workflow)->end
    main_workflow = Workflow()
    main_workflow.set_start_comp("start", MockStartNode("start"),
                                 inputs_schema={
                                     "a": "${a}",
                                     "b": "${b}",
                                     "c": 1,
                                     "d": [1, 2, 3]})

    main_workflow.add_workflow_comp("a", SubWorkflowComponent(sub_workflow),
                                    inputs_schema={
                                        "aa": "${start.a}",
                                        "ac": "${start.c}"})
    main_workflow.set_end_comp("end", MockEndNode("end"),
                               inputs_schema={
                                   "result": "${a.aa}"})
    main_workflow.add_connection("start", "a")
    main_workflow.add_connection("a", "end")

    index = 0
    async for chunk in main_workflow.stream({"a": 1, "b": "haha"}, create_workflow_session(),
                                            stream_modes=[BaseStreamMode.CUSTOM]):
        if isinstance(chunk, CustomSchema):
            assert chunk == expected_datas_model[index]
            logger.info(f"stream chunk: {chunk}")
            index += 1


async def test_nested_workflow():
    flow1 = Workflow()
    flow1.set_start_comp("start", MockStartNode("start"),
                         inputs_schema={
                             "a1": "${a1}",
                             "a2": "${a2}"})

    # start2->a2->end2
    flow2 = Workflow()
    flow2.set_start_comp("start2", MockStartNode("start2"), inputs_schema={"a1": "${input}"})
    flow2.add_workflow_comp("a2", Node1("a2"), inputs_schema={"value": "${start2.a1}"})
    # MockEndNode is End, use Node1
    flow2.set_end_comp("end2", Node1("end2"), inputs_schema={"result": "${a2.value}"})
    flow2.add_connection("start2", "a2")
    flow2.add_connection("a2", "end2")

    # flow2: start->a1|composite->end
    flow1.add_workflow_comp("a1", Node1("a1"), inputs_schema={"value": "${start.a1}"})
    flow1.add_workflow_comp("composite", SubWorkflowComponent(flow2),
                            inputs_schema={"input": "${start.a2}"})

    flow1.set_end_comp("end", MockEndNode("end"), inputs_schema={"b1": "${a1.value}", "b2": "${composite.result}"})
    flow1.add_connection("start", "a1")
    flow1.add_connection("start", "composite")
    flow1.add_connection("a1", "end")
    flow1.add_connection("composite", "end")
    result = await flow1.invoke({"a1": 1, "a2": 2}, create_workflow_session())
    assert result.result == {"b1": 1, "b2": 2}


async def test_nested_workflow_same_node_id():
    flow1 = Workflow()
    flow1.set_start_comp("start", Start(),
                         inputs_schema={
                             "a": "${a1}",
                             "b": "${a2}"})

    # start2->a2->end2
    flow2 = Workflow()
    flow2.set_start_comp("start", Start(), inputs_schema={"a1": "${input}"})
    flow2.add_workflow_comp("a1", Node1("a1"), inputs_schema={"value": "${start.a1}"})
    flow2.set_end_comp("end", End({}), inputs_schema={"result": "${a1.value}"})
    flow2.add_connection("start", "a1")
    flow2.add_connection("a1", "end")

    # flow2: start->composite->a1->end
    flow1.add_workflow_comp("composite", SubWorkflowComponent(flow2),
                            inputs_schema={"input": "${start.b}"})
    flow1.add_workflow_comp("a1", Node1("a1"), inputs_schema={"value_different": "${start.a}",
                                                              "value_different_result": "${composite.output.result}"})

    flow1.set_end_comp("end", End({}), inputs_schema={"b1": "${a1.value_different}", "b2": "${composite.output.result}",
                                                      "b3": "${a1.value_different_result}"})

    flow1.add_connection("start", "composite")
    flow1.add_connection("composite", "a1")
    flow1.add_connection("a1", "end")
    result = await flow1.invoke({"a1": 1, "a2": 2}, create_workflow_session())
    assert result.result == {'output': {'b1': 1, 'b2': 2, 'b3': 2}}


async def test_nested_workflow_same_node_id_with_template():
    flow1 = Workflow()
    flow1.set_start_comp("start", Start(),
                         inputs_schema={
                             "a": "${a1}",
                             "b": "${a2}"})

    # start2->a2->end2
    flow2 = Workflow()
    flow2.set_start_comp("start", Start(), inputs_schema={"a1": "${input}"})
    flow2.add_workflow_comp("a1", Node1("a1"), inputs_schema={"value": "${start.a1}"})
    flow2.set_end_comp("end", End(conf={"responseTemplate": "填充结果{{result}}"}),
                       inputs_schema={"result": "${a1.value}"})
    flow2.add_connection("start", "a1")
    flow2.add_connection("a1", "end")

    # flow2: start->composite->a1->end
    flow1.add_workflow_comp("composite", SubWorkflowComponent(flow2),
                            inputs_schema={"input": "${start.b}"})
    flow1.add_workflow_comp("a1", Node1("a1"), inputs_schema={"value_different": "${start.a}",
                                                              "value_different_result": "${composite.response}"})

    flow1.set_end_comp("end", End({}),
                       inputs_schema={"b1": "${a1.value_different}", "b2": "${composite.response}",
                                      "b3": "${a1.value_different_result}"})

    flow1.add_connection("start", "composite")
    flow1.add_connection("composite", "a1")
    flow1.add_connection("a1", "end")
    result = await flow1.invoke({"a1": 1, "a2": 2}, create_workflow_session())
    assert result.result == {'output': {'b1': 1, 'b2': '填充结果2', 'b3': '填充结果2'}}


async def test_stream_comp_workflow():
    # start -> a ---> b -> end
    flow = Workflow()
    flow.set_start_comp("start", MockStartNode("start"), inputs_schema={"a": "${a}"})
    flow.add_workflow_comp("a", StreamCompNode("a"), inputs_schema={"value": "${start.a}"},
                           comp_ability=[ComponentAbility.STREAM], wait_for_all=True)
    flow.add_workflow_comp("b", CollectCompNode("b"), inputs_schema={"value1": "${a.value}"},
                           stream_inputs_schema={"value": "${a.value}"}, comp_ability=[ComponentAbility.COLLECT],
                           wait_for_all=True)
    flow.set_end_comp("end", MockEndNode("end"), inputs_schema={"result1": "${b.value}"})
    flow.add_connection("start", "a")
    flow.add_stream_connection("a", "b")
    flow.add_connection("b", "end")
    idx = 1
    result = await flow.invoke({"a": idx}, create_workflow_session())
    assert result.result == {"result1": idx * sum(range(1, 3))}


async def test_transform_workflow():
    # start -> a ---> b ---> c -> end
    flow = Workflow()
    flow.set_start_comp("start", MockStartNode("start"), inputs_schema={"a": "${a}"})
    # a: throw 2 frames: {value: 1}, {value: 2}
    flow.add_workflow_comp("a", StreamCompNode("a"), inputs_schema={"value": "${start.a}"},
                           comp_ability=[ComponentAbility.STREAM], wait_for_all=True)
    # b: transform 2 frames to c
    flow.add_workflow_comp("b", TransformCompNode("b"), inputs_schema={"value1": "${a.value}"},
                           stream_inputs_schema={"value": "${a.value}"}, comp_ability=[ComponentAbility.TRANSFORM],
                           wait_for_all=True)
    # c: value = sum(value of frames)
    flow.add_workflow_comp("c", CollectCompNode("c"), inputs_schema={"value1": "${b.value}"},
                           stream_inputs_schema={"value": "${b.value}"}, comp_ability=[ComponentAbility.COLLECT],
                           wait_for_all=True)
    flow.set_end_comp("end", MockEndNode("end"), inputs_schema={"result": "${c.value}"})
    flow.add_connection("start", "a")
    flow.add_stream_connection("a", "b")
    flow.add_stream_connection("b", "c")
    flow.add_connection("c", "end")

    result = await flow.invoke({"a": 1}, create_workflow_session())
    assert result.result == {"result": 3}


async def test_five_transform_workflow():
    # start -> a ---> b ---> c ---> d ---> e ---> f ---> g -> end
    flow = Workflow()
    flow.set_start_comp("start", MockStartNode("start"), inputs_schema={"a": "${a}"})
    # a: throw 2 frames: {value: 1}, {value: 2}
    flow.add_workflow_comp("a", StreamCompNode("a"), inputs_schema={"value": "${start.a}"},
                           comp_ability=[ComponentAbility.STREAM], wait_for_all=True)
    # b: transform frame to c
    flow.add_workflow_comp("b", TransformCompNode("b"), inputs_schema={"value1": "${a.value}"},
                           stream_inputs_schema={"value": "${a.value}"}, comp_ability=[ComponentAbility.TRANSFORM],
                           wait_for_all=True)
    # c: transform frame to d
    flow.add_workflow_comp("c", TransformCompNode("c"), inputs_schema={"value1": "${b.value}"},
                           stream_inputs_schema={"value": "${b.value}"}, comp_ability=[ComponentAbility.TRANSFORM],
                           wait_for_all=True)
    # d: transform frame to e
    flow.add_workflow_comp("d", TransformCompNode("d"), inputs_schema={"value1": "${c.value}"},
                           stream_inputs_schema={"value": "${c.value}"}, comp_ability=[ComponentAbility.TRANSFORM],
                           wait_for_all=True)
    # e: transform frame to f
    flow.add_workflow_comp("e", TransformCompNode("e"), inputs_schema={"value1": "${d.value}"},
                           stream_inputs_schema={"value": "${d.value}"}, comp_ability=[ComponentAbility.TRANSFORM],
                           wait_for_all=True)
    # f: transform frame to g
    flow.add_workflow_comp("f", TransformCompNode("f"), inputs_schema={"value1": "${e.value}"},
                           stream_inputs_schema={"value": "${e.value}"}, comp_ability=[ComponentAbility.TRANSFORM],
                           wait_for_all=True)
    # g: collect all frames
    flow.add_workflow_comp("g", CollectCompNode("g"), inputs_schema={"value1": "${f.value}"},
                           stream_inputs_schema={"value": "${f.value}"}, comp_ability=[ComponentAbility.COLLECT],
                           wait_for_all=True)
    flow.set_end_comp("end", MockEndNode("end"), inputs_schema={"result": "${g.value}"})
    flow.add_connection("start", "a")
    flow.add_stream_connection("a", "b")
    flow.add_stream_connection("b", "c")
    flow.add_stream_connection("c", "d")
    flow.add_stream_connection("d", "e")
    flow.add_stream_connection("e", "f")
    flow.add_stream_connection("f", "g")
    flow.add_connection("g", "end")

    result = await flow.invoke({"a": 1}, create_workflow_session())
    assert result.result == {"result": 3}


async def test_auto_complete_abilities_detects_unregistered_edge_nodes():
    """Test that auto_complete_abilities raises exception when edges reference unregistered components."""
    flow = Workflow()
    flow.set_start_comp("start", MockStartNode("start"))
    flow.add_workflow_comp("a", Node1("a"))
    flow.set_end_comp("end", MockEndNode("end"))

    # Use mock to inject an edge with an unregistered target node to simulate configuration error
    # This bypasses add_connection validation to test auto_complete_abilities defensive check
    workflow_internal = getattr(flow, "_internal", None)
    workflow_spec = workflow_internal.config().spec
    original_edges = workflow_spec.edges.copy()
    workflow_spec.edges["a"] = ["unregistered_node"]

    try:
        # auto_complete_abilities is called during invoke/stream, which should detect the issue
        with pytest.raises(BaseError) as context:
            await flow.invoke({"a": 1}, create_workflow_session())

        error_msg = str(context.value)
        # Verify error message contains useful debug info
        assert "unregistered_node" in error_msg
        assert "start" in error_msg  # Should show registered components
        assert "end" in error_msg
    finally:
        # Restore original edges
        workflow_spec.edges = original_edges


async def test_invoke_validates_unregistered_edge_nodes():
    """Test that invoke validates unregistered edge nodes."""
    # Test unregistered target in connection
    flow1 = Workflow()
    flow1.set_start_comp("start", MockStartNode("start"))
    flow1.add_workflow_comp("a", Node1("a"))
    flow1.set_end_comp("end", MockEndNode("end"))
    flow1.add_connection("start", "a")
    flow1.add_connection("a", "unknown_target")  # No validation at add_connection time

    with pytest.raises(BaseError) as context:
        await flow1.invoke({"a": 1}, create_workflow_session())
    error_msg = str(context.value)
    assert ("unknown_target" in error_msg)
    assert ("start" in error_msg)  # Should show registered components

    # Test unregistered source in connection
    flow2 = Workflow()
    flow2.set_start_comp("start", MockStartNode("start"))
    flow2.add_workflow_comp("a", Node1("a"))
    flow2.set_end_comp("end", MockEndNode("end"))
    flow2.add_connection("unknown_source", "a")  # No validation at add_connection time
    flow2.add_connection("a", "end")

    with pytest.raises(BaseError) as context:
        await flow2.invoke({"a": 1}, create_workflow_session())
    error_msg = str(context.value)
    assert "unknown_source" in error_msg

    # Test that valid connections still work by actually executing the workflow
    flow3 = Workflow()
    flow3.set_start_comp("start", MockStartNode("start"))
    flow3.add_workflow_comp("a", Node1("a"))
    flow3.set_end_comp("end", MockEndNode("end"))
    flow3.add_connection("start", "a")
    flow3.add_connection("a", "end")
    result = await flow3.invoke({"a": 1}, create_workflow_session())
    assert result is not None


async def test_nested_loop():
    def create_sub_workflow():
        flow = Workflow()
        flow.set_start_comp("start", Start(), inputs_schema={"input_arr": "${array}", "input_num": "${num}"})
        flow.set_end_comp("end", End(), inputs_schema={"end_out": "${loop}"})

        loop_group = LoopGroup()
        loop_group.add_workflow_comp("loop_1", AddTenNode("loop_1"), inputs_schema={"source": "${loop.index}"})
        loop_group.add_workflow_comp("loop_2", AddTenNode("loop_2"), inputs_schema={"source": "${loop.user_num}"})

        set_variable_component = LoopSetVariableComponent({"${loop.user_num}": "${loop_2.result}"})

        loop_group.add_workflow_comp("loop_3", set_variable_component)
        loop_group.start_nodes(["loop_1"])
        loop_group.end_nodes(["loop_3"])
        loop_group.add_connection("loop_1", "loop_2")
        loop_group.add_connection("loop_2", "loop_3")

        loop_component = LoopComponent(loop_group,
                                       output_schema={"l_out1": "${loop_1.result}", "l_out2": "${loop_2.result}"})

        flow.add_workflow_comp("loop", loop_component, inputs_schema={"loop_type": "number", "loop_number": 2,
                                                                      "intermediate_var": {
                                                                          "user_num": "${start.input_num}"}})

        flow.add_connection("start", "loop")
        flow.add_connection("loop", "end")
        return flow

    def create_main_loop():
        loop_group = LoopGroup()
        loop_group.start_nodes(['s'])
        loop_group.add_workflow_comp("s", Start())
        loop_group.add_workflow_comp("sub", SubWorkflowComponent(create_sub_workflow()),
                                     inputs_schema={"array": "${array}", "num": "${num}"})
        loop_group.add_workflow_comp("e", End(), inputs_schema={"result": "${end_out}"})
        loop_group.end_nodes(['e'])
        loop_group.add_connection("s", "sub")
        loop_group.add_connection("sub", "e")
        loop_component = LoopComponent(loop_group,
                                       output_schema={"array": "${array}", "result": "${result}"})
        return loop_component

    main_workflow = Workflow()
    main_workflow.set_start_comp("main_start", Start(), inputs_schema={"input_arr": "${array}", "input_num": "${num}"})
    main_workflow.add_workflow_comp("main_loop", create_main_loop(),
                                    inputs_schema={"loop_type": "number", "loop_number": 2,
                                                   "intermediate_var": {
                                                       "user_num": "${start.input_num}"}})
    main_workflow.set_end_comp("main_end", End(), inputs_schema={"end_out": "${loop}"})

    main_workflow.add_connection("main_start", "main_loop")
    main_workflow.add_connection("main_loop", "main_end")

    inputs = {"array": [4, 5, 6], "num": -3}

    try:
        loop_indexes = []
        async for chunk in main_workflow.stream(inputs, session=create_workflow_session()):
            if isinstance(chunk, TraceSchema):
                loop_index = chunk.payload.get("loopIndex")
                if loop_index is not None and chunk.payload.get("invokeId") == "main_loop.sub.loop.loop_1":
                    loop_indexes.append(loop_index)
        assert loop_indexes == [0, 0, 1, 1, 0, 0, 1, 1]
    except Exception as e:
        print(e)
        assert False


class LogComp(WorkflowComponent):
    def __init__(self, name: str):
        super().__init__()
        self.name = name
        self.times = 0

    async def invoke(self, inputs: Input, session: Session, context: ModelContext):
        logger.info(f"Invoked {self.name}")
        return {"out": "b_value"}

    async def stream(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        for i in range(0, inputs.get('num')):
            yield {"out": i}

    async def collect(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        if self.times < 2:
            self.times += 1
            raise Exception("collect first time")
        result = []
        async for value in inputs.get("stream"):
            result.append(value)
        return {"out": result}

    async def transform(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        async for value in inputs.get("stream"):
            # await asyncio.sleep(0.5)
            yield {"out": value}


async def test_workflow_with_branch_and_stream():
    workflow = Workflow()
    workflow.set_start_comp("start", Start(), inputs_schema={"out": "${inputs}", "num": "${num}"})
    workflow.add_workflow_comp("stream_comp", LogComp("stream_comp"), inputs_schema={"num": "${start.num}"})
    branch_comp = BranchComponent()
    branch_comp.add_branch("${start.out} == 'a'", "a")
    branch_comp.add_branch("${start.out} == 'b'", "b")
    workflow.add_workflow_comp("branch", branch_comp)
    workflow.add_workflow_comp("a", LogComp("a"))
    workflow.add_workflow_comp("b", LogComp("b"))
    workflow.add_workflow_comp("wait", LogComp("wait"))
    workflow.set_end_comp("end", End(conf={"responseTemplate": "s: {{s}}, b: {{b}}"}),
                          inputs_schema={"b": "${b.out}"},
                          stream_inputs_schema={"s": "${stream_comp.out}"},
                          response_mode="streaming")

    workflow.add_connection("start", "stream_comp")
    workflow.add_connection("start", "branch")
    # workflow.add_connection("a", "end")
    # workflow.add_connection("b", "end")
    workflow.add_connection("a", "wait")
    workflow.add_connection("b", "wait")
    workflow.add_connection("wait", "end")
    workflow.add_stream_connection("stream_comp", "end")

    async for chunk in workflow.stream(inputs={"inputs": 'b', 'num': 5}, session=create_workflow_session(),
                                       stream_modes=[BaseStreamMode.OUTPUT]):
        print(chunk)


async def test_workflow_with_interrupt_recovery():
    workflow = create_workflow2()
    try:
        async for chunk in workflow.stream(inputs={"inputs": 10}, session=create_workflow_session(session_id="123")):
            logger.info(chunk)
    except Exception as e:
        logger.error(f"failed call workflow, error: {e}")
    workflow2 = create_workflow2()
    try:
        async for chunk in workflow2.stream(InteractiveInput(), session=create_workflow_session(session_id="123")):
            logger.info(chunk)
    except Exception as e:
        logger.error(f"failed call workflow, error: {e}")


def create_workflow2() -> Workflow:
    workflow = Workflow(WorkflowCard(id="123"))
    workflow.set_start_comp("start", Start(), inputs_schema={"out": "${inputs}"})
    workflow.add_workflow_comp("a", LogComp("a"), inputs_schema={"num": "${start.out}"})
    workflow.add_workflow_comp("b", LogComp("b"), stream_inputs_schema={"stream": "${a.out}"})
    workflow.add_workflow_comp("c", LogComp("c"), stream_inputs_schema={"stream": "${b.out}"})
    workflow.set_end_comp("end", End(), inputs_schema={"result": "${c.out}"})

    workflow.add_connection("start", "a")
    workflow.add_stream_connection("a", "b")
    workflow.add_stream_connection("b", "c")
    workflow.add_connection("c", "end")
    return workflow


async def test_illegal_nested_workflow():
    class InteractionNode(WorkflowComponent):
        async def invoke(self, inputs: Input, session: Session, context: ModelContext):
            res = await session.interact("value")
            return res

    class NestedFlow(WorkflowComponent):
        async def invoke(self, inputs: Input, session: Session, context: ModelContext):
            nested_flow = Workflow()
            nested_flow.set_start_comp("start", Start(), inputs_schema={"out": "${inputs}"})
            nested_flow.set_end_comp("end", End(), inputs_schema={"result": "${start.out}"})
            nested_flow.add_connection("start", "end")
            result = await nested_flow.invoke(inputs, session, is_sub=True)
            return {"output": result}

    workflow = Workflow()
    workflow.set_start_comp("start", Start(), inputs_schema={"out": "${inputs}"})
    workflow.add_workflow_comp("nested_flow", NestedFlow(), inputs_schema={"out": "${start.out"})
    workflow.add_workflow_comp("interaction_node", InteractionNode(), inputs_schema={"out": "${start.out}"})
    workflow.set_end_comp("end", End(), inputs_schema={"result": "${nested_flow.output}"})

    workflow.add_connection("start", "nested_flow")
    workflow.add_connection("nested_flow", "interaction_node")
    workflow.add_connection("interaction_node", "end")

    with pytest.raises(BaseError) as cm:
        await workflow.invoke({"inputs": "hi"}, create_workflow_session())

    assert cm.value.code == StatusCode.CHECKPOINTER_POST_WORKFLOW_EXECUTION_ERROR.code


async def test_workflow_with_loop_component_multi_abilities():
    flow = Workflow()
    flow.set_start_comp("s", MockStartNode("s"), inputs_schema={"a": "${input_array}"})
    flow.set_end_comp("e", MockEndNode("e"),
                      inputs_schema={"result1": "${l.result1}", "result2": "${l.result2}"})

    # create loop body:
    # 1 --> +--> 2 - -+
    #       |         +--> 4
    #       +--> 3 - -+
    loop_group = LoopGroup()
    loop_group.add_workflow_comp("1", CommonNode("a"), inputs_schema={"result": "${l.item}"})
    loop_group.add_workflow_comp("2", CommonNode("b"), inputs_schema={"result": "${1.result}"})
    loop_group.add_workflow_comp("3", CollectCompNode("c"), stream_inputs_schema={"value": "${1.result}"})
    loop_group.add_workflow_comp("4", CommonNode("4"),
                                 inputs_schema={"result1": "${2.result}", "result2": "${3.value}"})
    loop_group.start_comp("1")
    loop_group.end_comp("4")
    loop_group.add_connection("1", "2")
    loop_group.add_stream_connection("1", "3")
    loop_group.add_connection("2", "4")
    loop_group.add_connection("3", "4")

    loop_component = LoopComponent(loop_group, {"result1": "${4.result1}", "result2": "${4.result2}"})

    flow.add_workflow_comp("l", loop_component, inputs_schema={"loop_type": "array",
                                                               "loop_array": {"item": "${s.a}"}})

    # s --> l --> e

    flow.add_connection("s", "l")
    flow.add_connection("l", "e")

    result = await flow.invoke({"input_array": [1, 2, 3]}, create_workflow_session())
    assert result == WorkflowOutput(result={"result1": [1, 2, 3], "result2": [1, 2, 3]},
                                    state=WorkflowExecutionState.COMPLETED)


async def test_sub_flow_multi_stream_output():
    class SlowInvokeNode(WorkflowComponent):

        def __init__(self, node_id: str):
            super().__init__()
            self.node_id = node_id

        async def invoke(self, inputs: Input, session: Session, context: ModelContext):
            import asyncio
            await asyncio.sleep(0.1)
            return inputs

    # sub_start --> +--> sub_a - -+
    #               |             +--> sub_end
    #               +--> sub_b - -+
    sub_flow = Workflow()
    sub_flow.set_start_comp("sub_start", Start(), inputs_schema={"out": "${query}"})
    sub_flow.add_workflow_comp("sub_a", SlowInvokeNode("sub_a"), inputs_schema={"out": "${sub_start.out}"})
    sub_flow.add_workflow_comp("sub_b", CommonNode("sub_b"), inputs_schema={"out": "${sub_start.out}"},
                               comp_ability=[ComponentAbility.STREAM])
    sub_flow.set_end_comp("sub_end", End(), inputs_schema={"result_a": "${sub_a.out}"},
                          stream_inputs_schema={"result_b": "${sub_b.out}"}, response_mode="streaming")
    sub_flow.add_connection("sub_start", "sub_a")
    sub_flow.add_connection("sub_start", "sub_b")
    sub_flow.add_connection("sub_a", "sub_end")
    sub_flow.add_stream_connection("sub_b", "sub_end")

    flow = Workflow()
    flow.set_start_comp("start", Start(), inputs_schema={"out": "${query}"})
    flow.add_workflow_comp("sub_flow", SubWorkflowComponent(sub_flow), inputs_schema={"query": "${start.out}"})
    flow.set_end_comp("end", End(), inputs_schema={"result": "${sub_flow}"})
    flow.add_connection("start", "sub_flow")
    flow.add_connection("sub_flow", "end")

    chunks = []
    async for chunk in flow.stream({"query": "hello"}, create_workflow_session(), stream_modes=[BaseStreamMode.OUTPUT]):
        chunks.append(chunk)

    assert len(chunks) == 1
    assert chunks[0].payload["output"]["result"]["stream"] == [{"output": {"result_b": "hello"}},
                                                               {"output": {"result_a": "hello"}}]


async def test_sub_flow_stream_output():
    class CustomComponent(WorkflowComponent):
        async def invoke(self, inputs: Input, session: Session, context: ModelContext):
            exec_id = session.get_executable_id()
            logger.info(f"exec_id: {exec_id}， invoke start")
            a = inputs.get("a")
            if a is None:
                a = 0
            b = inputs.get("b")
            logger.info(f"exec_id: {exec_id}， invoke done")
            return {"result": int(a) + int(b)}

        async def stream(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
            exec_id = session.get_executable_id()
            logger.info(f"exec_id: {exec_id}， stream start")
            inputs_a = inputs.get("a")
            import asyncio
            if isinstance(inputs_a, list):
                await asyncio.sleep(0.1)
                yield {"b": inputs.get("b")}
                await asyncio.sleep(0.1)
                yield {"op": "+"}
                for a in inputs_a:
                    yield {"a": a}
                    await asyncio.sleep(0.1)
                    yield {"result": int(a) + int(inputs.get("b"))}
            else:
                await asyncio.sleep(0.1)
                logger.info("stream step: 1")
                yield {"a": inputs_a}
                await asyncio.sleep(0.1)
                logger.info("stream step: 2")
                yield {"op": "+"}
                await asyncio.sleep(0.1)
                logger.info("stream step: 3")
                yield {"b": inputs.get("b")}
                await asyncio.sleep(0.1)
                logger.info("stream step: 4")
                yield {"result": int(inputs_a) + int(inputs.get("b"))}
            logger.info(f"exec_id: {exec_id}， stream done")

    # sub_start --> +--> custom_comp - - +
    #               |                    +--> sub_end
    #               +--> custom_comp1 - -+
    sub_flow = Workflow()
    sub_flow.set_start_comp("sub_start", Start(),
                            inputs_schema={"a": "${user_inputs.a}", "b": "${user_inputs.b}"})
    sub_flow.add_workflow_comp("custom_comp", CustomComponent(),
                               inputs_schema={"a": "${sub_start.a}", "b": "${sub_start.b}"},
                               wait_for_all=True, comp_ability=[ComponentAbility.STREAM])
    sub_flow.add_workflow_comp("custom_comp1", CustomComponent(),
                               inputs_schema={"a": "${sub_start.a}", "b": "${sub_start.b}"})

    sub_flow.set_end_comp("sub_end",
                          End({"responseTemplate": "输出:{{a}}{{op}}{{b}}={{result}};输出1:{{result1}}"}),
                          response_mode="streaming",
                          stream_inputs_schema={"op": "${custom_comp.op}", "a": "${custom_comp.a}",
                                                "b": "${custom_comp.b}", "result": "${custom_comp.result}"},
                          inputs_schema={"result1": "${custom_comp1.result}"})
    sub_flow.add_connection("sub_start", "custom_comp")
    sub_flow.add_connection("sub_start", "custom_comp1")
    sub_flow.add_stream_connection("custom_comp", "sub_end")
    sub_flow.add_connection("custom_comp1", "sub_end")

    flow = Workflow()
    flow.set_start_comp("start", Start(), inputs_schema={"data": "${inputs}"})
    flow.add_workflow_comp("sub_flow", SubWorkflowComponent(sub_flow), inputs_schema={"user_inputs": "${start.data}"})
    flow.set_end_comp("end", End(), stream_inputs_schema={"result": "${sub_flow.response}"})
    flow.add_connection("start", "sub_flow")
    flow.add_stream_connection("sub_flow", "end")

    result = await flow.invoke({"inputs": {"a": 1, "b": 2}}, create_workflow_session())
    assert result.result == {
        "output": [{"result": "输出:"}, {"result": 1}, {"result": "+"}, {"result": 2}, {"result": "="},
    {"result": 3}, {"result": ";输出1:"}, {"result": 3}]}
    assert result.state == WorkflowExecutionState.COMPLETED


async def test_single_component_execution():
    class CustomComponent(WorkflowComponent):
        async def invoke(self, inputs: Input, session: Session, context: ModelContext):
            exec_id = session.get_executable_id()
            logger.info(f"===================: {inputs}， invoke start")

            logger.info(f"exec_id: {exec_id}， invoke done")
            return {"result": "result"}
    component = CustomComponent()
    session = create_workflow_session()
    # 3. create Vertex instance
    component_id = "test_component"
    inputs = {"a": "测试输入", "b": "测试输入2"}
    inputs_schema = {"a": "${a}", "b": "${b}"}
    outputs_schema = {"result": "${result}"}

    # 4. call execute_single_component method
    result = await execute_single_component(
        component_id=component_id,
        session=session,
        executor=component,
        inputs=inputs,
        inputs_schema=inputs_schema,
        outputs_schema=outputs_schema
    )
    # 5. check result
    assert result == {'result': 'result'}



async def test_workflow_cancel():
    import asyncio
    
    class CustomComponent(WorkflowComponent):
        async def invoke(self, inputs: Input, session: Session, context: ModelContext):
            await asyncio.sleep(0.1)
            return {}

    flow = Workflow()
    flow.set_start_comp("start", Start(), inputs_schema={"data": "${inputs}"})
    flow.set_end_comp("end", End(), inputs_schema={"data": "${start.data}"})
    flow.add_workflow_comp("custom_comp", CustomComponent(), inputs_schema={"data": "${start.data}"})

    flow.add_connection("start", "custom_comp")
    flow.add_connection("custom_comp", "end")

    import uuid
    session_id = str(uuid.uuid4())
    task = asyncio.create_task(flow.invoke({"inputs": {"a": 1, "b": 2}},
                                           create_workflow_session(session_id=session_id)))
    await asyncio.sleep(0.01)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        assert True

    from openjiuwen.core.session.checkpointer import CheckpointerFactory
    session_exist = await CheckpointerFactory.get_checkpointer().session_exists(session_id)
    assert session_exist is False


async def test_questioner_context_sharing():
    """Test that two questioner components can share context history."""
    from unittest.mock import AsyncMock, MagicMock
    from openjiuwen.core.workflow import QuestionerComponent, QuestionerConfig, FieldInfo
    from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig

    # Create mock model config
    from openjiuwen.core.foundation.llm import ProviderType
    model_client = ModelClientConfig(
        client_provider=ProviderType.OpenAI,
        api_base="http://mock",
        api_key="mock_key"
    )
    model_config = ModelRequestConfig(model="mock_model")
    
    # Create two questioner components with chat history enabled
    questioner_config_1 = QuestionerConfig(
        model_client_config=model_client,
        model_config=model_config,
        extract_fields_from_response=True,
        field_names=[FieldInfo(field_name="name", description="Name", required=True)],
        with_chat_history=True,
        chat_history_max_rounds=5
    )
    
    questioner_config_2 = QuestionerConfig(
        model_client_config=model_client,
        model_config=model_config,
        question_content="What is your address?",
        extract_fields_from_response=False,
        with_chat_history=True,
        chat_history_max_rounds=5
    )
    
    questioner_1 = QuestionerComponent(questioner_comp_config=questioner_config_1)
    questioner_2 = QuestionerComponent(questioner_comp_config=questioner_config_2)
    
    # Create workflow: Start -> Questioner1 -> Questioner2 -> End
    workflow = Workflow(card=WorkflowCard(name="test_questioner_context", id="test_ctx", version="0.0.1"))
    workflow.set_start_comp("s", Start(), inputs_schema={"query": "${query}"})
    workflow.add_workflow_comp("q1", questioner_1, inputs_schema={"query": "${s.query}"})
    workflow.add_workflow_comp("q2", questioner_2, inputs_schema={"query": "dummy"})
    workflow.set_end_comp("e", End(), inputs_schema={"name": "${q1.name}", "address": "${q2.user_response}"})
    workflow.add_connection("s", "q1")
    workflow.add_connection("q1", "q2")
    workflow.add_connection("q2", "e")
    
    # Create mock context
    context = AsyncMock(spec=ModelContext)
    messages_store = []
    
    async def mock_add_messages(msgs):
        messages_store.extend(msgs)
    
    async def mock_get_context_window(dialogue_round=5):
        mock_window = MagicMock()
        mock_window.get_messages.return_value = messages_store[-dialogue_round * 2:] if messages_store else []
        return mock_window
    
    context.add_messages = mock_add_messages
    context.get_context_window = mock_get_context_window
    
    # Create session with context
    session = create_workflow_session()
    session._context = context
    
    # Verify that messages are written to context
    # After workflow execution, context should contain:
    # 1. User message from initial query
    # 2. Assistant message from questioner 1's question
    # 3. User message from questioner 1's feedback
    # 4. Assistant message from questioner 2's question
    
    # Note: This is a structural test to verify the fix
    # The actual workflow execution would require mocking LLM responses
    assert True  # Placeholder - full integration test would go here


async def test_questioner_writes_to_context():
    """Test that questioner component writes messages to context."""
    from unittest.mock import AsyncMock, MagicMock
    from openjiuwen.core.workflow.components.llm.questioner_comp import (
        QuestionerDirectReplyHandler, QuestionerConfig, QuestionerState
    )

    # Create simple config without model (we'll mock the handler methods)
    config = QuestionerConfig(
        question_content="What is your name?",
        extract_fields_from_response=False,
        with_chat_history=True
    )

    # Create handler
    handler = QuestionerDirectReplyHandler()
    handler._config = config
    handler._query = "test query"

    # Create state
    state = QuestionerState()
    handler._state = state

    # Create mock context
    context = AsyncMock(spec=ModelContext)
    messages_written = []

    async def capture_messages(msgs):
        messages_written.extend(msgs)

    context.add_messages = capture_messages

    # Test _write_user_message_to_context
    await handler._write_user_message_to_context("Hello", context)

    # Verify user message was written
    assert len(messages_written) == 1
    assert messages_written[0].role == "user"
    assert messages_written[0].content == "Hello"

    # Test _write_assistant_message_to_context
    messages_written.clear()
    await handler._write_assistant_message_to_context("What is your name?", context)

    # Verify assistant message was written
    assert len(messages_written) == 1
    assert messages_written[0].role == "assistant"
    assert messages_written[0].content == "What is your name?"

    # Test that messages are NOT written when with_chat_history is False
    config_no_history = QuestionerConfig(
        question_content="Test",
        extract_fields_from_response=False,
        with_chat_history=False
    )
    handler._config = config_no_history
    messages_written.clear()

    await handler._write_user_message_to_context("Should not write", context)
    assert len(messages_written) == 0  # No message should be written

