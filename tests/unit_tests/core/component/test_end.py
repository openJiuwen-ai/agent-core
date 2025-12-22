from typing import AsyncIterator

import pytest

from openjiuwen.core.common.constants.constant import END_NODE_STREAM
from openjiuwen.core.workflow import Input, Output
from openjiuwen.core.workflow import End
from openjiuwen.core.workflow import Start
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.session import Session
from openjiuwen.core.session import WorkflowSession
from openjiuwen.core.session.stream import BaseStreamMode, OutputSchema
from openjiuwen.core.workflow import Workflow, WorkflowExecutionState
from openjiuwen.core.workflow import ComponentAbility
from openjiuwen.core.workflow import WorkflowComponent
from tests.unit_tests.core.workflow.mock_nodes import (ComputeComponent2,
                                                       Node1, StreamCompNode)

pytestmark = pytest.mark.asyncio


class MockStreamCmp(WorkflowComponent):
    async def stream(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        yield inputs


async def test_simple_template_workflow():
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
    res = await flow.invoke({"a": 1, "b": "haha"}, WorkflowSession())
    assert res.result == {'responseContent': 'hello:haha'}


async def test_end_invoke_template():
    flow = Workflow()
    flow.set_start_comp("s", Start(),
                        inputs_schema={"query": "${user_inputs.query}", "content": "${user_inputs.content}"})
    conf = {"responseTemplate": "渲染结果:{{param1}},{{param2}}"}
    flow.set_end_comp("e", End(conf=conf), inputs_schema={"param1": "${s.query}", "param2": "${s.content}"})
    flow.add_connection("s", "e")
    res = await flow.invoke({"user_inputs": {"query": "你好", "content": "杭州"}}, WorkflowSession())

    assert res.result == {'responseContent': '渲染结果:你好,杭州'}


async def test_end_invoke_no_template():
    flow = Workflow()
    flow.set_start_comp("s", Start(),
                        inputs_schema={"query": "${user_inputs.query}", "content": "${user_inputs.content}"})
    conf = {}
    flow.set_end_comp("e", End(conf=conf), inputs_schema={"param1": "${s.query}", "param2": "${s.content}"})
    flow.add_connection("s", "e")
    res = await flow.invoke({"user_inputs": {"query": "你好", "content": "杭州"}}, WorkflowSession())
    assert res.result == {'output': {'param1': '你好', 'param2': '杭州'}}


async def test_end_stream_template():
    flow = Workflow()
    flow.set_start_comp("s", Start(),
                        inputs_schema={"query": "${user_inputs.query}", "content": "${user_inputs.content}"})
    conf = {"responseTemplate": "渲染结果:{{param1}},{{param2}}"}
    flow.set_end_comp("e", End(conf=conf), inputs_schema={"param1": "${s.query}", "param2": "${s.content}"},
                      response_mode="streaming")
    flow.add_connection("s", "e")
    result = flow.stream(inputs={"user_inputs": {"query": "你好", "content": "杭州"}}, session=WorkflowSession(),
                         stream_modes=[BaseStreamMode.OUTPUT])

    expect_result = [OutputSchema(type=END_NODE_STREAM, index=0, payload={'answer': '渲染结果:'}),
                     OutputSchema(type=END_NODE_STREAM, index=1, payload={'answer': '你好'}),
                     OutputSchema(type=END_NODE_STREAM, index=2, payload={'answer': ','}),
                     OutputSchema(type=END_NODE_STREAM, index=3, payload={'answer': '杭州'})]

    streams = []
    async for stream in result:
        print(stream)
        streams.append(stream)

    assert streams == expect_result


async def test_end_stream_no_template():
    flow = Workflow()
    flow.set_start_comp("s", Start(),
                        inputs_schema={"query": "${user_inputs.query}", "content": "${user_inputs.content}"})
    conf = {}
    flow.set_end_comp("e", End(conf=conf), inputs_schema={"param1": "${s.query}", "param2": "${s.content}"},
                      response_mode="streaming")
    flow.add_connection("s", "e")
    result = flow.stream(inputs={"user_inputs": {"query": "你好", "content": "杭州"}}, session=WorkflowSession(),
                         stream_modes=[BaseStreamMode.OUTPUT])

    expect_result = [
        OutputSchema(type=END_NODE_STREAM, index=0, payload={'output': {'param1': '你好'}}),
        OutputSchema(type=END_NODE_STREAM, index=1, payload={'output': {'param2': '杭州'}}),
    ]

    streams = []
    async for stream in result:
        print(stream)
        streams.append(stream)

    assert streams == expect_result


async def test_end_transform():
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
    result = flow.stream(inputs={"user_inputs": {"query": "你好", "content": "杭州"}}, session=WorkflowSession(),
                         stream_modes=[BaseStreamMode.OUTPUT])
    expect_result = [OutputSchema(type='end node stream', index=0, payload={'answer': '渲染结果:'}),
                     OutputSchema(type='end node stream', index=1, payload={'answer': '你好'}),
                     OutputSchema(type='end node stream', index=2, payload={'answer': ','}),
                     OutputSchema(type='end node stream', index=3, payload={'answer': '杭州'})]

    streams = []
    async for stream in result:
        print(stream)
        streams.append(stream)
    print(streams)

    assert expect_result == streams


async def test_simple_output_schema_workflow():
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
    res = await flow.invoke({"a": 1, "b": "haha"}, WorkflowSession())
    assert res.result == {'output': {'end_input': 'haha'}}


async def test_end_stream_workflow():
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
    actual_chunks = []
    expect_chunks = [OutputSchema(type='end node stream', index=0, payload={'answer': 'hello:'}),
                     OutputSchema(type='end node stream', index=1, payload={'answer': 1})]
    async for chunk in flow.stream({"a": 1, "b": "haha"}, WorkflowSession(), stream_modes=[BaseStreamMode.OUTPUT]):
        actual_chunks.append(chunk)
        index += 1

    print(actual_chunks)
    assert expect_chunks == actual_chunks

async def test_end_batch_stream_workflow():
    flow = Workflow()
    start = Start({"inputs": [{"id": "query", "type": "String", "required": "true", "sourceType": "ref"}]})
    input_schema = {
        "query": "${a}",
        "response_node": "${response_mode}",
        "d": "${a}"
    }
    flow.set_start_comp("start", start, inputs_schema=input_schema)

    flow.add_workflow_comp(
        "a",
        StreamCompNode("a"),
        inputs_schema={"value": "${a}"},
        comp_ability=[ComponentAbility.STREAM],
        wait_for_all=True
    )

    flow.set_end_comp(
        "end",
        End({"responseTemplate": "hello:{{value}}"}),
        stream_inputs_schema={"value": "${a.value}"},
        response_mode="streaming"
    )

    flow.add_connection("start", "a")
    flow.add_stream_connection("a", "end")

    expect_results = [
        OutputSchema(type='end node stream', index=0, payload={'answer': 'hello:'}),
        OutputSchema(type='end node stream', index=1, payload={'answer': 1}),
        OutputSchema(type='end node stream', index=2, payload={'answer': 2})
    ]

    real_result = []
    async for chunk in flow.stream({"a": 1, "b": "haha"},
                                   WorkflowSession(), stream_modes=[BaseStreamMode.OUTPUT]):
        real_result.append(chunk)

    print(real_result)
    assert expect_results == real_result



class MockStreamNode(WorkflowComponent):
    async def stream(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        yield inputs


async def test_end_no_streaming_no_template():
    workflow = Workflow()
    workflow.set_start_comp("start", Start(), inputs_schema={"a": "${user_input.a}", "b": "${user_input.b}"})
    workflow.set_end_comp("end", End(), stream_inputs_schema={'a': '${stream.a}', 'b': '${stream.b}'})
    workflow.add_workflow_comp("stream", MockStreamNode(), inputs_schema={"a": "${start.a}", "b": "${start.b}"})
    workflow.add_connection("start", "stream")
    workflow.add_stream_connection("stream", "end")

    user_input = {'user_input': {'a': 1, 'b': 2}}
    result = await workflow.invoke(user_input, WorkflowSession())
    assert result.result == {'collect_output': [{'a': 1}, {'b': 2}], 'output': None}


async def test_end_template_001():
    """
    Test End component with responseTemplate in streaming mode using invoke().
    
    Scenario:
        - Workflow: Start -> ComputeComponent2 -> End
        - End component has a responseTemplate: "输出:{{custom.result}}"
        - response_mode is set to "streaming"
        - The variable {{custom.result}} is not mapped in inputs_schema
    
    Expected behavior:
        - The static text "输出:" should be rendered as the first frame
        - The workflow should complete successfully with COMPLETED state
        - Result should contain at least one OutputSchema with type 'end node stream'
    """
    flow = Workflow()

    flow.set_start_comp("start", Start(), inputs_schema={"a": "${user_input.a}", "b": "${user_input.b}"})
    flow.add_workflow_comp("custom", ComputeComponent2(), inputs_schema={"a": "${start.a}", "b": "${start.b}"})
    flow.set_end_comp("end", End({"responseTemplate": "输出:{{custom.result}}"}), response_mode="streaming")

    flow.add_connection("start", "custom")
    flow.add_connection("custom", "end")

    user_input = {'user_input': {'a': 1, 'b': 2}}
    result = await flow.invoke(user_input, WorkflowSession())
    
    assert len(result.result) > 0, f"Expected non-empty result, got: {result.result}"
    assert result.state == WorkflowExecutionState.COMPLETED, f"Expected COMPLETED state, got: {result.state}"
    assert result.result[0].type == END_NODE_STREAM, f"Expected END_NODE_STREAM type, got: {result.result[0].type}"
    assert result.result[0].payload['answer'] == "输出:", \
        f"Expected '输出:' as first answer, got: {result.result[0].payload['answer']}"
    print(result.result)


async def test_end_template_002():
    """
    Test End component with responseTemplate in streaming mode using stream().
    
    Scenario:
        - Workflow: Start -> ComputeComponent2 -> End
        - End component has a responseTemplate: "输出是:{{custom.result}}"
        - response_mode is set to "streaming"
        - The variable {{custom.result}} is not mapped in inputs_schema
        - Using stream() method to consume output chunks
    
    Expected behavior:
        - The static text "输出是:" should be rendered as the first chunk
        - At least one chunk should be yielded from the stream
        - Each chunk should be an OutputSchema with type 'end node stream'
    """
    flow = Workflow()

    flow.set_start_comp("start", Start(), inputs_schema={"a": "${user_input.a}", "b": "${user_input.b}"})
    flow.add_workflow_comp("custom", ComputeComponent2(), inputs_schema={"a": "${start.a}", "b": "${start.b}"})
    flow.set_end_comp("end", End({"responseTemplate": "输出是:{{custom.result}}"}), response_mode="streaming")

    flow.add_connection("start", "custom")
    flow.add_connection("custom", "end")

    user_input = {'user_input': {'a': 1, 'b': 2}}
    stream_chunks = []
    async for chunk in flow.stream(user_input, WorkflowSession(), stream_modes=[BaseStreamMode.OUTPUT]):
        print(f"chunk: {chunk}")
        stream_chunks.append(chunk)
    
    assert len(stream_chunks) > 0, f"Expected at least 1 chunk, got: {len(stream_chunks)}"
    assert stream_chunks[0].type == END_NODE_STREAM, f"Expected END_NODE_STREAM type, got: {stream_chunks[0].type}"
    assert stream_chunks[0].payload['answer'] == "输出是:", \
        f"Expected '输出是:' as first answer, got: {stream_chunks[0].payload['answer']}"


async def test_end_template_013():
    """
    Test End component with responseTemplate in non-streaming (invoke) mode using invoke().
    
    Scenario:
        - Workflow: Start -> ComputeComponent2 -> End
        - End component has a responseTemplate: "输出:{{custom.result}}"
        - response_mode is NOT set (defaults to invoke mode)
        - The variable {{custom.result}} is not mapped in inputs_schema
    
    Expected behavior:
        - The static text "输出:" should be rendered in responseContent
        - The workflow should complete successfully with COMPLETED state
        - Result should contain responseContent with the static text
    """
    flow = Workflow()
    flow.set_start_comp("start", Start(), inputs_schema={"a": "${user_input.a}", "b": "${user_input.b}"})
    flow.add_workflow_comp("custom", ComputeComponent2(), inputs_schema={"a": "${start.a}", "b": "${start.b}"})
    flow.set_end_comp("end", End({"responseTemplate": "输出:{{custom.result}}"}))

    flow.add_connection("start", "custom")
    flow.add_connection("custom", "end")

    result = await flow.invoke({"user_input": {"a": 1, "b": 2}}, WorkflowSession())
    
    assert result.state == WorkflowExecutionState.COMPLETED, f"Expected COMPLETED state, got: {result.state}"
    assert result.result is not None, f"Expected non-None result, got: {result.result}"
    assert result.result.get('responseContent') == "输出:", \
        f"Expected '输出:' as responseContent, got: {result.result.get('responseContent')}"
    print(result)


async def test_end_template_014():
    """
    Test End component with responseTemplate in non-streaming (invoke) mode using stream().
    
    Scenario:
        - Workflow: Start -> ComputeComponent2 -> End
        - End component has a responseTemplate: "输出:{{custom.result}}"
        - response_mode is NOT set (defaults to invoke mode)
        - The variable {{custom.result}} is not mapped in inputs_schema
        - Using stream() method to consume output chunks
    
    Expected behavior:
        - At least one chunk should be yielded from the stream
        - The chunk should be an OutputSchema with type 'workflow_final'
        - The payload should contain responseContent with the static text "输出:"
    """
    flow = Workflow()
    flow.set_start_comp("start", Start(), inputs_schema={"a": "${user_input.a}", "b": "${user_input.b}"})
    flow.add_workflow_comp("custom", ComputeComponent2(), inputs_schema={"a": "${start.a}", "b": "${start.b}"})
    flow.set_end_comp("end", End({"responseTemplate": "输出:{{custom.result}}"}))
    flow.add_connection("start", "custom")
    flow.add_connection("custom", "end")

    stream_result = []
    async for chunk in flow.stream(
            {"user_input": {"a": 1, "b": 2}}, WorkflowSession(),
            stream_modes=[BaseStreamMode.OUTPUT]):
        stream_result.append(chunk)

    assert len(stream_result) > 0, f"Expected at least 1 chunk, got: {len(stream_result)}"
    assert stream_result[0].type == "workflow_final", \
        f"Expected 'workflow_final' type, got: {stream_result[0].type}"
    assert stream_result[0].payload.get('responseContent') == "输出:", \
        f"Expected '输出:' as responseContent, got: {stream_result[0].payload.get('responseContent')}"
    print(stream_result)


async def test_end_template_017():
    """
    Test End component with responseTemplate using stream_inputs_schema and stream().
    
    Scenario:
        - Workflow: Start -> ComputeComponent2 -> End
        - End component has a responseTemplate: "输出:{{a}}{{op}}{{b}}={{end_result}}"
        - stream_inputs_schema maps variables from ComputeComponent2's streaming output
        - Using stream connection from custom to end
        - Using stream() method to consume output chunks
    
    Expected behavior:
        - The template should be fully rendered with all variables: "输出:1+2=3"
        - At least one chunk should be yielded from the stream
        - The chunk should be an OutputSchema with type 'workflow_final'
    """
    flow = Workflow()
    flow.set_start_comp("start", Start(), inputs_schema={"a": "${user_input.a}", "b": "${user_input.b}"})
    flow.add_workflow_comp("custom", ComputeComponent2(), inputs_schema={"a": "${start.a}", "b": "${start.b}"})
    flow.set_end_comp(
        "end", End({"responseTemplate": "输出:{{a}}{{op}}{{b}}={{end_result}}"}),
        stream_inputs_schema={
            'op': '${custom.op}', 'a': '${custom.a}',
            'b': '${custom.b}', 'end_result': '${custom.result}'
        })

    flow.add_connection("start", "custom")
    flow.add_stream_connection("custom", "end")

    stream_result = []
    async for chunk in flow.stream(
            {"user_input": {"a": 1, "b": 2, "op": "+"}}, WorkflowSession(),
            stream_modes=[BaseStreamMode.OUTPUT]):
        stream_result.append(chunk)

    assert len(stream_result) > 0, f"Expected at least 1 chunk, got: {len(stream_result)}"
    assert stream_result[0].type == "workflow_final", \
        f"Expected 'workflow_final' type, got: {stream_result[0].type}"
    assert stream_result[0].payload.get('responseContent') == "输出:1+2=3", \
        f"Expected '输出:1+2=3' as responseContent, got: {stream_result[0].payload.get('responseContent')}"
    print(stream_result)


async def test_end_template_019():
    """
    Test End component with responseTemplate using stream_inputs_schema and invoke().
    
    Scenario:
        - Workflow: Start -> ComputeComponent2 -> End
        - End component has a responseTemplate: "输出:{{a}}{{op}}{{b}}={{end_result}}"
        - stream_inputs_schema maps variables from ComputeComponent2's streaming output
        - Using stream connection from custom to end
        - Using invoke() method to get final result
    
    Expected behavior:
        - The template should be fully rendered with all variables: "输出:1+2=3"
        - The workflow should complete successfully with COMPLETED state
        - Result should contain responseContent with the fully rendered template
    """
    flow = Workflow()
    flow.set_start_comp("start", Start(), inputs_schema={"a": "${user_input.a}", "b": "${user_input.b}"})
    flow.add_workflow_comp("custom", ComputeComponent2(), inputs_schema={"a": "${start.a}", "b": "${start.b}"})
    flow.set_end_comp(
        "end", End({"responseTemplate": "输出:{{a}}{{op}}{{b}}={{end_result}}"}),
        stream_inputs_schema={
            'op': '${custom.op}', 'a': '${custom.a}',
            'b': '${custom.b}', 'end_result': '${custom.result}'
        })
    flow.add_connection("start", "custom")
    flow.add_stream_connection("custom", "end")

    result = await flow.invoke({"user_input": {"a": 1, "b": 2, "op": "+"}}, WorkflowSession())

    assert result.state == WorkflowExecutionState.COMPLETED, \
        f"Expected COMPLETED state, got: {result.state}"
    assert result.result is not None, f"Expected non-None result, got: {result.result}"
    assert result.result.get('responseContent') == "输出:1+2=3", \
        f"Expected '输出:1+2=3' as responseContent, got: {result.result.get('responseContent')}"
    print(result)