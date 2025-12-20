from typing import AsyncIterator

import pytest

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.component.base import WorkflowComponent
from openjiuwen.core.component.end_comp import End
from openjiuwen.core.component.start_comp import Start
from openjiuwen.core.component.workflow_comp import SubWorkflowComponent
from openjiuwen.core.context_engine.base import Context
from openjiuwen.core.runtime.base import ComponentExecutable, Input, Output
from openjiuwen.core.runtime.runtime import Runtime
from openjiuwen.core.runtime.workflow import WorkflowRuntime
from openjiuwen.core.stream.base import BaseStreamMode, OutputSchema
from openjiuwen.core.workflow.base import Workflow
from openjiuwen.core.workflow.workflow_config import WorkflowConfig

pytestmark = pytest.mark.asyncio


class CustomStream(ComponentExecutable, WorkflowComponent):
    async def invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        return {'custom_output': inputs}

    async def stream(self, inputs: Input, runtime: Runtime, context: Context) -> AsyncIterator[Output]:
        if inputs is None:
            yield 1
        else:
            for index in inputs.get("value"):
                yield {"value": "stream_{}".format(index)}

    async def transform(self, inputs: Input, runtime: Runtime, context: Context) -> AsyncIterator[Output]:
        values = inputs.get("value")
        async for item in values:
            yield {"value": "tranform_{}".format(item)}


class TestSubWorkflowComp:
    async def test_add_component(self):
        main_workflow = Workflow(WorkflowConfig(workflow_max_nesting_depth=2))
        main_workflow.set_start_comp("start", Start())
        main_workflow.add_workflow_comp("fick_comp", SubWorkflowComponent(main_workflow))
        main_workflow.set_end_comp("end", End())
        main_workflow.add_connection("start", 'fick_comp')
        main_workflow.add_connection('fick_comp', "end")
        with pytest.raises(JiuWenBaseException):
            await main_workflow.invoke(inputs={}, runtime=WorkflowRuntime())

    def create_nesting_workflow(self, sub_workflow_depth=0, workflow_config=None):
        workflow = Workflow(workflow_config)
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
        with pytest.raises(JiuWenBaseException) as err:
            workflow_config = WorkflowConfig(workflow_max_nesting_depth=1)
            main_workflow = self.create_nesting_workflow(3, workflow_config)
            await main_workflow.invoke(inputs={}, runtime=WorkflowRuntime())
        assert err.value.message == StatusCode.COMPONENT_EXECUTE_ERROR.errmsg.format(node_id="sub2",
             ability="invoke",
             error=StatusCode.SUB_WORKFLOW_COMPONENT_RUNNING_ERROR.errmsg.format(
                 detail='workflow nesting hierarchy is too big, must <= 1'))

        workflow_config = WorkflowConfig(workflow_max_nesting_depth=3)
        main_workflow = self.create_nesting_workflow(3, workflow_config)

        await main_workflow.invoke(inputs={}, runtime=WorkflowRuntime())

        workflow_config = WorkflowConfig(workflow_max_nesting_depth=0)
        main_workflow = self.create_nesting_workflow(0, workflow_config)

        await main_workflow.invoke(inputs={}, runtime=WorkflowRuntime())

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
            OutputSchema(type='end node stream', index=0, payload={'output': {'result': 'tranform_stream_1'}}),
            OutputSchema(type='end node stream', index=1, payload={'output': {'result': 'tranform_stream_2'}}),
            OutputSchema(type='end node stream', index=2, payload={'output': {'result': 'tranform_stream_3'}})]

        async for chunk in main_workflow.stream(inputs={}, runtime=WorkflowRuntime(),
                                                stream_modes=[BaseStreamMode.OUTPUT]):
            chunks.append(chunk)

        assert chunks == expect_chunks
