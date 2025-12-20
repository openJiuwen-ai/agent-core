#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import unittest
from collections.abc import Callable

import pytest

from jiuwen.core.common.exception.exception import JiuWenBaseException
from jiuwen.core.component.branch_comp import BranchComponent
from jiuwen.core.component.branch_router import BranchRouter
from jiuwen.core.component.break_comp import BreakComponent
from jiuwen.core.component.condition.array import ArrayCondition
from jiuwen.core.component.condition.number import NumberCondition
from jiuwen.core.component.end_comp import End
from jiuwen.core.component.loop_callback.intermediate_loop_var import IntermediateLoopVarCallback
from jiuwen.core.component.loop_callback.output import OutputCallback
from jiuwen.core.component.loop_comp import LoopGroup, AdvancedLoopComponent, LoopComponent
from jiuwen.core.component.set_variable_comp import SetVariableComponent
from jiuwen.core.component.start_comp import Start
from jiuwen.core.component.workflow_comp import SubWorkflowComponent
from jiuwen.core.graph.executable import Input
from jiuwen.core.runtime.runtime import BaseRuntime, Runtime
from jiuwen.core.runtime.state import ReadableStateLike
from jiuwen.core.runtime.workflow import WorkflowRuntime
from jiuwen.core.stream.base import BaseStreamMode, CustomSchema
from jiuwen.core.workflow.base import Workflow, WorkflowExecutionState, WorkflowOutput
from jiuwen.core.workflow.workflow_config import ComponentAbility
from tests.unit_tests.workflow.test_mock_node import SlowNode, CountNode, StreamCompNode, CollectCompNode, \
    TransformCompNode, MockStartNode, MockEndNode, Node1, StreamNode
from tests.unit_tests.workflow.test_node import AddTenNode, CommonNode

pytestmark = pytest.mark.asyncio


async def test_workflow_with_loop_number_condition():
    flow = await create_workflow()

    result = await flow.invoke({"input_number": 1, "loop_number": 3}, WorkflowRuntime())
    assert result == WorkflowOutput(result={"array_result": [10, 11, 12], "user_var": 31},
                                    state=WorkflowExecutionState.COMPLETED)

    result = await flow.invoke({"input_number": 2, "loop_number": 2}, WorkflowRuntime())
    assert result == WorkflowOutput(result={"array_result": [10, 11], "user_var": 22},
                                    state=WorkflowExecutionState.COMPLETED)
    flow = await create_workflow()
    result = await flow.invoke({"input_number": 2, "loop_number": 2}, WorkflowRuntime())
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
    set_variable_component = SetVariableComponent({"${l.intermediate_loop_var.user_var}": "${2.result}"})
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


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def invoke_workflow(self, inputs: Input, runtime: BaseRuntime, flow: Workflow):
        feature = asyncio.ensure_future(flow.invoke(inputs=inputs, runtime=runtime))
        self.loop.run_until_complete(feature)
        return feature.result()

    def assert_workflow_invoke(self, inputs: dict, runtime: BaseRuntime, flow: Workflow, expect_results: dict = None,
                               checker: Callable = None):
        if expect_results is not None:
            assert (self.invoke_workflow(inputs, runtime, flow) ==
                    WorkflowOutput(result=expect_results, state=WorkflowExecutionState.COMPLETED))
        elif checker is not None:
            checker(self.invoke_workflow(inputs, runtime, flow))

    def test_start_comp(self):
        flow = Workflow()
        with self.assertRaises(JiuWenBaseException):
            Start(conf = {"inputs": [{"required": True}]})

        conf = {"inputs": [
            {"id": "query", "required": True},
            {"id": "param1", "required": False},
            {"id": "param2", "required": False, "default_value": False}
        ]}

        flow.set_start_comp("s", Start(conf=conf), inputs_schema={"query": "${user_inputs.query}"})
        flow.set_end_comp("e", Start(),
                          inputs_schema={"query": "${s.query}", "param1": "${s.param1}", "param2": "${s.param2}"})

        flow.add_connection("s", "e")
        with self.assertRaises(JiuWenBaseException) as e:
            self.invoke_workflow(inputs={"user_inputs": {}}, runtime=WorkflowRuntime(), flow=flow)

        result = self.invoke_workflow(inputs={"user_inputs": {"query": "hello"}}, runtime=WorkflowRuntime(), flow=flow)
        assert result.result == {"query": "hello", "param1": None, "param2": False}

    def test_simple_workflow(self):
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
        self.assert_workflow_invoke({"a": 1, "b": "haha"}, WorkflowRuntime(), flow, expect_results={"result": 1})

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
        self.assert_workflow_invoke({"a1": 1, "a2": 2}, WorkflowRuntime(), flow2, expect_results={"b1": 1, "b2": 2})

    def test_simple_workflow_with_condition(self):
        """
        start -> condition[a,b] -> end
        """
        flow = Workflow()
        flow.set_start_comp("start", MockStartNode("start"),
                            inputs_schema={"a": "${a}",
                                           "b": "${b}",
                                           "c": 1,
                                           "d": [1, 2, 3]})

        def router(runtime: Runtime):
            val = runtime.get_global_state("start.a")
            if val is not None:
                return "a"
            val = runtime.get_global_state("start.b")
            if val is not None:
                return "b"
            return "a"

        flow.add_conditional_connection("start", router=router)
        flow.add_workflow_comp("a", Node1("a"), inputs_schema={"a": "${start.a}", "b": "${start.c}"})
        flow.add_workflow_comp("b", Node1("b"), inputs_schema={"b": "${start.b}"})
        flow.set_end_comp("end", MockEndNode("end"), {"result1": "${a.a}", "result2": "${b.b}"})
        flow.add_connection("a", "end")
        flow.add_connection("b", "end")
        self.assert_workflow_invoke({"a": 1}, WorkflowRuntime(), flow,
                                    expect_results={"result1": 1, "result2": None})
        self.assert_workflow_invoke({"b": "haha"}, WorkflowRuntime(), flow,
                                    expect_results={"result1": None, "result2": "haha"})

    def test_simple_workflow_with_branch_condition(self):
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
        self.assert_workflow_invoke({"a": 1}, WorkflowRuntime(), flow,
                                    expect_results={"result1": 1, "result2": None})
        self.assert_workflow_invoke({"b": "haha"}, WorkflowRuntime(), flow,
                                    expect_results={"result1": None, "result2": "haha"})

    def test_workflow_with_wait_for_all(self):
        # flow: start -> (a->a1)|b|c|d -> collect -> end
        for waitForAll in [True, False]:
            flow = Workflow()

            def start_input_transformer(state: ReadableStateLike):
                start_input_schema = {"a": "${a}", "b": "${b}", "c": "${c}",
                                      "d": "${d}"}
                return state.get(start_input_schema)

            flow.set_start_comp("start", MockStartNode("start"), inputs_transformer=start_input_transformer)
            flow.add_workflow_comp("a", Node1("a"), inputs_schema={"a": "${start.a}"})
            flow.add_workflow_comp("a1", SlowNode("a1", 1), inputs_schema={"a": "${a.a}"})
            flow.add_workflow_comp("b", Node1("b"), inputs_schema={"b": "${start.b}"})
            flow.add_workflow_comp("c", Node1("c"), inputs_schema={"c": "${start.c}"})
            flow.add_workflow_comp("d", Node1("d"), inputs_schema={"d": "${start.d}"})
            flow.add_workflow_comp("collect", CountNode("collect"), wait_for_all=waitForAll)
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
            if waitForAll:
                self.assert_workflow_invoke({"a": 1, "b": 2, "c": 3, "d": 4}, WorkflowRuntime(), flow,
                                            expect_results={"result": 1})
            else:
                self.assert_workflow_invoke({"a": 1, "b": 2, "c": 3, "d": 4}, WorkflowRuntime(), flow,
                                            expect_results={"result": 2})

    def test_workflow_with_branch(self):
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

        result = self.invoke_workflow({"a": 2}, WorkflowRuntime(), flow)
        assert result.result["b"] == 12

        result = self.invoke_workflow({"a": 15}, WorkflowRuntime(), flow)
        assert result.result["a"] == 15

    def test_workflow_with_loop(self):
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
        set_variable_component = SetVariableComponent({"${l.user_var}": "${2.result}"})
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

        result = self.invoke_workflow({"input_array": [1, 2, 3], "input_number": 1}, WorkflowRuntime(), flow)
        assert result == WorkflowOutput(result={"array_result": [11, 12, 13], "user_var": 31, "index": [0, 1, 2]},
                                        state=WorkflowExecutionState.COMPLETED)

        result = self.invoke_workflow({"input_array": [4, 5], "input_number": 2}, WorkflowRuntime(), flow)
        assert result == WorkflowOutput(result={"array_result": [14, 15], "user_var": 22, "index": [0, 1]},
                                        state=WorkflowExecutionState.COMPLETED)

    def test_workflow_with_loop_component(self):
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
        set_variable_component = SetVariableComponent({"${l.user_var}": "${2.result}"})
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

        result = self.invoke_workflow({"input_array": [1, 2, 3], "input_number": 1}, WorkflowRuntime(), flow)
        assert result == WorkflowOutput(result={"array_result": [11, 12, 13], "user_var": 31, "index": [0, 1, 2]},
                                        state=WorkflowExecutionState.COMPLETED)

        result = self.invoke_workflow({"input_array": [4, 5], "input_number": 2}, WorkflowRuntime(), flow)
        assert result == WorkflowOutput(result={"array_result": [14, 15], "user_var": 22, "index": [0, 1]},
                                        state=WorkflowExecutionState.COMPLETED)

    def test_workflow_with_loop_component_number_condition(self):
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
        set_variable_component = SetVariableComponent({"${l.user_var}": "${2.result}"})
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

        result = self.invoke_workflow({"input_number": 2, "loop_number": 2}, WorkflowRuntime(), flow)
        assert result == WorkflowOutput(result={"array_result": [10, 11], "user_var": 22},
                                        state=WorkflowExecutionState.COMPLETED)

        result = self.invoke_workflow({"input_number": 1, "loop_number": 3}, WorkflowRuntime(), flow)
        assert result == WorkflowOutput(result={"array_result": [10, 11, 12], "user_var": 31},
                                        state=WorkflowExecutionState.COMPLETED)

    def test_workflow_with_loop_component_expression_condition(self):
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
        set_variable_component = SetVariableComponent({"${l.user_var}": "${2.result}"})
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

        result = self.invoke_workflow({"input_number": 2, "loop_number": 2}, WorkflowRuntime(), flow)
        assert result == WorkflowOutput(result={"array_result": [10, 11], "user_var": 22},
                                        state=WorkflowExecutionState.COMPLETED)

        result = self.invoke_workflow({"input_number": 1, "loop_number": 3}, WorkflowRuntime(), flow)
        assert result == WorkflowOutput(result={"array_result": [10, 11, 12], "user_var": 31},
                                        state=WorkflowExecutionState.COMPLETED)

    def test_workflow_with_loop_component_always_true(self):
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
        set_variable_component = SetVariableComponent({"${l.user_var}": "${2.result}"})
        loop_group.add_workflow_comp("3", set_variable_component)

        sw = BranchComponent()
        sw.add_branch("${l.index} >= ${loop_number} - 1", ["4"], "1")
        sw.add_branch("${l.index} < ${loop_number} - 1", ["5"], "2")

        loop_group.add_workflow_comp("sw", sw)

        break_node = BreakComponent()
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

        result = self.invoke_workflow({"input_number": 2, "loop_number": 2}, WorkflowRuntime(), flow)
        assert result == WorkflowOutput(result={"array_result": [10, 11], "user_var": 22},
                                        state=WorkflowExecutionState.COMPLETED)

        result = self.invoke_workflow({"input_number": 1, "loop_number": 3}, WorkflowRuntime(), flow)
        assert result == WorkflowOutput(result={"array_result": [10, 11, 12], "user_var": 31},
                                        state=WorkflowExecutionState.COMPLETED)

    def test_workflow_with_loop_component_break(self):
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
        set_variable_component = SetVariableComponent({"${l.user_var}": "${2.result}"})
        loop_group.add_workflow_comp("3", set_variable_component)
        break_node = BreakComponent()
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

        result = self.invoke_workflow({"input_array": [1, 2, 3], "input_number": 1}, WorkflowRuntime(), flow)
        assert result == WorkflowOutput(result={"array_result": [11], "user_var": 11},
                                        state=WorkflowExecutionState.COMPLETED)

        result = self.invoke_workflow({"input_array": [4, 5], "input_number": 2}, WorkflowRuntime(), flow)
        assert result == WorkflowOutput(result={"array_result": [14], "user_var": 12},
                                        state=WorkflowExecutionState.COMPLETED)

    def test_workflow_with_loop_break(self):
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
        set_variable_component = SetVariableComponent({"${l.user_var}": "${2.result}"})
        loop_group.add_workflow_comp("3", set_variable_component)
        break_node = BreakComponent()
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

        result = self.invoke_workflow({"input_array": [1, 2, 3], "input_number": 1}, WorkflowRuntime(), flow)
        assert result == WorkflowOutput(result={"array_result": [11], "user_var": 11},
                                        state=WorkflowExecutionState.COMPLETED)

        result = self.invoke_workflow({"input_array": [4, 5], "input_number": 2}, WorkflowRuntime(), flow)
        assert result == WorkflowOutput(result={"array_result": [14], "user_var": 12},
                                        state=WorkflowExecutionState.COMPLETED)

    def test_simple_stream_workflow(self):
        async def stream_workflow():
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
            async for chunk in flow.stream({"a": 1, "b": "haha"}, WorkflowRuntime()):
                if not isinstance(chunk, CustomSchema):
                    continue
                assert chunk == expected_datas_model[index], f"Mismatch at index {index}"
                index += 1

        self.loop.run_until_complete(stream_workflow())

    def test_seq_exec_stream_workflow(self):
        async def stream_workflow():
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
            async for chunk in flow.stream({"a": 1, "b": "haha"}, WorkflowRuntime()):
                if not isinstance(chunk, CustomSchema):
                    continue
                node_id = chunk.node_id
                index = index_dict[node_id]
                assert chunk == expected_datas_model[node_id][index], f"Mismatch at node {node_id} index {index}"
                index_dict[node_id] = index_dict[node_id] + 1

        self.loop.run_until_complete(stream_workflow())

    def test_parallel_exec_stream_workflow(self):
        async def stream_workflow():
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
            async for chunk in flow.stream({"a": 1, "b": "haha"}, WorkflowRuntime()):
                if not isinstance(chunk, CustomSchema):
                    continue
                node_id = chunk.node_id
                index = index_dict[node_id]
                assert chunk == expected_datas_model[node_id][index], f"Mismatch at node {node_id} index {index}"
                index_dict[node_id] = index_dict[node_id] + 1

        self.loop.run_until_complete(stream_workflow())

    def test_sub_stream_workflow(self):
        async def stream_workflow():
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
            async for chunk in main_workflow.stream({"a": 1, "b": "haha"}, WorkflowRuntime(),
                                                    stream_modes=[BaseStreamMode.CUSTOM]):
                if isinstance(chunk, CustomSchema):
                    assert chunk == expected_datas_model[index], f"Mismatch at index {index}"
                    index += 1

        self.loop.run_until_complete(stream_workflow())

    def test_nested_workflow(self):
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
        self.assert_workflow_invoke({"a1": 1, "a2": 2}, WorkflowRuntime(), flow1, expect_results={"b1": 1, "b2": 2})

    def test_nested_workflow_same_node_id(self):
        flow1 = Workflow()
        flow1.set_start_comp("start", Start({}),
                             inputs_schema={
                                 "a": "${a1}",
                                 "b": "${a2}"})

        # start2->a2->end2
        flow2 = Workflow()
        flow2.set_start_comp("start", Start({}), inputs_schema={"a1": "${input}"})
        flow2.add_workflow_comp("a1", Node1("a1"), inputs_schema={"value": "${start.a1}"})
        flow2.set_end_comp("end", End({}), inputs_schema={"result": "${a1.value}"})
        flow2.add_connection("start", "a1")
        flow2.add_connection("a1", "end")

        # flow2: start->composite->a1->end
        flow1.add_workflow_comp("composite", SubWorkflowComponent(flow2),
                                inputs_schema={"input": "${start.b}"})
        flow1.add_workflow_comp("a1", Node1("a1"), inputs_schema={"value_different": "${start.a}",
                                                                  "value_different_result": "${composite.result}"})

        flow1.set_end_comp("end", End({}), inputs_schema={"b1": "${a1.value_different}", "b2": "${composite.result}",
                                                          "b3": "${a1.value_different_result}"})

        flow1.add_connection("start", "composite")
        flow1.add_connection("composite", "a1")
        flow1.add_connection("a1", "end")
        self.assert_workflow_invoke({"a1": 1, "a2": 2}, WorkflowRuntime(), flow1,
                                    expect_results={'responseContent': '', 'output': {'b1': 1, 'b2': 2, 'b3': 2}})

    def test_stream_comp_workflow(self):
        # start -> a ---> b -> end
        flow = Workflow()
        flow.set_start_comp("start", MockStartNode("start"), inputs_schema={"a": "${a}"})
        flow.add_workflow_comp("a", StreamCompNode("a"), inputs_schema={"value": "${start.a}"},
                               comp_ability=[ComponentAbility.STREAM], wait_for_all=True)
        flow.add_workflow_comp("b", CollectCompNode("b"), inputs_schema={"value": "${a.value}"},
                               stream_inputs_schema={"value": "${a.value}"}, comp_ability=[ComponentAbility.COLLECT],
                               wait_for_all=True)
        flow.set_end_comp("end", MockEndNode("end"), inputs_schema={"result1": "${b.value}"})
        flow.add_connection("start", "a")
        flow.add_stream_connection("a", "b")
        flow.add_connection("b", "end")
        idx = 1
        self.assert_workflow_invoke({"a": idx}, WorkflowRuntime(), flow,
                                    expect_results={"result1": idx * sum(range(1, 3))})

    def test_transform_workflow(self):
        # start -> a ---> b ---> c -> end
        flow = Workflow()
        flow.set_start_comp("start", MockStartNode("start"), inputs_schema={"a": "${a}"})
        # a: throw 2 frames: {value: 1}, {value: 2}
        flow.add_workflow_comp("a", StreamCompNode("a"), inputs_schema={"value": "${start.a}"},
                               comp_ability=[ComponentAbility.STREAM], wait_for_all=True)
        # b: transform 2 frames to c
        flow.add_workflow_comp("b", TransformCompNode("b"), inputs_schema={"value": "${a.value}"},
                               stream_inputs_schema={"value": "${a.value}"}, comp_ability=[ComponentAbility.TRANSFORM],
                               wait_for_all=True)
        # c: value = sum(value of frames)
        flow.add_workflow_comp("c", CollectCompNode("c"), inputs_schema={"value": "${b.value}"},
                               stream_inputs_schema={"value": "${b.value}"}, comp_ability=[ComponentAbility.COLLECT],
                               wait_for_all=True)
        flow.set_end_comp("end", MockEndNode("end"), inputs_schema={"result": "${c.value}"})
        flow.add_connection("start", "a")
        flow.add_stream_connection("a", "b")
        flow.add_stream_connection("b", "c")
        flow.add_connection("c", "end")

        self.assert_workflow_invoke({"a": 1}, WorkflowRuntime(), flow, expect_results={"result": 3})

    def test_five_transform_workflow(self):
        # start -> a ---> b ---> c ---> d ---> e ---> f ---> g -> end
        flow = Workflow()
        flow.set_start_comp("start", MockStartNode("start"), inputs_schema={"a": "${a}"})
        # a: throw 2 frames: {value: 1}, {value: 2}
        flow.add_workflow_comp("a", StreamCompNode("a"), inputs_schema={"value": "${start.a}"},
                               comp_ability=[ComponentAbility.STREAM], wait_for_all=True)
        # b: transform frame to c
        flow.add_workflow_comp("b", TransformCompNode("b"), inputs_schema={"value": "${a.value}"},
                               stream_inputs_schema={"value": "${a.value}"}, comp_ability=[ComponentAbility.TRANSFORM],
                               wait_for_all=True)
        # c: transform frame to d
        flow.add_workflow_comp("c", TransformCompNode("c"), inputs_schema={"value": "${b.value}"},
                               stream_inputs_schema={"value": "${b.value}"}, comp_ability=[ComponentAbility.TRANSFORM],
                               wait_for_all=True)
        # d: transform frame to e
        flow.add_workflow_comp("d", TransformCompNode("d"), inputs_schema={"value": "${c.value}"},
                               stream_inputs_schema={"value": "${c.value}"}, comp_ability=[ComponentAbility.TRANSFORM],
                               wait_for_all=True)
        # e: transform frame to f
        flow.add_workflow_comp("e", TransformCompNode("e"), inputs_schema={"value": "${d.value}"},
                               stream_inputs_schema={"value": "${d.value}"}, comp_ability=[ComponentAbility.TRANSFORM],
                               wait_for_all=True)
        # f: transform frame to g
        flow.add_workflow_comp("f", TransformCompNode("f"), inputs_schema={"value": "${e.value}"},
                               stream_inputs_schema={"value": "${e.value}"}, comp_ability=[ComponentAbility.TRANSFORM],
                               wait_for_all=True)
        # g: collect all frames
        flow.add_workflow_comp("g", CollectCompNode("g"), inputs_schema={"value": "${f.value}"},
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

        self.assert_workflow_invoke({"a": 1}, WorkflowRuntime(), flow, expect_results={"result": 3})
