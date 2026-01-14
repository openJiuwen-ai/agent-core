import json
import sys
import types
from typing import AsyncIterator
from unittest.mock import Mock

import pytest

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.workflow import Input, Output, WorkflowCard
from openjiuwen.core.workflow import ArrayCondition
from openjiuwen.core.workflow import End
from openjiuwen.core.workflow import WorkflowComponent
from openjiuwen.core.workflow.components.flow_related.loop.loop_callback.intermediate_loop_var import IntermediateLoopVarCallback
from openjiuwen.core.workflow.components.flow_related.loop.loop_callback.output import OutputCallback
from openjiuwen.core.workflow import LoopGroup
from openjiuwen.core.workflow import SetVariableComponent
from openjiuwen.core.workflow import Start
from openjiuwen.core.workflow.components.flow_related.workflow_comp import SubWorkflowComponent
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.session import Session
from openjiuwen.core.workflow import ComponentAbility
from openjiuwen.core.workflow.components.flow_related.loop.loop_comp import AdvancedLoopComponent
from tests.unit_tests.core.workflow.mock_nodes import AddTenNode, CommonNode, MockStartNode, MockEndNode, StreamCompNode

fake_base = types.ModuleType("base")
fake_base.logger = Mock()

fake_exception_module = types.ModuleType("base")
fake_exception_module.JiuWenBaseException = Mock()

sys.modules["openjiuwen.core.common.logging.base"] = fake_base
sys.modules["openjiuwen.core.common.exception.base"] = fake_exception_module

from tests.unit_tests.core.session.tracer.mock_node_with_tracer import StreamNodeWithTracer
from openjiuwen.core.common.logging import logger

from openjiuwen.core.session.workflow import WorkflowSession
from openjiuwen.core.workflow import Workflow
from openjiuwen.core.session.stream import CustomSchema, OutputSchema, TraceSchema, BaseStreamMode

pytestmark = pytest.mark.asyncio

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


class Producer(WorkflowComponent):
    async def stream(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        logger.debug(f"producer inputs: {inputs}")
        for v in inputs.get("array"):
            logger.debug(f"send stream frame {v}")
            yield {"output": v}


class AnyTypeReturnNode(WorkflowComponent):
    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        return inputs.get("data")


class TestTraceWorkflow:
    async def test_any_type_trace(self):
        async def inner_test(inputs):
            workflow = Workflow(card=WorkflowCard(id="test"))
            workflow.set_start_comp("start", Start())
            workflow.add_workflow_comp("node", AnyTypeReturnNode(), inputs_schema={"data": "${inputs}"})
            workflow.set_end_comp("end", End(), inputs_schema={"output": "${node}"},
                                  response_mode="streaming")
            workflow.add_connection("start", "node")
            workflow.add_connection("node", "end")
            chunks = []
            async for chunk in workflow.stream(inputs={"inputs": inputs}, session=WorkflowSession(),
                                               stream_modes=[BaseStreamMode.TRACE]):
                chunks.append(chunk)
            assert chunks[-2].payload.get("streamOutputs") == [
                {'type': 'end node stream', 'index': 0, 'payload': {'output': {'output': inputs}}}]

        all_type_inputs = [[1, 2, 3], "abc", {'a': 1}, 1, 0.4, None]
        for item in all_type_inputs:
            await inner_test(item)

    async def test_stream_workflow_with_trace(self):
        workflow = Workflow(card=WorkflowCard(id="test"))
        workflow.set_start_comp("start", Start())
        workflow.add_workflow_comp("producer", Producer(), inputs_schema={"array": "${inputs}"})
        workflow.set_end_comp("end", End(), stream_inputs_schema={"output": "${producer.output}"},
                              response_mode="streaming")
        workflow.add_connection("start", "producer")
        workflow.add_stream_connection("producer", "end")
        expect_chunks = [{'invokeId': 'test', 'status': 'start', 'inputs': {'inputs': [1, 2, 3]}, 'streamInputs': None,
                          'outputs': None, 'streamOutputs': [], 'workflowId': 'test', 'componentId': None},
                         {'invokeId': 'start', 'status': 'start', 'inputs': None, 'streamInputs': None, 'outputs': None,
                          'streamOutputs': None, 'workflowId': 'test', 'componentId': 'start'},
                         {'invokeId': 'start', 'status': 'finish', 'inputs': None, 'streamInputs': None,
                          'outputs': None, 'streamOutputs': [], 'workflowId': 'test', 'componentId': 'start'},
                         {'invokeId': 'producer', 'status': 'start', 'inputs': {'array': [1, 2, 3]},
                          'streamInputs': None, 'outputs': None, 'streamOutputs': None, 'workflowId': 'test',
                          'componentId': 'producer'},
                         {'invokeId': 'producer', 'status': 'finish', 'inputs': {'array': [1, 2, 3]},
                          'streamInputs': None, 'outputs': None,
                          'streamOutputs': [{'output': 1}, {'output': 2}, {'output': 3}], 'workflowId': 'test',
                          'componentId': 'producer'}, {'invokeId': 'end', 'status': 'start', 'inputs': None,
                                                       'streamInputs': [{'output': 1}, {'output': 2}, {'output': 3}],
                                                       'outputs': None, 'streamOutputs': None, 'workflowId': 'test',
                                                       'componentId': 'end'},
                         {'invokeId': 'end', 'status': 'finish', 'inputs': None,
                          'streamInputs': [{'output': 1}, {'output': 2}, {'output': 3}], 'outputs': None,
                          'streamOutputs': [
                              {'type': 'end node stream', 'index': 0, 'payload': {'output': {'output': 1}}},
                              {'type': 'end node stream', 'index': 1, 'payload': {'output': {'output': 2}}},
                              {'type': 'end node stream', 'index': 2, 'payload': {'output': {'output': 3}}}],
                          'workflowId': 'test', 'componentId': 'end'},
                         {'invokeId': 'test', 'status': 'finish', 'inputs': {'inputs': [1, 2, 3]}, 'streamInputs': None,
                          'outputs': None, 'streamOutputs': [], 'workflowId': 'test', 'componentId': None}]

        chunks = []
        async for chunk in workflow.stream(inputs={"inputs": [1, 2, 3]}, session=WorkflowSession(),
                                           stream_modes=[BaseStreamMode.TRACE]):
            payload: dict = chunk.payload
            selected_keys = ["invokeId", "status", 'inputs', 'streamInputs', "outputs", "streamOutputs", "workflowId",
                             "componentId"]
            payload = {k: payload.get(k) for k in selected_keys}
            chunks.append(payload)
        assert chunks == expect_chunks

    async def test_seq_exec_stream_workflow_with_tracer(self):
        """
        start -> a -> b -> end
        """
        tracer_chunks = []

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
                                       WorkflowSession()):
            if isinstance(chunk, CustomSchema):
                node_id = chunk.node_id
                index = index_dict[node_id]
                assert chunk == expected_datas_model[node_id][index], f"Mismatch at node {node_id} index {index}"
                logger.info(f"stream chunk: {chunk}")
                index_dict[node_id] = index_dict[node_id] + 1
            elif isinstance(chunk, TraceSchema):
                print(f"stream chunk: {chunk}")
                tracer_chunks.append(chunk)

        record_tracer_info(tracer_chunks, "test_seq_exec_stream_workflow_with_tracer.json")

    async def test_parallel_exec_stream_workflow_with_tracer(self):
        """
        start -> a | b -> end
        """
        tracer_chunks = []

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
                                       WorkflowSession()):
            if isinstance(chunk, CustomSchema):
                node_id = chunk.node_id
                index = index_dict[node_id]
                assert chunk == expected_datas_model[node_id][index], f"Mismatch at node {node_id} index {index}"
                logger.info(f"stream chunk: {chunk}")
                index_dict[node_id] = index_dict[node_id] + 1
            elif isinstance(chunk, TraceSchema):
                print(f"stream chunk: {chunk}")
                tracer_chunks.append(chunk)
        record_tracer_info(tracer_chunks, "test_parallel_exec_stream_workflow_with_tracer.json")

    async def test_sub_stream_workflow_with_tracer(self):
        """
        main_workflow: start -> a(sub_workflow) -> end
        sub_workflow: sub_start -> sub_a -> sub_end
        """
        tracer_chunks = []

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
                                                WorkflowSession()):
            if not isinstance(chunk, (TraceSchema, OutputSchema)):
                assert chunk == expected_datas_model[index], f"Mismatch at index {index}"
                logger.info(f"stream chunk: {chunk}")
                index += 1
            else:
                print(f"stream chunk: {chunk}")
                tracer_chunks.append(chunk)
        record_tracer_info(tracer_chunks, "test_sub_stream_workflow_with_tracer.json")

    async def test_nested_stream_workflow_with_tracer(self):
        """
        main_workflow: start -> a(sub_workflow) | b -> end
        sub_workflow: sub_start -> sub_a -> sub_end
        """
        tracer_chunks = []

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
                                                WorkflowSession()):
            if isinstance(chunk, TraceSchema):
                print(f"stream chunk: {chunk}")
                tracer_chunks.append(chunk)

        for chunk in tracer_chunks:
            payload = chunk.payload
            payload.get("parentInvokeId")
            payload.get("parentNodeId")
            if payload.get("invokeId") == "start":
                assert payload.get("parentInvokeId") != None, f"start node parent_invoke_id should not be None"
                assert payload.get("parentNodeId") == "", f"a node parent_node_id should be ''"
            elif payload.get("invokeId") == "a":
                assert payload.get("parentInvokeId") in ("start", "b"), f"a node parent_invoke_id should be start or b"
                assert payload.get("parentNodeId") == "", f"a node parent_node_id should be ''"
            elif payload.get("invokeId") == "b":
                assert payload.get("parentInvokeId") in ("start", "a"), f"b node parent_invoke_id should be a or start"
                assert payload.get("parentNodeId") == "", f"b node parent_node_id should be ''"
            elif payload.get("invokeId") == "end":
                assert payload.get("parentInvokeId") in ("a", "b"), f"end node parent_invoke_id should be a or b"
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

    async def test_nested_parallel_stream_workflow_with_tracer(self):
        """
        main_workflow: start -> a(sub_workflow) | b(sub_workflow) -> end
        sub_workflow: sub_start -> sub_a -> sub_end
        """
        tracer_chunks = []

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
                                                WorkflowSession()):
            if isinstance(chunk, TraceSchema):
                print(f"stream chunk: {chunk}")
                tracer_chunks.append(chunk)
        record_tracer_info(tracer_chunks, "test_nested_parallel_stream_workflow_with_tracer.json")

    async def test_workflow_stream_with_loop_with_tracer(self):
        """
        s->a->loop(1->2->3)->b->e
        """
        tracer_chunks = []

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
                                       WorkflowSession()):
            if isinstance(chunk, TraceSchema):
                print(f"stream chunk: {chunk}")
                tracer_chunks.append(chunk)

        loop_index = 1
        for chunk in tracer_chunks:
            payload = chunk.payload
            assert payload.get("startTime") is not None
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

    async def test_workflow_strean_with_node_exception_with_tracer(self):
        flow = Workflow()
        start = Start({"inputs": [{"id": "query", "type": "String", "required": "true", "sourceType": "ref"}]})
        flow.set_start_comp("start", start,
                            inputs_schema={
                                "query": "${a}",
                                "response_node": "${response_mode}",
                                "d": "${a}"})

        flow.add_workflow_comp("a", StreamCompNode("a"), inputs_schema={"value": "${a}"},
                               comp_ability=[ComponentAbility.STREAM], wait_for_all=True)

        end = End({"responseTemplate": "hello:{{end_input}}"})

        async def failing_stream(*args, **kwargs):
            raise RuntimeError("mocked stream error")
            yield

        end.stream = failing_stream

        flow.set_end_comp("end", end,
                          inputs_schema={"end_input": "${start.d}"}, response_mode="streaming")
        flow.add_connection("start", "a")
        flow.add_stream_connection("a", "end")

        results = []
        with pytest.raises(JiuWenBaseException) as e:
            async for chunk in flow.stream({"a": 1, "b": "haha"}, WorkflowSession(),
                                           stream_modes=[BaseStreamMode.TRACE]):
                logger.info("stream chunk: {%s}", chunk)
                results.append(chunk)
        assert e.value.error_code == StatusCode.WORKFLOW_COMPONENT_RUNTIME_ERROR.code
        assert e.value.message == StatusCode.WORKFLOW_COMPONENT_RUNTIME_ERROR.errmsg.format(
            node_id="end",
            ability="stream",
            error_msg=RuntimeError("mocked stream error"),
        )

        assert len(results) == 8
        # for 'a' node with stream output, tracer finish frame with empty output
        a_finish_chunk = results[4]
        assert a_finish_chunk.payload["invokeId"] == 'a' and a_finish_chunk.payload["status"] == 'finish' and \
               a_finish_chunk.payload.get("outputs") == None
        # for 'end' node, tracer frame with error info
        end_error_chunk = results[6]
        assert end_error_chunk.payload["invokeId"] == 'end' and end_error_chunk.payload["status"] == 'error' and \
               end_error_chunk.payload["error"] == {
                   'error_code': StatusCode.WORKFLOW_COMPONENT_RUNTIME_ERROR.code,
                   "message": StatusCode.WORKFLOW_COMPONENT_RUNTIME_ERROR.errmsg.format(
                       node_id="end",
                       ability="stream",
                       error_msg=str(RuntimeError("mocked stream error")),
                   )}
