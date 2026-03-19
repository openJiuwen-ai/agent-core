from typing import AsyncIterator

import pytest

from openjiuwen.core.common.exception.errors import BaseError, WorkflowError
from openjiuwen.core.workflow import Input, Output
from openjiuwen.core.workflow import End
from openjiuwen.core.workflow import Start
from openjiuwen.core.workflow import WorkflowComponent
from openjiuwen.core.workflow.components.flow.workflow_comp import SubWorkflowComponent
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.workflow.components import Session
from openjiuwen.core.workflow import create_workflow_session
from openjiuwen.core.session.stream import BaseStreamMode, OutputSchema
from openjiuwen.core.workflow import Workflow

pytestmark = pytest.mark.asyncio


class CustomStream(WorkflowComponent):
    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        return {'custom_output': inputs}

    async def stream(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        if inputs is None:
            yield 1
        else:
            for index in inputs.get("value"):
                yield {"value": "stream_{}".format(index)}

    async def transform(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        values = inputs.get("value")
        async for item in values:
            yield {"value": "transform_{}".format(item)}


class BatchConsumerComponent(WorkflowComponent):
    """Component that consumes batch output from sub_workflow"""
    last_invoked_inputs = None

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        # This should receive the output from sub_workflow_comp via template
        result = inputs.get("result") if inputs else None
        BatchConsumerComponent.last_invoked_inputs = inputs
        return {"consumed_result": result}


class TestSubWorkflowComp:
    async def test_add_component(self):
        main_workflow = Workflow(workflow_max_nesting_depth=2)
        main_workflow.set_start_comp("start", Start())
        main_workflow.add_workflow_comp("fick_comp", SubWorkflowComponent(main_workflow))
        main_workflow.set_end_comp("end", End())
        main_workflow.add_connection("start", 'fick_comp')
        main_workflow.add_connection('fick_comp', "end")
        with pytest.raises(BaseError):
            await main_workflow.invoke(inputs={}, session=create_workflow_session())

    def create_nesting_workflow(self, sub_workflow_depth=0, **kwargs):
        workflow = Workflow(**kwargs)
        workflow.set_start_comp("start", Start())
        if sub_workflow_depth > 0:
            workflow.add_workflow_comp(f'sub{sub_workflow_depth}',
                                       SubWorkflowComponent(self.create_nesting_workflow(sub_workflow_depth - 1)))
        workflow.set_end_comp("end", End())
        if sub_workflow_depth > 0:
            workflow.add_connection("start", f'sub{sub_workflow_depth}')
            workflow.add_connection(f'sub{sub_workflow_depth}', "end")
        else:
            workflow.add_connection("start", "end")
        return workflow

    async def test_sub_invoke(self):
        with pytest.raises(WorkflowError) as err:
            main_workflow = self.create_nesting_workflow(3, workflow_max_nesting_depth=1)
            await main_workflow.invoke(inputs={}, session=create_workflow_session())

        assert f"workflow nesting hierarchy is too big, must <= 1" in str(err.value)
        main_workflow = self.create_nesting_workflow(3, workflow_max_nesting_depth=3)

        await main_workflow.invoke(inputs={}, session=create_workflow_session())

        main_workflow = self.create_nesting_workflow(0, workflow_max_nesting_depth=0)

        await main_workflow.invoke(inputs={}, session=create_workflow_session())

    async def test_workflow(self):
        sub_workflow = Workflow()
        sub_workflow.set_start_comp("sub_start", Start())
        sub_workflow.add_workflow_comp("custom", CustomStream(), inputs_schema={"value": "123"})
        sub_workflow.add_workflow_comp("custom1", CustomStream(), stream_inputs_schema={"value": "${custom.value}"})
        sub_workflow.set_end_comp("sub_end", End(), response_mode="streaming",
                                  stream_inputs_schema={'out': '${custom1.value}'})
        sub_workflow.add_connection("sub_start", "custom")
        sub_workflow.add_stream_connection("custom", "custom1")
        sub_workflow.add_stream_connection("custom1", "sub_end")

        main_workflow = Workflow()
        sub_workflow_comp = SubWorkflowComponent(sub_workflow)
        main_workflow.set_start_comp("start", Start(), inputs_schema={})
        main_workflow.add_workflow_comp("sub_workflow_comp", sub_workflow_comp, inputs_schema={})
        main_workflow.set_end_comp("end", End(), response_mode="streaming",
                                   stream_inputs_schema={'result': '${sub_workflow_comp.output.out}'})
        main_workflow.add_connection("start", "sub_workflow_comp")
        main_workflow.add_stream_connection("sub_workflow_comp", "end")
        chunks = []
        expect_chunks = [
            OutputSchema(type='end node stream', index=0, payload={'output': {'result': 'transform_stream_1'}}),
            OutputSchema(type='end node stream', index=1, payload={'output': {'result': 'transform_stream_2'}}),
            OutputSchema(type='end node stream', index=2, payload={'output': {'result': 'transform_stream_3'}})]

        async for chunk in main_workflow.stream(inputs={}, session=create_workflow_session(),
                                                stream_modes=[BaseStreamMode.OUTPUT]):
            chunks.append(chunk)

        assert chunks == expect_chunks

    async def test_workflow_with_llm_stream_and_batch_edge(self):
        """Test sub_workflow with LLM stream output and batch edge.

        Sub workflow: Start -> CustomStream (stream) -> End (streaming)
        Main workflow: Start -> SubWorkflowComp (stream) -> BatchConsumer (invoke) -> End (streaming)

        Verify batch consumer gets all stream messages.
        """
        # Reset for test
        BatchConsumerComponent.last_invoked_inputs = None

        # Use CustomStream which mimics LLM stream behavior
        sub_workflow = Workflow()
        sub_workflow.set_start_comp("sub_start", Start())
        sub_workflow.add_workflow_comp("custom", CustomStream(), inputs_schema={"value": "123"})
        sub_workflow.add_workflow_comp("custom1", CustomStream(), stream_inputs_schema={"value": "${custom.value}"})
        sub_workflow.set_end_comp("sub_end", End(), response_mode="streaming",
                                  stream_inputs_schema={'out': '${custom1.value}'})
        sub_workflow.add_connection("sub_start", "custom")
        sub_workflow.add_stream_connection("custom", "custom1")
        sub_workflow.add_stream_connection("custom1", "sub_end")

        # Main workflow with stream and batch edges
        main_workflow = Workflow()
        sub_workflow_comp = SubWorkflowComponent(sub_workflow, cache_stream=True)
        main_workflow.set_start_comp("start", Start(), inputs_schema={})
        main_workflow.add_workflow_comp("sub_workflow_comp", sub_workflow_comp, inputs_schema={})
        main_workflow.set_end_comp("end", End(), response_mode="streaming",
                                   stream_inputs_schema={'result': '${sub_workflow_comp.output.out}'})
        # Batch consumer
        main_workflow.add_workflow_comp("batch_consumer", BatchConsumerComponent(),
                                        inputs_schema={'result': '${sub_workflow_comp.output}'})
        main_workflow.add_connection("start", "sub_workflow_comp")
        main_workflow.add_connection("sub_workflow_comp", "batch_consumer")
        main_workflow.add_connection("batch_consumer", "end")
        # Stream edge
        main_workflow.add_stream_connection("sub_workflow_comp", "end")

        # Stream and verify
        chunks = []
        async for chunk in main_workflow.stream(inputs={}, session=create_workflow_session(),
                                                stream_modes=[BaseStreamMode.OUTPUT]):
            chunks.append(chunk)

        # Verify stream output
        assert len(chunks) == 3

        # Verify batch consumer received merged stream output
        # The result is a dict with merged values from all accumulated frames
        # Input: [{'output': {'out': 'transform_stream_1'}}, ...]
        # Output: {'out': 'transform_stream_1transform_stream_2transform_stream_3'}
        batch_result = BatchConsumerComponent.last_invoked_inputs.get("result")
        assert batch_result is not None
        # The result should be a dict with merged string values
        assert isinstance(batch_result, dict)
        assert "out" in batch_result
        # Values should be merged into a single string
        assert batch_result["out"] == "transform_stream_1transform_stream_2transform_stream_3"

    async def test_workflow_with_stream_and_batch_edge(self):
        """Test sub_workflow with both stream edge and batch edge.

        When sub_workflow has:
        - Stream edge: sub_workflow -> End (streaming)
        - Batch edge: sub_workflow -> BatchConsumer (batch)

        The batch consumer should be able to get the output when cache_stream=True.
        """
        # Create sub_workflow: Start -> CustomStream -> End (streaming with template)
        sub_workflow = Workflow()
        sub_workflow.set_start_comp("sub_start", Start())
        sub_workflow.add_workflow_comp("custom", CustomStream(), inputs_schema={"value": "123"})
        sub_workflow.add_workflow_comp("custom1", CustomStream(), stream_inputs_schema={"value": "${custom.value}"})
        sub_workflow.set_end_comp("sub_end", End(), response_mode="streaming",
                                  stream_inputs_schema={'out': '${custom1.value}'})
        sub_workflow.add_connection("sub_start", "custom")
        sub_workflow.add_stream_connection("custom", "custom1")
        sub_workflow.add_stream_connection("custom1", "sub_end")

        # Create main_workflow with both stream and batch edges
        main_workflow = Workflow()
        # Use cache_stream=True to enable batch output caching
        sub_workflow_comp = SubWorkflowComponent(sub_workflow, cache_stream=True)
        main_workflow.set_start_comp("start", Start(), inputs_schema={})
        main_workflow.add_workflow_comp("sub_workflow_comp", sub_workflow_comp, inputs_schema={})
        main_workflow.set_end_comp("end", End(), response_mode="streaming",
                                   stream_inputs_schema={'result': '${sub_workflow_comp.output.out}'})
        # Add batch consumer component
        main_workflow.add_workflow_comp("batch_consumer", BatchConsumerComponent(),
                                        inputs_schema={'result': '${sub_workflow_comp.output}'})
        main_workflow.add_connection("start", "sub_workflow_comp")
        main_workflow.add_connection("sub_workflow_comp", "batch_consumer")
        main_workflow.add_connection("batch_consumer", "end")
        # Add stream edge
        main_workflow.add_stream_connection("sub_workflow_comp", "end")

        # Stream and verify
        chunks = []
        expect_chunks = [
            OutputSchema(type='end node stream', index=0, payload={'output': {'result': 'transform_stream_1'}}),
            OutputSchema(type='end node stream', index=1, payload={'output': {'result': 'transform_stream_2'}}),
            OutputSchema(type='end node stream', index=2, payload={'output': {'result': 'transform_stream_3'}})]

        async for chunk in main_workflow.stream(inputs={}, session=create_workflow_session(),
                                                stream_modes=[BaseStreamMode.OUTPUT]):
            chunks.append(chunk)

        assert chunks == expect_chunks
