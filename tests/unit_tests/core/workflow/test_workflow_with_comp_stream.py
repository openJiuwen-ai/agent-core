import asyncio
import os
from typing import AsyncIterator

import pytest

from openjiuwen.core.common.constants.constant import INTERACTION
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.workflow import Input, Output, WorkflowCard
from openjiuwen.core.workflow import End, EndConfig
from openjiuwen.core.workflow import Start
from openjiuwen.core.workflow import WorkflowComponent
from openjiuwen.core.workflow.components.flow_related.workflow_comp import SubWorkflowComponent
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.graph.executable import Executable
from openjiuwen.core.session import END_COMP_TEMPLATE_RENDER_POSITION_TIMEOUT_KEY, WORKFLOW_EXECUTE_TIMEOUT
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.session import BaseSession, Session
from openjiuwen.core.session import WorkflowSession
from openjiuwen.core.session.stream import StreamMode, BaseStreamMode, OutputSchema
from openjiuwen.core.workflow import Workflow, WorkflowOutput, WorkflowChunk
from openjiuwen.core.workflow import ComponentAbility
from tests.unit_tests.core.workflow.mock_nodes import ComputeComponent2, DualAbilityWithErrorComponent

pytestmark = pytest.mark.asyncio

os.environ.setdefault("LLM_SSL_VERIFY", "false")


class MockStreamNode(WorkflowComponent):
    def __init__(self):
        super().__init__()

    async def invoke(self, inputs, session: BaseSession, context: ModelContext = None) -> WorkflowOutput:
        return inputs

    async def stream(
            self,
            inputs,
            session: BaseSession,
            context: ModelContext = None,
            stream_modes: list[StreamMode] = None
    ) -> AsyncIterator[WorkflowChunk]:
        await asyncio.sleep(0.3)
        yield inputs

    def to_executable(self) -> Executable:
        return self


async def test_no_stream_called():
    with pytest.raises(JiuWenBaseException) as error:
        flow = Workflow()
        flow.set_start_comp("start", Start())
        flow.set_end_comp("end", End(), inputs_schema={}, response_mode="streaming")
        flow.add_workflow_comp("stream", MockStreamNode(), inputs_schema={})
        flow.add_connection("start", "stream")
        flow.add_stream_connection("stream", "end")
        session = WorkflowSession()
        session.config().set_envs({WORKFLOW_EXECUTE_TIMEOUT: 0.2})
        await flow.invoke({"a": "生成markdown回复"}, session)

    assert error.value.error_code == StatusCode.WORKFLOW_INVOKE_TIMEOUT.code
    with pytest.raises(JiuWenBaseException) as error:
        session = WorkflowSession()
        session.config().set_envs({WORKFLOW_EXECUTE_TIMEOUT: 0.2})
        async for chunk in flow.stream({"a": "生成markdown回复"}, session,
                                       stream_modes=[BaseStreamMode.OUTPUT]):
            logger.info(chunk)
    assert error.value.error_code == StatusCode.WORKFLOW_STREAM_EXECUTION_TIMEOUT.code


class Producer(WorkflowComponent):
    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        return {"output": inputs.get("array")}

    async def stream(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        logger.info(f"producer inputs: {inputs}")
        for v in inputs.get("array"):
            logger.info(f"send stream frame {v}")
            yield {"output": v}


async def test_multi_stream_workflow():
    wf = create_component_stream_workflow_with_template()
    chunks = []
    expect_chunks = [
        OutputSchema(type='end node stream', index=0, payload={'answer': 'a: '}),
        OutputSchema(type='end node stream', index=1, payload={'answer': 1}),
        OutputSchema(type='end node stream', index=2, payload={'answer': 2}),
        OutputSchema(type='end node stream', index=3, payload={'answer': 3}),
        OutputSchema(type='end node stream', index=4, payload={'answer': '; c: '}),
        OutputSchema(type='end node stream', index=5, payload={'answer': 1}),
        OutputSchema(type='end node stream', index=6, payload={'answer': 2}),
        OutputSchema(type='end node stream', index=7, payload={'answer': 3}),
        OutputSchema(type='end node stream', index=8, payload={'answer': '; batch: '}),
        OutputSchema(type='end node stream', index=9, payload={'answer': [1, 2, 3]}),
        OutputSchema(type='end node stream', index=10, payload={'answer': '; b: '}),
        OutputSchema(type='end node stream', index=11, payload={'answer': 1}),
        OutputSchema(type='end node stream', index=12, payload={'answer': 2}),
        OutputSchema(type='end node stream', index=13, payload={'answer': 3})]

    async for chunk in wf.stream({"inputs": [1, 2, 3]}, WorkflowSession(), stream_modes=[BaseStreamMode.OUTPUT]):
        assert chunk is not None
        logger.info(chunk)
        chunks.append(chunk)
    assert chunks == expect_chunks

    res = await wf.invoke({"inputs": [1, 2, 3]}, WorkflowSession())
    logger.info(res)
    assert res.result == expect_chunks


async def test_batch_multi_stream_workflow():
    def create_component_workflow_with_template() -> Workflow:
        workflow = Workflow()
        workflow.set_start_comp("start", Start(), inputs_schema={"array": "${inputs}"})
        workflow.add_workflow_comp("a", Producer(), inputs_schema={"array": "${start.array}"})
        workflow.add_workflow_comp("b", Producer(), inputs_schema={"array": "${start.array}"})
        workflow.add_workflow_comp("c", Producer(), inputs_schema={"array": "${start.array}"})
        workflow.add_workflow_comp("batch", Producer(), inputs_schema={"array": "${start.array}"})
        end = End(EndConfig(responseTemplate="a: {{a}}; c: {{c}}; batch: {{batch}}; b: {{b}}"))
        workflow.set_end_comp("end", end,
                              inputs_schema={"batch": "${batch.output}"},
                              stream_inputs_schema={"a": "${a.output}", "b": "${b.output}", "c": "${c.output}"})

        workflow.add_connection("start", "a")
        workflow.add_connection("start", "b")
        workflow.add_connection("start", "c")
        workflow.add_connection("start", "batch")
        workflow.add_stream_connection("a", "end")
        workflow.add_stream_connection("b", "end")
        workflow.add_stream_connection("c", "end")
        workflow.add_connection("batch", "end")
        return workflow

    wf = create_component_workflow_with_template()

    res = await wf.invoke({"inputs": [1, 2, 3]}, WorkflowSession())
    logger.info(res)
    assert res.result == {'responseContent': 'a: 123; c: 123; batch: [1, 2, 3]; b: 123'}

    chunks = []
    # End 组件批输出时也会发送 end node stream，然后发送 workflow_final
    expect_chunks = [
        OutputSchema(type='workflow_final', index=0,
                     payload={'responseContent': 'a: 123; c: 123; batch: [1, 2, 3]; b: 123'})
    ]

    async for chunk in wf.stream({"inputs": [1, 2, 3]}, WorkflowSession(), stream_modes=[BaseStreamMode.OUTPUT]):
        assert chunk is not None
        logger.info(chunk)
        chunks.append(chunk)
    assert chunks == expect_chunks


def create_component_stream_workflow_with_template() -> Workflow:
    workflow = Workflow()
    workflow.set_start_comp("start", Start(), inputs_schema={"array": "${inputs}"})
    workflow.add_workflow_comp("a", Producer(), inputs_schema={"array": "${start.array}"})
    workflow.add_workflow_comp("b", Producer(), inputs_schema={"array": "${start.array}"})
    workflow.add_workflow_comp("c", Producer(), inputs_schema={"array": "${start.array}"})
    workflow.add_workflow_comp("batch", Producer(), inputs_schema={"array": "${start.array}"})
    end = End(EndConfig(responseTemplate="a: {{a}}; c: {{c}}; batch: {{batch}}; b: {{b}}"))
    workflow.set_end_comp("end", end,
                          inputs_schema={"batch": "${batch.output}"},
                          stream_inputs_schema={"a": "${a.output}", "b": "${b.output}", "c": "${c.output}"},
                          response_mode="streaming")

    workflow.add_connection("start", "a")
    workflow.add_connection("start", "b")
    workflow.add_connection("start", "c")
    workflow.add_connection("start", "batch")
    workflow.add_stream_connection("a", "end")
    workflow.add_stream_connection("b", "end")
    workflow.add_stream_connection("c", "end")
    workflow.add_connection("batch", "end")
    return workflow


def create_component_stream_workflow_without_template() -> Workflow:
    workflow = Workflow()
    workflow.set_start_comp("start", Start(), inputs_schema={"array": "${inputs}"})
    workflow.add_workflow_comp("a", Producer(), inputs_schema={"array": "${start.array}"})
    workflow.add_workflow_comp("b", Producer(), inputs_schema={"array": "${start.array}"})
    workflow.add_workflow_comp("c", Producer(), inputs_schema={"array": "${start.array}"})
    workflow.add_workflow_comp("batch", Producer(), inputs_schema={"array": "${start.array}"})
    workflow.set_end_comp("end", End(),
                          inputs_schema={"batch": "${batch.output}"},
                          stream_inputs_schema={"a": "${a.output}", "b": "${b.output}", "c": "${c.output}"},
                          response_mode="streaming")

    workflow.add_connection("start", "a")
    workflow.add_connection("start", "b")
    workflow.add_connection("start", "c")
    workflow.add_connection("start", "batch")
    workflow.add_stream_connection("a", "end")
    workflow.add_stream_connection("b", "end")
    workflow.add_stream_connection("c", "end")
    workflow.add_connection("batch", "end")
    return workflow


async def test_stream_component_in_sub_workflow_with_invoke():
    def create_component_invoke_workflow_without_template() -> Workflow:
        workflow = Workflow()
        workflow.set_start_comp("start", Start(), inputs_schema={"array": "${inputs}"})
        workflow.add_workflow_comp("a", Producer(), inputs_schema={"array": "${start.array}"})
        workflow.add_workflow_comp("b", Producer(), inputs_schema={"array": "${start.array}"})
        workflow.add_workflow_comp("c", Producer(), inputs_schema={"array": "${start.array}"})
        workflow.add_workflow_comp("batch", Producer(), inputs_schema={"array": "${start.array}"})
        workflow.set_end_comp("end", End(),
                              inputs_schema={"batch": "${batch.output}"},
                              stream_inputs_schema={"a": "${a.output}", "b": "${b.output}", "c": "${c.output}"})

        workflow.add_connection("start", "a")
        workflow.add_connection("start", "b")
        workflow.add_connection("start", "c")
        workflow.add_connection("start", "batch")
        workflow.add_stream_connection("a", "end")
        workflow.add_stream_connection("b", "end")
        workflow.add_stream_connection("c", "end")
        workflow.add_connection("batch", "end")
        return workflow

    wf = Workflow()
    wf.set_start_comp("main_start", Start(), inputs_schema={"array": "${inputs}"})
    wf.add_workflow_comp("workflow", SubWorkflowComponent(create_component_invoke_workflow_without_template()),
                         inputs_schema={"inputs": "${main_start.array}"})
    end = End(EndConfig(responseTemplate="sub_workflow: {{sub_workflow}}"))
    wf.set_end_comp("main_end", end,
                    inputs_schema={"sub_workflow": "${workflow.output.batch}"},
                    response_mode="streaming")

    wf.add_connection("main_start", "workflow")
    wf.add_connection("workflow", "main_end")
    chunks = []
    expect_chunks = [
        OutputSchema(type='end node stream', index=0, payload={'answer': 'sub_workflow: '}),
        OutputSchema(type='end node stream', index=1, payload={'answer': [1, 2, 3]})]

    async for chunk in wf.stream({"inputs": [1, 2, 3]}, WorkflowSession(), stream_modes=[BaseStreamMode.OUTPUT]):
        assert chunk is not None
        logger.info(chunk)
        chunks.append(chunk)
    assert expect_chunks == chunks


async def test_stream_component_in_sub_workflow_with_stream():
    wf = Workflow()
    wf.set_start_comp("main_start", Start(), inputs_schema={"array": "${inputs}"})
    wf.add_workflow_comp("workflow", SubWorkflowComponent(create_component_stream_workflow_with_template()),
                         inputs_schema={"inputs": "${main_start.array}"})
    end = End(EndConfig(responseTemplate="sub_workflow: {{sub_workflow}}"))
    wf.set_end_comp("main_end", end,
                    stream_inputs_schema={"sub_workflow": "${workflow.answer}"},
                    response_mode="streaming")

    wf.add_connection("main_start", "workflow")
    wf.add_stream_connection("workflow", "main_end")

    chunks = []
    expect_chunks = [
        OutputSchema(type='end node stream', index=0, payload={'answer': 'sub_workflow: '}),
        OutputSchema(type='end node stream', index=1, payload={'answer': 'a: '}),
        OutputSchema(type='end node stream', index=2, payload={'answer': 1}),
        OutputSchema(type='end node stream', index=3, payload={'answer': 2}),
        OutputSchema(type='end node stream', index=4, payload={'answer': 3}),
        OutputSchema(type='end node stream', index=5, payload={'answer': '; c: '}),
        OutputSchema(type='end node stream', index=6, payload={'answer': 1}),
        OutputSchema(type='end node stream', index=7, payload={'answer': 2}),
        OutputSchema(type='end node stream', index=8, payload={'answer': 3}),
        OutputSchema(type='end node stream', index=9, payload={'answer': '; batch: '}),
        OutputSchema(type='end node stream', index=10, payload={'answer': [1, 2, 3]}),
        OutputSchema(type='end node stream', index=11, payload={'answer': '; b: '}),
        OutputSchema(type='end node stream', index=12, payload={'answer': 1}),
        OutputSchema(type='end node stream', index=13, payload={'answer': 2}),
        OutputSchema(type='end node stream', index=14, payload={'answer': 3})]

    async for chunk in wf.stream({"inputs": [1, 2, 3]}, WorkflowSession(), stream_modes=[BaseStreamMode.OUTPUT]):
        assert chunk is not None
        logger.info(chunk)
        chunks.append(chunk)
    assert chunks == expect_chunks


async def test_stream_component_in_sub_workflow_with_stream_collect():
    wf = Workflow()
    wf.set_start_comp("main_start", Start(), inputs_schema={"array": "${inputs}"})
    wf.add_workflow_comp("workflow", SubWorkflowComponent(create_component_stream_workflow_with_template()),
                         inputs_schema={"inputs": "${main_start.array}"})
    end = End(EndConfig(responseTemplate="sub_workflow: {{sub_workflow}}"))
    wf.set_end_comp("main_end", end,
                    inputs_schema={"sub_workflow": "${workflow.stream}"},
                    response_mode="streaming")

    wf.add_connection("main_start", "workflow")
    wf.add_connection("workflow", "main_end")

    chunks = []
    expect_chunks = [
        OutputSchema(type='end node stream', index=0, payload={'answer': 'sub_workflow: '}),
        OutputSchema(type='end node stream', index=1, payload={'answer': [
            {'answer': 'a: '},
            {'answer': 1},
            {'answer': 2},
            {'answer': 3},
            {'answer': '; c: '},
            {'answer': 1},
            {'answer': 2},
            {'answer': 3},
            {'answer': '; batch: '},
            {'answer': [1, 2, 3]},
            {'answer': '; b: '},
            {'answer': 1},
            {'answer': 2},
            {'answer': 3}]})]

    async for chunk in wf.stream({"inputs": [1, 2, 3]}, WorkflowSession(), stream_modes=[BaseStreamMode.OUTPUT]):
        assert chunk is not None
        logger.info(chunk)
        chunks.append(chunk)
    assert expect_chunks == chunks


# Test the ability of workflow components to stream between components
async def test_stream_component_in_sub_workflow_with_substream():
    wf = Workflow()
    wf.set_start_comp("main_start", Start(), inputs_schema={"array": "${inputs}"})
    wf.add_workflow_comp("workflow", SubWorkflowComponent(create_component_stream_workflow_without_template()),
                         inputs_schema={"inputs": "${main_start.array}"})
    end = End()
    wf.set_end_comp("main_end", end,
                    stream_inputs_schema={"sub_workflow": "${workflow.output}"},
                    response_mode="streaming")

    wf.add_connection("main_start", "workflow")
    wf.add_stream_connection("workflow", "main_end")
    chunks = []
    expect_chunks = [
        {'output': {'sub_workflow': {'a': 1}}},
        {'output': {'sub_workflow': {'a': 2}}},
        {'output': {'sub_workflow': {'a': 3}}},
        {'output': {'sub_workflow': {'b': 1}}},
        {'output': {'sub_workflow': {'b': 2}}},
        {'output': {'sub_workflow': {'b': 3}}},
        {'output': {'sub_workflow': {'c': 1}}},
        {'output': {'sub_workflow': {'c': 2}}},
        {'output': {'sub_workflow': {'c': 3}}},
        {'output': {'sub_workflow': {'batch': [1, 2, 3]}}}
    ]
    async for chunk in wf.stream({"inputs": [1, 2, 3]}, WorkflowSession(), stream_modes=[BaseStreamMode.OUTPUT]):
        assert chunk is not None
        logger.info(chunk)
        chunks.append(chunk.payload)
    assert len(chunks) == len(expect_chunks)
    for chunk in chunks:
        assert chunk in expect_chunks


# Test the ability of workflow components to stream between components with templates
async def test_stream_component_in_sub_workflow_with_substream_template():
    wf = Workflow()
    wf.set_start_comp("main_start", Start(), inputs_schema={"array": "${inputs}"})
    wf.add_workflow_comp("workflow", SubWorkflowComponent(create_component_stream_workflow_with_template()),
                         inputs_schema={"inputs": "${main_start.array}"})
    end = End()
    wf.set_end_comp("main_end", end,
                    stream_inputs_schema={"sub_workflow": "${workflow.answer}"},
                    response_mode="streaming")

    wf.add_connection("main_start", "workflow")
    wf.add_stream_connection("workflow", "main_end")

    chunks = []
    expect_chunks = [
        OutputSchema(type='end node stream', index=0, payload={'output': {'sub_workflow': 'a: '}}),
        OutputSchema(type='end node stream', index=1, payload={'output': {'sub_workflow': 1}}),
        OutputSchema(type='end node stream', index=2, payload={'output': {'sub_workflow': 2}}),
        OutputSchema(type='end node stream', index=3, payload={'output': {'sub_workflow': 3}}),
        OutputSchema(type='end node stream', index=4, payload={'output': {'sub_workflow': '; c: '}}),
        OutputSchema(type='end node stream', index=5, payload={'output': {'sub_workflow': 1}}),
        OutputSchema(type='end node stream', index=6, payload={'output': {'sub_workflow': 2}}),
        OutputSchema(type='end node stream', index=7, payload={'output': {'sub_workflow': 3}}),
        OutputSchema(type='end node stream', index=8, payload={'output': {'sub_workflow': '; batch: '}}),
        OutputSchema(type='end node stream', index=9, payload={'output': {'sub_workflow': [1, 2, 3]}}),
        OutputSchema(type='end node stream', index=10, payload={'output': {'sub_workflow': '; b: '}}),
        OutputSchema(type='end node stream', index=11, payload={'output': {'sub_workflow': 1}}),
        OutputSchema(type='end node stream', index=12, payload={'output': {'sub_workflow': 2}}),
        OutputSchema(type='end node stream', index=13, payload={'output': {'sub_workflow': 3}})]

    async for chunk in wf.stream({"inputs": [1, 2, 3]}, WorkflowSession(), stream_modes=[BaseStreamMode.OUTPUT]):
        assert chunk is not None
        logger.info(chunk)
        chunks.append(chunk)

    logger.info(chunks)
    assert expect_chunks == chunks


class Interaction(WorkflowComponent):
    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        result = await session.interact("please enter any input")
        return {"output": result}


async def test_interaction_with_stream():
    def create_workflow() -> Workflow:
        wf = Workflow(card=WorkflowCard(id="test_interaction_with_stream"))
        wf.set_start_comp("start", Start(), inputs_schema={"array": "${inputs}"})
        wf.add_workflow_comp("interaction", Interaction())
        wf.add_workflow_comp("stream", Producer(), inputs_schema={"array": "${start.array}"})
        end = End(EndConfig(responseTemplate="a: {{a}}; batch: {{batch}}"))
        wf.set_end_comp("end", end,
                        inputs_schema={"batch": "${interaction.output}"},
                        stream_inputs_schema={"a": "${stream.output}"},
                        response_mode="streaming")

        wf.add_connection("start", "interaction")
        wf.add_connection("start", "stream")
        wf.add_connection("interaction", "end")
        wf.add_stream_connection("stream", "end")
        return wf

    wf1 = create_workflow()
    wf2 = create_workflow()
    chunks = []
    interaction = False
    async for chunk in wf1.stream({"inputs": [1, 2, 3]}, WorkflowSession(session_id="123"),
                                  stream_modes=[BaseStreamMode.OUTPUT]):
        assert chunk is not None
        logger.info(chunk)
        if chunk.type == INTERACTION:
            interaction = True
    assert interaction

    logger.info("human in the loop...")
    session = WorkflowSession(session_id="123")
    actual_chunks = []
    expect_chunks = [OutputSchema(type='end node stream', index=0, payload={'answer': 'a: '}),
                     OutputSchema(type='end node stream', index=1, payload={'answer': '; batch: '}),
                     OutputSchema(type='end node stream', index=2, payload={'answer': {'inputs': [1, 2, 3]}})]

    async for chunk in wf2.stream(InteractiveInput({"inputs": [1, 2, 3]}), session,
                                  stream_modes=[BaseStreamMode.OUTPUT]):
        assert chunk is not None
        logger.info(chunk)
        actual_chunks.append(chunk)
    assert actual_chunks == expect_chunks


async def test_interaction_with_exception():
    run_times = 0

    class ExceptionComp(WorkflowComponent):
        async def stream(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
            if run_times == 0:
                raise Exception("first time")
            else:
                for i in range(10):
                    yield dict(output=i)

    def create_workflow_with_exception() -> Workflow:
        wf = Workflow(card=WorkflowCard(id="test_interaction_with_exception"))
        wf.set_start_comp("start", Start(), inputs_schema={"array": "${inputs}"})
        wf.add_workflow_comp("exception", ExceptionComp())
        end = End(EndConfig(responseTemplate="a: {{a}}; batch: {{batch}}"))
        wf.set_end_comp("end", end,
                        stream_inputs_schema={"a": "${exception.output}"},
                        response_mode="streaming")

        wf.add_connection("start", "exception")
        wf.add_stream_connection("exception", "end")
        return wf

    wf1 = create_workflow_with_exception()
    wf2 = create_workflow_with_exception()

    try:
        res = await wf1.invoke({"inputs": [1, 2, 3]}, WorkflowSession(session_id="123"))
        logger.info(res)
    except Exception as e:
        logger.error(e)
    run_times += 1
    logger.info("human in the loop...")
    session = WorkflowSession(session_id="123")
    session.config().set_envs({END_COMP_TEMPLATE_RENDER_POSITION_TIMEOUT_KEY: 1})
    res = await wf2.invoke(InteractiveInput({"inputs": [1, 2, 3]}), session)

    expect_result = [
        OutputSchema(type='end node stream', index=0, payload={'answer': 'a: '}),
        OutputSchema(type='end node stream', index=1, payload={'answer': 0}),
        OutputSchema(type='end node stream', index=2, payload={'answer': 1}),
        OutputSchema(type='end node stream', index=3, payload={'answer': 2}),
        OutputSchema(type='end node stream', index=4, payload={'answer': 3}),
        OutputSchema(type='end node stream', index=5, payload={'answer': 4}),
        OutputSchema(type='end node stream', index=6, payload={'answer': 5}),
        OutputSchema(type='end node stream', index=7, payload={'answer': 6}),
        OutputSchema(type='end node stream', index=8, payload={'answer': 7}),
        OutputSchema(type='end node stream', index=9, payload={'answer': 8}),
        OutputSchema(type='end node stream', index=10, payload={'answer': 9}),
        OutputSchema(type='end node stream', index=11, payload={'answer': '; batch: '})
    ]

    assert res.result == expect_result


class StreamNodeWithException(WorkflowComponent):
    def __init__(self):
        super().__init__()
        self._raise_error: bool = True

    async def stream(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        array = inputs.get("array")
        for item in array:
            yield {"array": item}

    async def transform(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        iter = inputs.get("array")
        i = 0
        async for item in iter:
            yield {'array': item}
            i += 1
            if i >= 3:
                if self._raise_error:
                    self._raise_error = False
                    raise JiuWenBaseException(-1, "mock error")

    async def collect(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        iter = inputs.get("array")
        results = []
        async for item in iter:
            results.append(item)
        return {"collect": results}


async def test_workflow_stream_with_exception():
    workflow = Workflow()
    workflow.set_start_comp("start_comp", Start(), inputs_schema={"array": "${user_inputs.array}"})
    workflow.add_workflow_comp("stream_comp", StreamNodeWithException(), inputs_schema={"array": "${start_comp.array}"})
    workflow.add_workflow_comp("transform_comp", StreamNodeWithException(),
                               stream_inputs_schema={"array": "${stream_comp.array}"})
    workflow.add_workflow_comp("collect_comp", StreamNodeWithException(),
                               stream_inputs_schema={"array": "${transform_comp.array}"})
    workflow.set_end_comp("end_comp", End(), inputs_schema={"result": "${collect_comp.collect}"})
    workflow.add_connection("start_comp", "stream_comp")
    workflow.add_stream_connection("stream_comp", "transform_comp")
    workflow.add_stream_connection("transform_comp", "collect_comp")
    workflow.add_connection("collect_comp", "end_comp")
    with pytest.raises(JiuWenBaseException) as e:
        await workflow.invoke(inputs={"user_inputs": {"array": [1, 2, 3, 4, 5, 6, 7]}},
                              session=WorkflowSession())
    assert e.value.error_code == StatusCode.WORKFLOW_COMPONENT_RUNTIME_ERROR.code
    assert "error: [-1] mock error" in e.value.message

    logger.info("after exception, execution again")
    result = await workflow.invoke(inputs={"user_inputs": {"array": [1, 2, 3, 4, 5, 6, 7]}}, session=WorkflowSession())
    assert result.result == {'output': {'result': [1, 2, 3, 4, 5, 6, 7]}}


async def test_node_with_dual_stream_abilities_transform_and_stream():
    """
    Test a node with both TRANSFORM and STREAM abilities can correctly merge
    stream input and batch input, then output combined stream to downstream.

    Workflow structure:
        start --> A (STREAM)  --stream--> C (TRANSFORM + STREAM) --stream--> end
        start --> B (INVOKE)  --batch---> C

    Node behaviors:
        - A: Receives batch input {a=1, b=2}, streams out {a, op, b, result}
        - B: Receives batch input {a=1, b=2}, returns {result: 3}
        - C: Has dual abilities:
            * TRANSFORM: Consumes A's stream, outputs {a_A, op_A, b_A, result_A}
            * STREAM: Uses B's result as input {a=3, b=3}, streams out {a, op, b, result}
        - end: Collects all stream data from C

    This test verifies:
        1. Node C correctly executes both TRANSFORM and STREAM abilities
        2. All stream outputs from C (both abilities) are received by end node
        3. End message is sent only after ALL stream abilities complete (not prematurely)
    """
    flow = Workflow()

    # Setup start node
    flow.set_start_comp("start", Start(),
                        inputs_schema={'a': '${user_inputs.a}', 'b': '${user_inputs.b}'})

    # Setup end node to collect all stream fields from C
    flow.set_end_comp("end", End(), response_mode="streaming",
                      stream_inputs_schema={
                          'a': '${C.a}', 'op': '${C.op}', 'b': '${C.b}', 'result': '${C.result}',
                          'a_A': '${C.a_A}', 'op_A': '${C.op_A}', 'b_A': '${C.b_A}', 'result_A': '${C.result_A}'
                      })

    # Node A: STREAM ability - batch in, stream out
    flow.add_workflow_comp('A', ComputeComponent2(),
                           inputs_schema={'a': '${start.a}', 'b': '${start.b}'},
                           comp_ability=[ComponentAbility.STREAM], wait_for_all=True)

    # Node B: INVOKE ability - batch in, batch out
    flow.add_workflow_comp('B', ComputeComponent2(),
                           inputs_schema={'a': '${start.a}', 'b': '${start.b}'},
                           comp_ability=[ComponentAbility.INVOKE], wait_for_all=True)

    # Node C: TRANSFORM + STREAM abilities
    # - TRANSFORM: stream in (from A), stream out
    # - STREAM: batch in (from B), stream out
    flow.add_workflow_comp('C', ComputeComponent2(),
                           inputs_schema={'a': '${B.result}', 'b': '${B.result}'},
                           stream_inputs_schema={'data': {
                               'a_A': '${A.a}', 'op_A': '${A.op}',
                               'b_A': '${A.b}', 'result_A': '${A.result}'
                           }},
                           comp_ability=[ComponentAbility.TRANSFORM, ComponentAbility.STREAM],
                           wait_for_all=True)

    # Setup connections
    flow.add_connection('start', 'A')
    flow.add_connection('start', 'B')
    flow.add_stream_connection('A', 'C')
    flow.add_connection('B', 'C')
    flow.add_stream_connection('C', 'end')

    # Execute workflow
    user_inputs = {'user_inputs': {'a': 1, 'b': 2}}
    stream_chunks = []

    async for chunk in flow.stream(user_inputs, session=WorkflowSession(), stream_modes=[BaseStreamMode.OUTPUT]):
        stream_chunks.append(chunk)

    # Verify results
    assert len(stream_chunks) > 0, "Should receive stream chunks from workflow"

    # Extract payload outputs for verification
    output_payloads = []
    for chunk in stream_chunks:
        if hasattr(chunk, 'payload'):
            output_payloads.append(chunk.payload.get('output', {}))

    # Verify STREAM ability output from C (uses B.result=3 as input: a=3, b=3, result=6)
    stream_keys = {'a', 'op', 'b', 'result'}
    stream_outputs = {}
    for payload in output_payloads:
        for key, value in payload.items():
            if key in stream_keys:
                stream_outputs[key] = value
    assert 'a' in stream_outputs, "Should have 'a' from C's STREAM ability"
    assert 'op' in stream_outputs, "Should have 'op' from C's STREAM ability"
    assert 'b' in stream_outputs, "Should have 'b' from C's STREAM ability"
    assert 'result' in stream_outputs, "Should have 'result' from C's STREAM ability"
    # B.result = 1 + 2 = 3, so C's STREAM input is {a=3, b=3}, output result = 3 + 3 = 6
    stream_result = stream_outputs.get('result')
    assert stream_result == 6, f"C's STREAM result should be 6 (3+3), got {stream_result}"

    # Verify TRANSFORM ability output from C (transforms A's stream)
    transform_keys = {'a_A', 'op_A', 'b_A', 'result_A'}
    transform_outputs = {}
    for payload in output_payloads:
        for key, value in payload.items():
            if key in transform_keys:
                transform_outputs[key] = value
    assert 'a_A' in transform_outputs, "Should have 'a_A' from C's TRANSFORM ability"
    assert 'op_A' in transform_outputs, "Should have 'op_A' from C's TRANSFORM ability"
    assert 'b_A' in transform_outputs, "Should have 'b_A' from C's TRANSFORM ability"
    assert 'result_A' in transform_outputs, "Should have 'result_A' from C's TRANSFORM ability"
    # A's input is {a=1, b=2}, so A's stream output result = 1 + 2 = 3
    transform_result = transform_outputs.get('result_A')
    assert transform_result == 3, f"C's TRANSFORM result_A should be 3 (1+2), got {transform_result}"

    # Verify we received outputs from BOTH abilities (8 total fields)
    all_output_keys = set()
    for p in output_payloads:
        all_output_keys.update(p.keys())
    expected_keys = {'a', 'op', 'b', 'result', 'a_A', 'op_A', 'b_A', 'result_A'}
    assert expected_keys == all_output_keys, f"Should have all 8 output fields, got {all_output_keys}"


async def test_dual_ability_node_with_stream_error():
    """
    Test that when one stream ability (STREAM) fails, the error is properly propagated
    and the workflow fails gracefully.

    This test verifies error handling in dual-ability nodes:
    - When STREAM ability raises an exception, the workflow should fail
    - The exception should be wrapped in COMPONENT_EXECUTION_RUNTIME_ERROR
    """
    flow = Workflow()

    # Setup start node
    flow.set_start_comp("start", Start(),
                        inputs_schema={'a': '${user_inputs.a}', 'b': '${user_inputs.b}'})

    # Setup end node
    flow.set_end_comp("end", End(), response_mode="streaming",
                      stream_inputs_schema={'result': '${C.result}'})

    # Node A: STREAM ability - will provide stream input to C
    flow.add_workflow_comp('A', ComputeComponent2(),
                           inputs_schema={'a': '${start.a}', 'b': '${start.b}'},
                           comp_ability=[ComponentAbility.STREAM], wait_for_all=True)

    # Node B: INVOKE ability - will provide batch input to C
    flow.add_workflow_comp('B', ComputeComponent2(),
                           inputs_schema={'a': '${start.a}', 'b': '${start.b}'},
                           comp_ability=[ComponentAbility.INVOKE], wait_for_all=True)

    # Node C: TRANSFORM + STREAM abilities, with STREAM configured to fail
    flow.add_workflow_comp('C', DualAbilityWithErrorComponent(error_in_stream=True),
                           inputs_schema={'a': '${B.result}', 'b': '${B.result}'},
                           stream_inputs_schema={'data': {'a_A': '${A.a}'}},
                           comp_ability=[ComponentAbility.TRANSFORM, ComponentAbility.STREAM],
                           wait_for_all=True)

    # Setup connections
    flow.add_connection('start', 'A')
    flow.add_connection('start', 'B')
    flow.add_stream_connection('A', 'C')
    flow.add_connection('B', 'C')
    flow.add_stream_connection('C', 'end')

    # Execute workflow and expect exception
    user_inputs = {'user_inputs': {'a': 1, 'b': 2}}

    with pytest.raises(JiuWenBaseException) as exc_info:
        async for _ in flow.stream(user_inputs, session=WorkflowSession(), stream_modes=[BaseStreamMode.OUTPUT]):
            pass

    # Verify the exception is properly wrapped
    assert exc_info.value.error_code == StatusCode.WORKFLOW_COMPONENT_RUNTIME_ERROR.code
    assert "stream" in exc_info.value.message.lower()  # Ability name should be in the message


async def test_dual_ability_node_with_transform_error():
    """
    Test that when TRANSFORM ability fails, the error is properly propagated.

    This test verifies:
    - When TRANSFORM ability raises an exception, the workflow should fail
    - The exception should be wrapped in COMPONENT_EXECUTION_RUNTIME_ERROR
    """
    flow = Workflow()

    # Setup start node
    flow.set_start_comp("start", Start(),
                        inputs_schema={'a': '${user_inputs.a}', 'b': '${user_inputs.b}'})

    # Setup end node
    flow.set_end_comp("end", End(), response_mode="streaming",
                      stream_inputs_schema={'result': '${C.result}'})

    # Node A: STREAM ability - will provide stream input to C
    flow.add_workflow_comp('A', ComputeComponent2(),
                           inputs_schema={'a': '${start.a}', 'b': '${start.b}'},
                           comp_ability=[ComponentAbility.STREAM], wait_for_all=True)

    # Node B: INVOKE ability - will provide batch input to C
    flow.add_workflow_comp('B', ComputeComponent2(),
                           inputs_schema={'a': '${start.a}', 'b': '${start.b}'},
                           comp_ability=[ComponentAbility.INVOKE], wait_for_all=True)

    # Node C: TRANSFORM + STREAM abilities, with TRANSFORM configured to fail
    flow.add_workflow_comp('C', DualAbilityWithErrorComponent(error_in_transform=True),
                           inputs_schema={'a': '${B.result}', 'b': '${B.result}'},
                           stream_inputs_schema={'data': {'a_A': '${A.a}'}},
                           comp_ability=[ComponentAbility.TRANSFORM, ComponentAbility.STREAM],
                           wait_for_all=True)

    # Setup connections
    flow.add_connection('start', 'A')
    flow.add_connection('start', 'B')
    flow.add_stream_connection('A', 'C')
    flow.add_connection('B', 'C')
    flow.add_stream_connection('C', 'end')

    # Execute workflow and expect exception
    user_inputs = {'user_inputs': {'a': 1, 'b': 2}}

    with pytest.raises(JiuWenBaseException) as exc_info:
        async for _ in flow.stream(user_inputs, session=WorkflowSession(), stream_modes=[BaseStreamMode.OUTPUT]):
            pass

    # Verify the exception is properly wrapped
    assert exc_info.value.error_code == StatusCode.WORKFLOW_COMPONENT_RUNTIME_ERROR.code
    assert "transform" in exc_info.value.message.lower()  # Ability name should be in the message


class StreamNode(WorkflowComponent):

    def __init__(self, delay: float = 0.0):
        self.delay = delay

    async def stream(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        await asyncio.sleep(self.delay)
        for i in range(0, 10):
            await asyncio.sleep(0.01)
            yield {"output": i}


async def test_stream_trigger_consumer_twice():
    wf_id = "llm_workflow"
    name = "llm_workflow"
    version = "0.0.1"
    inputs_schema_dict = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "用户输入信息",
            }
        }
    }
    workflow_inputs_schema = inputs_schema_dict
    workflow_card = WorkflowCard(name=name, id=wf_id, version=version, inputs_schema=workflow_inputs_schema)
    flow = Workflow(card=workflow_card)

    start_component = Start(
        {
            "inputs": [
                {"id": "query", "type": "String", "required": "true", "sourceType": "ref"}
            ]
        }
    )
    end_component = End({"responseTemplate": "123"})

    flow.set_start_comp("s", start_component, inputs_schema={"query": "${query}"})
    flow.set_end_comp("e", end_component,
                      stream_inputs_schema={"output": "${llm.output}", "output2": "${llm2.output}"},
                      response_mode="streaming")
    flow.add_workflow_comp("llm", StreamNode(0), inputs_schema={"query": "${s.query}"})
    flow.add_workflow_comp("llm2", StreamNode(0.5), inputs_schema={"query": "${s.query}"})

    flow.add_connection("s", "llm")
    flow.add_connection("s", "llm2")
    flow.add_stream_connection("llm", "e")
    flow.add_stream_connection("llm2", "e")

    async for chunk in flow.stream({"query": "请介绍一下你自己！"}, WorkflowSession()):
        logger.info(chunk)


async def test_stream_trigger_consumer():
    wf_id = "llm_workflow"
    name = "llm_workflow"
    version = "0.0.1"
    inputs_schema_dict = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "用户输入信息",
            }
        }
    }
    workflow_inputs_schema = inputs_schema_dict
    workflow_card = WorkflowCard(name=name, id=wf_id, version=version, inputs_schema=workflow_inputs_schema)
    flow = Workflow(card=workflow_card)

    start_component = Start(
        {
            "inputs": [
                {"id": "query", "type": "String", "required": "true", "sourceType": "ref"}
            ]
        }
    )
    end_component = End({"responseTemplate": "123"})

    flow.set_start_comp("s", start_component, inputs_schema={"query": "${query}"})
    flow.set_end_comp("e", end_component,
                      stream_inputs_schema={"output": "${llm.output}"},
                      response_mode="streaming")
    flow.add_workflow_comp("llm", StreamNode(), inputs_schema={"query": "${s.query}"})

    flow.add_connection("s", "llm")
    flow.add_stream_connection("llm", "e")

    async for chunk in flow.stream({"query": "请介绍一下你自己！"}, WorkflowSession()):
        logger.info(chunk)
