#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import unittest
from collections.abc import Callable
from typing import AsyncIterator

from jiuwen.core.common.constants.constant import END_NODE_STREAM
from jiuwen.core.component.base import WorkflowComponent
from jiuwen.core.component.end_comp import End
from jiuwen.core.component.start_comp import Start
from jiuwen.core.context_engine.base import Context
from jiuwen.core.runtime.base import ComponentExecutable, Input, Output
from jiuwen.core.runtime.runtime import BaseRuntime, Runtime
from jiuwen.core.runtime.workflow import WorkflowRuntime
from jiuwen.core.stream.base import BaseStreamMode, OutputSchema
from jiuwen.core.workflow.base import Workflow, WorkflowExecutionState, WorkflowOutput
from jiuwen.core.workflow.workflow_config import ComponentAbility
from tests.unit_tests.workflow.test_mock_node import Node1, StreamCompNode


class MockStreamCmp(WorkflowComponent, ComponentExecutable):
    async def stream(self, inputs: Input, runtime: Runtime, context: Context) -> AsyncIterator[Output]:
        yield inputs


class EndNodeTest(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def invoke_workflow(self, inputs: dict, runtime: BaseRuntime, flow: Workflow):
        feature = asyncio.ensure_future(flow.invoke(inputs=inputs, runtime=runtime))
        self.loop.run_until_complete(feature)
        return feature.result()

    def assert_workflow_invoke(self, inputs: dict, context: BaseRuntime, flow: Workflow, expect_results: dict = None,
                               checker: Callable = None):
        if expect_results is not None:
            assert (self.invoke_workflow(inputs, context, flow) ==
                    WorkflowOutput(result=expect_results, state=WorkflowExecutionState.COMPLETED))
        elif checker is not None:
            checker(self.invoke_workflow(inputs, context, flow))

    def test_simple_template_workflow(self):
        # flow1: start -> a -> end
        flow = Workflow()
        flow.set_start_comp("start", Start(
            {"inputs": [{"id": "query", "type": "String", "required": "true", "sourceType": "ref"}]}),
                            inputs_schema={
                                "query": "${a}",
                                "response_node": "${response_mode}",
                                "d": "${b}"})
        flow.add_workflow_comp("a", Node1("a"),
                               inputs_schema={
                                   "aa": "${start.d}",
                                   "ac": "${start.d}"})
        flow.set_end_comp("end", End({"responseTemplate": "hello:{{end_input}}"}),
                          inputs_schema={
                              "end_input": "${start.d}",
                              "response_mode": "${start.response_node}"})
        flow.add_connection("start", "a")
        flow.add_connection("a", "end")
        self.assert_workflow_invoke({"a": 1, "b": "haha"}, WorkflowRuntime(), flow,
                                    expect_results={'output': {}, 'responseContent': 'hello:haha'})

    def test_end_invoke_template(self):
        flow = Workflow()
        flow.set_start_comp("s", Start(),
                            inputs_schema={"query": "${user_inputs.query}", "content": "${user_inputs.content}"})
        conf = {"responseTemplate": "渲染结果:{{param1}},{{param2}}"}
        flow.set_end_comp("e", End(conf=conf), inputs_schema={"param1": "${s.query}", "param2": "${s.content}"})
        flow.add_connection("s", "e")
        assert self.invoke_workflow(inputs={"user_inputs": {"query": "你好", "content": "杭州"}},
                                    runtime=WorkflowRuntime(), flow=flow).result == {
                   'responseContent': '渲染结果:你好,杭州', 'output': {}}

    def test_end_invoke_no_template(self):
        flow = Workflow()
        flow.set_start_comp("s", Start(),
                            inputs_schema={"query": "${user_inputs.query}", "content": "${user_inputs.content}"})
        conf = {}
        flow.set_end_comp("e", End(conf=conf), inputs_schema={"param1": "${s.query}", "param2": "${s.content}"})
        flow.add_connection("s", "e")
        assert self.invoke_workflow(inputs={"user_inputs": {"query": "你好", "content": "杭州"}},
                                    runtime=WorkflowRuntime(), flow=flow).result == {
                   'output': {'param1': '你好', 'param2': '杭州'}, 'responseContent': ''}

    def test_end_stream_template(self):
        flow = Workflow()
        flow.set_start_comp("s", Start(),
                            inputs_schema={"query": "${user_inputs.query}", "content": "${user_inputs.content}"})
        conf = {"responseTemplate": "渲染结果:{{param1}},{{param2}}"}
        flow.set_end_comp("e", End(conf=conf), inputs_schema={"param1": "${s.query}", "param2": "${s.content}"},
                          response_mode="streaming")
        flow.add_connection("s", "e")
        result = flow.stream(inputs={"user_inputs": {"query": "你好", "content": "杭州"}}, runtime=WorkflowRuntime(),
                             stream_modes=[BaseStreamMode.OUTPUT])

        expect_result = [OutputSchema(type=END_NODE_STREAM, index=0, payload={'answer': '渲染结果:'}),
                         OutputSchema(type=END_NODE_STREAM, index=1, payload={'answer': '你好'}),
                         OutputSchema(type=END_NODE_STREAM, index=2, payload={'answer': ','}),
                         OutputSchema(type=END_NODE_STREAM, index=3, payload={'answer': '杭州'}),
                         OutputSchema(type=END_NODE_STREAM, index=4, payload={'answer': ''})]

        async def iter_result(result):
            streams = []
            async for stream in result:
                streams.append(stream)
            return streams

        assert asyncio.get_event_loop().run_until_complete(iter_result(result)) == expect_result

    def test_end_stream_no_template(self):
        flow = Workflow()
        flow.set_start_comp("s", Start(),
                            inputs_schema={"query": "${user_inputs.query}", "content": "${user_inputs.content}"})
        conf = {}
        flow.set_end_comp("e", End(conf=conf), inputs_schema={"param1": "${s.query}", "param2": "${s.content}"},
                          response_mode="streaming")
        flow.add_connection("s", "e")
        result = flow.stream(inputs={"user_inputs": {"query": "你好", "content": "杭州"}}, runtime=WorkflowRuntime(),
                             stream_modes=[BaseStreamMode.OUTPUT])

        expect_result = [
            OutputSchema(type=END_NODE_STREAM, index=0, payload={'output': {'param1': '你好'}}),
            OutputSchema(type=END_NODE_STREAM, index=1, payload={'output': {'param2': '杭州'}}),
        ]

        async def iter_result(result):
            streams = []
            async for stream in result:
                streams.append(stream)
            return streams

        assert asyncio.get_event_loop().run_until_complete(iter_result(result)) == expect_result

    def test_end_transform(self):
        flow = Workflow()
        flow.set_start_comp("s", Start(),
                            inputs_schema={"query": "${user_inputs.query}", "content": "${user_inputs.content}"})
        flow.add_workflow_comp("n", MockStreamCmp(), inputs_schema={"param1": "${s.query}", "param2": "${s.content}"},
                               comp_ability=[ComponentAbility.STREAM], wait_for_all=True)
        conf = {"responseTemplate": "渲染结果:{{param1}},{{param2}}"}
        flow.set_end_comp("e", End(conf=conf), stream_inputs_schema={"param1": "${n.param1}", "param2": "${n.param2}"},
                          response_mode="streaming")
        flow.add_connection("s", "n")
        flow.add_stream_connection("n", "e")
        result = flow.stream(inputs={"user_inputs": {"query": "你好", "content": "杭州"}}, runtime=WorkflowRuntime(),
                             stream_modes=[BaseStreamMode.OUTPUT])
        exepct_result = [
            OutputSchema(type=END_NODE_STREAM, index=0, payload={'output': {'param1': '你好', 'param2': '杭州'}})]

        async def iter_result(result):
            streams = []
            async for stream in result:
                streams.append(stream)
            return streams[:1]

        assert asyncio.get_event_loop().run_until_complete(iter_result(result)) == exepct_result

    def test_simple_output_schema_workflow(self):
        # flow1: start -> a -> end
        flow = Workflow()
        flow.set_start_comp("start", Start(
            {"inputs": [{"id": "query", "type": "String", "required": "true", "sourceType": "ref"}]}),
                            inputs_schema={
                                "query": "${a}",
                                "response_node": "${response_mode}",
                                "d": "${b}"})
        flow.add_workflow_comp("a", Node1("a"),
                               inputs_schema={
                                   "aa": "${start.d}",
                                   "ac": "${start.d}"})
        flow.set_end_comp("end", End(),
                          inputs_schema={
                              "end_input": "${start.d}",
                              "response_mode": "${start.response_node}"},
                          )
        flow.add_connection("start", "a")
        flow.add_connection("a", "end")
        self.assert_workflow_invoke({"a": 1, "b": "haha"}, WorkflowRuntime(), flow,
                                    expect_results={'output': {'end_input': 'haha'}, 'responseContent': ''})

    def test_end_stream_workflow(self):
        async def stream_workflow():
            flow = Workflow()
            start = Start({"inputs": [{"id": "query", "type": "String", "required": "true", "sourceType": "ref"}]})
            flow.set_start_comp("start", start,
                                inputs_schema={
                                    "query": "${a}",
                                    "response_node": "${response_mode}",
                                    "d": "${a}"})

            flow.add_workflow_comp("a", StreamCompNode("a"), inputs_schema={"value": "${a}"},
                                   comp_ability=[ComponentAbility.STREAM], wait_for_all=True)

            flow.set_end_comp("end", End({"responseTemplate": "hello:{{end_input}}"}),
                              inputs_schema={"end_input": "${start.d}"}, response_mode="streaming")
            flow.add_connection("start", "a")
            flow.add_stream_connection("a", "end")

            index = 0
            async for chunk in flow.stream({"a": 1, "b": "haha"},
                                           WorkflowRuntime()):
                index += 1

        self.loop.run_until_complete(stream_workflow())

    def test_end_batch_stream_workflow(self):

        async def stream_workflow():
            flow = Workflow()
            start = Start({"inputs": [{"id": "query", "type": "String", "required": "true", "sourceType": "ref"}]})
            input_schema = {
                "query": "${a}",
                "response_node": "${response_mode}",
                "d": "${a}"
            }
            flow.set_start_comp("start", start, inputs_schema=input_schema)

            flow.add_workflow_comp("a", StreamCompNode("a"), inputs_schema={"value": "${a}"},
                                   comp_ability=[ComponentAbility.STREAM], wait_for_all=True)

            flow.set_end_comp("end", End({"responseTemplate": "hello:{{value}}"}),
                              stream_inputs_schema={"value": "${a.value}"}, inputs_schema={"value": "${a.value}"},
                              response_mode="streaming")
            flow.add_connection("start", "a")
            flow.add_stream_connection("a", "end")

            index = 0
            async for chunk in flow.stream({"a": 1, "b": "haha"},
                                           WorkflowRuntime()):
                index += 1

        self.loop.run_until_complete(stream_workflow())
