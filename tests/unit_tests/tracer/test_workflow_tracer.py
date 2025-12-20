import json
import sys
import types
from unittest.mock import Mock

from jiuwen.core.component.condition.array import ArrayCondition
from jiuwen.core.component.loop_callback.intermediate_loop_var import IntermediateLoopVarCallback
from jiuwen.core.component.loop_callback.output import OutputCallback
from jiuwen.core.component.loop_comp import LoopGroup, AdvancedLoopComponent
from jiuwen.core.component.set_variable_comp import SetVariableComponent
from jiuwen.core.component.workflow_comp import SubWorkflowComponent
from tests.unit_tests.workflow.test_node import CommonNode, AddTenNode

fake_base = types.ModuleType("base")
fake_base.logger = Mock()

fake_exception_module = types.ModuleType("base")
fake_exception_module.JiuWenBaseException = Mock()

sys.modules["jiuwen.core.common.logging.base"] = fake_base
sys.modules["jiuwen.core.common.exception.base"] = fake_exception_module

from tests.unit_tests.tracer.test_mock_node_with_tracer import StreamNodeWithTracer
from jiuwen.core.common.logging import logger

import asyncio
import unittest
from collections.abc import Callable

from jiuwen.core.runtime.runtime import BaseRuntime
from jiuwen.core.runtime.workflow import WorkflowRuntime
from jiuwen.core.workflow.base import Workflow
from jiuwen.core.workflow.workflow_config import WorkflowConfig
from jiuwen.core.stream.base import CustomSchema, OutputSchema, TraceSchema
from jiuwen.graph.pregel.graph import PregelGraph
from tests.unit_tests.workflow.test_mock_node import MockStartNode, MockEndNode

switcher = False


def record_tracer_info(tracer_chunks, file_path):
    if not switcher:
        return
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            for chunk in tracer_chunks:
                json_data = json.dumps(chunk.model_dump(), default=str, ensure_ascii=False)
                f.write(json_data + "\n")
        print(f"调测信息已保存到文件：{file_path}")
    except Exception as e:
        print(f"调测信息保存失败：{e}")


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def invoke_workflow(self, inputs: dict, runtime: BaseRuntime, flow: Workflow):
        feature = asyncio.ensure_future(flow.invoke(inputs=inputs, runtime=runtime))
        self.loop.run_until_complete(feature)
        return feature.result()

    def assert_workflow_invoke(self, inputs: dict, runtime: BaseRuntime, flow: Workflow, expect_results: dict = None,
                               checker: Callable = None):
        if expect_results is not None:
            assert self.invoke_workflow(inputs, runtime, flow) == expect_results
        elif checker is not None:
            checker(self.invoke_workflow(inputs, runtime, flow))

    def test_seq_exec_stream_workflow_with_tracer(self):
        """
        start -> a -> b -> end
        """
        tracer_chunks = []

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
            flow.add_workflow_comp("a", StreamNodeWithTracer("a", node_a_expected_datas),
                                   inputs_schema={
                                       "aa": "${start.a}",
                                       "ac": "${start.c}"})

            node_b_expected_datas = [
                {"node_id": "b", "id": 1, "data": "1"},
                {"node_id": "b", "id": 2, "data": "2"},
            ]
            node_b_expected_datas_model = [CustomSchema(**item) for item in node_b_expected_datas]
            flow.add_workflow_comp("b", StreamNodeWithTracer("b", node_b_expected_datas),
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

            async for chunk in flow.stream({"a": 1, "b": "haha"},
                                           WorkflowRuntime()):
                if isinstance(chunk, CustomSchema):
                    node_id = chunk.node_id
                    index = index_dict[node_id]
                    assert chunk == expected_datas_model[node_id][index], f"Mismatch at node {node_id} index {index}"
                    logger.info(f"stream chunk: {chunk}")
                    index_dict[node_id] = index_dict[node_id] + 1
                elif isinstance(chunk, TraceSchema):
                    print(f"stream chunk: {chunk}")
                    tracer_chunks.append(chunk)

        self.loop.run_until_complete(stream_workflow())
        record_tracer_info(tracer_chunks, "test_seq_exec_stream_workflow_with_tracer.json")

    def test_parallel_exec_stream_workflow_with_tracer(self):
        """
        start -> a | b -> end
        """
        tracer_chunks = []

        async def stream_workflow():
            flow = Workflow()
            flow.set_start_comp("start", MockStartNode("start"),
                                inputs_schema={
                                    "a": "${user.inputs.a}",
                                    "b": "${user.inputs.b}",
                                    "c": 1,
                                    "d": [1, 2, 3]})

            node_a_expected_datas = [
                {"node_id": "a", "id": 1, "data": "1"},
                {"node_id": "a", "id": 2, "data": "2"},
            ]
            node_a_expected_datas_model = [CustomSchema(**item) for item in node_a_expected_datas]
            flow.add_workflow_comp("a", StreamNodeWithTracer("a", node_a_expected_datas),
                                   inputs_schema={
                                       "aa": "${start.a}",
                                       "ac": "${start.c}"})

            node_b_expected_datas = [
                {"node_id": "b", "id": 1, "data": "1"},
                {"node_id": "b", "id": 2, "data": "2"},
            ]
            node_b_expected_datas_model = [CustomSchema(**item) for item in node_b_expected_datas]
            flow.add_workflow_comp("b", StreamNodeWithTracer("b", node_b_expected_datas),
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
            async for chunk in flow.stream({"a": 1, "b": "haha"},
                                           WorkflowRuntime()):
                if isinstance(chunk, CustomSchema):
                    node_id = chunk.node_id
                    index = index_dict[node_id]
                    assert chunk == expected_datas_model[node_id][index], f"Mismatch at node {node_id} index {index}"
                    logger.info(f"stream chunk: {chunk}")
                    index_dict[node_id] = index_dict[node_id] + 1
                elif isinstance(chunk, TraceSchema):
                    print(f"stream chunk: {chunk}")
                    tracer_chunks.append(chunk)

        self.loop.run_until_complete(stream_workflow())
        record_tracer_info(tracer_chunks, "test_parallel_exec_stream_workflow_with_tracer.json")

    def test_sub_stream_workflow_with_tracer(self):
        """
        main_workflow: start -> a(sub_workflow) -> end
        sub_workflow: sub_start -> sub_a -> sub_end
        """
        tracer_chunks = []

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

            sub_workflow.add_workflow_comp("sub_a", StreamNodeWithTracer("a", expected_datas),
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
            async for chunk in main_workflow.stream({"a": 1, "b": "haha"},
                                                    WorkflowRuntime()):
                if not isinstance(chunk, (TraceSchema, OutputSchema)):
                    assert chunk == expected_datas_model[index], f"Mismatch at index {index}"
                    logger.info(f"stream chunk: {chunk}")
                    index += 1
                else:
                    print(f"stream chunk: {chunk}")
                    tracer_chunks.append(chunk)

        self.loop.run_until_complete(stream_workflow())
        record_tracer_info(tracer_chunks, "test_sub_stream_workflow_with_tracer.json")

    def test_nested_stream_workflow_with_tracer(self):
        """
        main_workflow: start -> a(sub_workflow) | b -> end
        sub_workflow: sub_start -> sub_a -> sub_end
        """
        tracer_chunks = []

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

            sub_workflow.add_workflow_comp("sub_a", StreamNodeWithTracer("a", expected_datas),
                                           inputs_schema={
                                               "aa": "${sub_start.a}",
                                               "ac": "${sub_start.c}"})
            sub_workflow.set_end_comp("sub_end", MockEndNode("end"),
                                      inputs_schema={
                                          "result": "${sub_a.aa}"})
            sub_workflow.add_connection("sub_start", "sub_a")
            sub_workflow.add_connection("sub_a", "sub_end")

            # main_workflow: start->a(sub workflow) | b ->end
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

            node_b_expected_datas = [
                {"node_id": "b", "id": 1, "data": "1"},
                {"node_id": "b", "id": 2, "data": "2"},
            ]
            node_b_expected_datas_model = [CustomSchema(**item) for item in node_b_expected_datas]
            main_workflow.add_workflow_comp("b", StreamNodeWithTracer("b", node_b_expected_datas),
                                            inputs_schema={
                                                "ba": "${start.b}",
                                                "bc": "${start.d}"})

            main_workflow.set_end_comp("end", MockEndNode("end"),
                                       inputs_schema={
                                           "result": "${a.aa}"})
            main_workflow.add_connection("start", "a")
            main_workflow.add_connection("a", "end")
            main_workflow.add_connection("start", "b")
            main_workflow.add_connection("b", "end")

            async for chunk in main_workflow.stream({"a": 1, "b": "haha"},
                                                    WorkflowRuntime()):
                if isinstance(chunk, TraceSchema):
                    print(f"stream chunk: {chunk}")
                    tracer_chunks.append(chunk)

        self.loop.run_until_complete(stream_workflow())
        for chunk in tracer_chunks:
            payload = chunk.payload
            payload.get("parentInvokeId")
            payload.get("parentNodeId")
            if payload.get("invokeId") == "start":
                assert payload.get("parentInvokeId") == None, f"start node parent_invoke_id should be None"
                assert payload.get("parentNodeId") == "", f"a node parent_node_id should be ''"
            elif payload.get("invokeId") == "a":
                assert payload.get("parentInvokeId") == "start", f"a node parent_invoke_id should be start"
                assert payload.get("parentNodeId") == "", f"a node parent_node_id should be ''"
            elif payload.get("invokeId") == "b":
                assert payload.get("parentInvokeId") == "a", f"b node parent_invoke_id should be a"
                assert payload.get("parentNodeId") == "", f"b node parent_node_id should be ''"
            elif payload.get("invokeId") == "end":
                assert payload.get("parentInvokeId") == "b", f"b node parent_invoke_id should be a"
                assert payload.get("parentNodeId") == "", f"b node parent_node_id should be ''"
            elif payload.get("invokeId") == "a.sub_start":
                assert payload.get("parentInvokeId") == None, f"sub_start node parent_invoke_id should be None"
                assert payload.get("parentNodeId") == "a", f"sub_start node parent_node_id should be a"
            elif payload.get("invokeId") == "a.sub_a":
                assert payload.get(
                    "parentInvokeId") == "a.sub_start", f"sub_a node parent_invoke_id should be sub_start"
                assert payload.get("parentNodeId") == "a", f"sub_a node parent_node_id should be a"
            elif payload.get("invokeId") == "a.sub_end":
                assert payload.get("parentInvokeId") == "a.sub_a", f"sub_end node parent_invoke_id should be sub_a"
                assert payload.get("parentNodeId") == "a", f"sub_end node parent_node_id should be a"
        record_tracer_info(tracer_chunks, "test_nested_stream_workflow_with_tracer.json")

    def test_nested_parallel_stream_workflow_with_tracer(self):
        """
        main_workflow: start -> a(sub_workflow) | b(sub_workflow) -> end
        sub_workflow: sub_start -> sub_a -> sub_end
        """
        tracer_chunks = []

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

            sub_workflow.add_workflow_comp("sub_a", StreamNodeWithTracer("a", expected_datas),
                                           inputs_schema={
                                               "aa": "${sub_start.a}",
                                               "ac": "${sub_start.c}"})
            sub_workflow.set_end_comp("sub_end", MockEndNode("end"),
                                      inputs_schema={
                                          "result": "${sub_a.aa}"})
            sub_workflow.add_connection("sub_start", "sub_a")
            sub_workflow.add_connection("sub_a", "sub_end")

            sub_workflow_2 = Workflow()
            sub_workflow_2.set_start_comp("sub_start", MockStartNode("start"),
                                          inputs_schema={
                                              "a": "${a}",
                                              "b": "${b}",
                                              "c": 1,
                                              "d": [1, 2, 3]})

            sub_workflow_2.add_workflow_comp("sub_a", StreamNodeWithTracer("a", expected_datas),
                                             inputs_schema={
                                                 "aa": "${sub2_start.a}",
                                                 "ac": "${sub2_start.c}"})
            sub_workflow_2.set_end_comp("sub_end", MockEndNode("end"),
                                        inputs_schema={
                                            "result": "${sub_a.aa}"})
            sub_workflow_2.add_connection("sub_start", "sub_a")
            sub_workflow_2.add_connection("sub_a", "sub_end")

            # main_workflow: start->a(sub workflow) | b(sub workflow) ->end
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

            node_b_expected_datas = [
                {"node_id": "b", "id": 1, "data": "1"},
                {"node_id": "b", "id": 2, "data": "2"},
            ]

            main_workflow.add_workflow_comp("b", SubWorkflowComponent(sub_workflow_2),
                                            inputs_schema={
                                                "aa": "${start.a}",
                                                "ac": "${start.c}"})

            main_workflow.set_end_comp("end", MockEndNode("end"),
                                       inputs_schema={
                                           "result": "${a.aa}"})
            main_workflow.add_connection("start", "a")
            main_workflow.add_connection("a", "end")
            main_workflow.add_connection("start", "b")
            main_workflow.add_connection("b", "end")

            async for chunk in main_workflow.stream({"a": 1, "b": "haha"},
                                                    WorkflowRuntime()):
                if isinstance(chunk, TraceSchema):
                    print(f"stream chunk: {chunk}")
                    tracer_chunks.append(chunk)

        self.loop.run_until_complete(stream_workflow())
        record_tracer_info(tracer_chunks, "test_nested_parallel_stream_workflow_with_tracer.json")

    def test_workflow_stream_with_loop_with_tracer(self):
        """
        s->a->loop(1->2->3)->b->e
        """
        tracer_chunks = []

        async def stream_workflow():
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
            loop_group.add_workflow_comp("2", AddTenNode("2"),
                                         inputs_schema={"source": "${l.user_var}"})
            loop_group.add_workflow_comp("3", SetVariableComponent(
                {"${l.user_var}": "${2.result}"}))
            loop_group.start_comp("1")
            loop_group.end_comp("3")
            loop_group.add_connection("1", "2")
            loop_group.add_connection("2", "3")
            output_callback = OutputCallback({"results": "${1.result}",
                                              "user_var": "${l.user_var}"})
            intermediate_callback = IntermediateLoopVarCallback({"user_var": "${input_number}"})

            loop = AdvancedLoopComponent(loop_group, ArrayCondition({"item": "${a.array}"}),
                                         callbacks=[output_callback, intermediate_callback])

            flow.add_workflow_comp("l", loop, inputs_schema={"input_number": "${input_number}"})

            # s->a->(1->2->3)->b->e
            flow.add_connection("s", "a")
            flow.add_connection("a", "l")
            flow.add_connection("l", "b")
            flow.add_connection("b", "e")

            async for chunk in flow.stream({"input_array": [1, 2, 3], "input_number": 1},
                                           WorkflowRuntime()):
                if isinstance(chunk, TraceSchema):
                    print(f"stream chunk: {chunk}")
                    tracer_chunks.append(chunk)

        self.loop.run_until_complete(stream_workflow())
        loop_index = 1
        for chunk in tracer_chunks:
            payload = chunk.payload
            if payload.get("invokeId") == "l":
                assert payload.get("parentInvokeId") == "a", f"l node parent_invoke_id should be a"
                assert payload.get("parentNodeId") == "", f"a node parent_node_id should be ''"
            elif payload.get("invokeId") == "3":
                assert payload.get("parentInvokeId") == "2", f"3 node parent_invoke_id should be start"
                assert payload.get("parentNodeId") == "", f"3 node parent_node_id should be ''"
                assert payload.get("loopNodeId") == "l", f"3 node parent_node_id should be l"
                if payload.get("status") == "finish":
                    assert payload.get("loopIndex") == loop_index, f"3 node loopIndex should be {loop_index}"
                    loop_index += 1
        record_tracer_info(tracer_chunks, "test_workflow_stream_with_loop_with_tracer.json")
