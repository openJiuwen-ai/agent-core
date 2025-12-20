import asyncio

import pytest

from openjiuwen.core.common.constants.constant import CONFIG_KEY
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.component.base import WorkflowComponent
from openjiuwen.core.component.branch_comp import BranchComponent
from openjiuwen.core.component.branch_router import BranchRouter
from openjiuwen.core.component.end_comp import End
from openjiuwen.core.component.start_comp import Start
from openjiuwen.core.context_engine.base import Context
from openjiuwen.core.runtime.base import Input, Output, ComponentExecutable
from openjiuwen.core.runtime.runtime import Runtime
from openjiuwen.core.runtime.workflow import WorkflowRuntime
from openjiuwen.core.workflow.base import Workflow
from openjiuwen.core.workflow.workflow_config import WorkflowConfig
from tests.unit_tests.core.workflow.mock_nodes import MockStartNode, Node1, CommonNode

pytestmark = pytest.mark.asyncio

SUB_WORKFLOW_COMPONENT = "sub_workflow"

class MockSubWorkflowComponent(WorkflowComponent, ComponentExecutable):
    def __init__(self):
        super().__init__()

    async def invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        results = []
        for i in range (0,8):
            workflow = self.sub_workflow()
            results.append(await workflow.sub_invoke({"a": "1", "b": 2}, runtime.base(), inputs.get(CONFIG_KEY)))
        output = {"results": results}
        print(output)
        return output


    def graph_invoker(self) -> bool:
        return True

    def component_type(self) -> str:
        return SUB_WORKFLOW_COMPONENT

    def sub_workflow(self) -> Workflow:
        flow = Workflow(workflow_config=WorkflowConfig())
        flow.set_start_comp("start", MockStartNode("start"),
                            inputs_schema={"a": "${a}",
                                           "b": "${b}",
                                           "c": 1,
                                           "d": [1, 2, 3]})

        router = BranchRouter()
        router.add_branch("len(${start.d}) > 2", "a")
        router.add_branch("len(${start.d}) < 2", "b")

        flow.add_conditional_connection("start", router=router)
        flow.add_workflow_comp("a", Node1("a"), inputs_schema={"a": "${start.a}"})
        flow.add_workflow_comp("b", Node1("b"), inputs_schema={"b": "${start.b}"})
        flow.set_end_comp("end", End(), {"result1": "${a.a}", "result2": "${b.b}"})
        flow.add_connection("a", "end")
        flow.add_connection("b", "end")
        return flow


class TestBranchComponent:
    async def test_sub_workflow_with_branch(self):
        workflow = Workflow()
        workflow.set_start_comp("s", Start(), inputs_schema={"input": "${data}"})
        workflow.add_workflow_comp("sub_workflow", MockSubWorkflowComponent())
        workflow.set_end_comp("e", End(), inputs_schema={"end_out": "${print_inputs}"})

        workflow.add_connection("s", "sub_workflow")
        workflow.add_connection("sub_workflow", "e")

        inputs = {"data":'aaa'}
        results = await workflow.invoke(inputs, WorkflowRuntime())
        print(results)

    async def test_add_branch_error(self):
        branch = BranchComponent()
        with pytest.raises(JiuWenBaseException):
            branch.add_branch(condition=None, target="a", branch_id='')
        with pytest.raises(JiuWenBaseException):
            branch.add_branch(condition="sss", target='', branch_id='')
        with pytest.raises(JiuWenBaseException):
            branch.add_branch(condition="sss", target=None, branch_id='')
        with pytest.raises(JiuWenBaseException):
            branch.add_branch(condition="sss", target=['', "xxx"], branch_id='')
        with pytest.raises(JiuWenBaseException):
            branch.add_branch(condition="sss", target=["xxx", None], branch_id='')


    async def runtime_with_expression(self, expression, value):
        workflow = Workflow()
        workflow.set_start_comp("start", Start(), inputs_schema={"input": "${data}"})
        branch_comp = BranchComponent()
        branch_comp.add_branch(condition=expression, target=["print_inputs"])
        workflow.add_workflow_comp("branch_component", branch_comp)
        workflow.add_workflow_comp("print_inputs", Start(), inputs_schema={"data": "${start}"})
        workflow.set_end_comp("end", End(), inputs_schema={"end_out": "${print_inputs}"})

        workflow.add_connection("start", "branch_component")
        workflow.add_connection("print_inputs", "end")

        inputs = {"data": value}
        print(await workflow.invoke(inputs, WorkflowRuntime()))

    async def test_expression_is_empty(self):
       await self.runtime_with_expression("is_empty(${start.input})", None)
       await  self.runtime_with_expression("is_empty(${start.input})", [])
       await  self.runtime_with_expression("is_empty(${start.input})", '')
       await self.runtime_with_expression("is_empty(${start.input})", {})
       with pytest.raises(JiuWenBaseException) as error:
           await self.runtime_with_expression("is_empty(${start.input})", 0)
       assert error.value.error_code == StatusCode.EXPRESSION_CONDITION_EVAL_ERROR.code
       print(error.value)

       with pytest.raises(JiuWenBaseException) as error:
           await self.runtime_with_expression("is_not_empty(${start.input})", 1.2)
       assert error.value.error_code == StatusCode.EXPRESSION_CONDITION_EVAL_ERROR.code
       print(error.value)

       await self.runtime_with_expression("is_empty(${start.input}[0])", [None, 'y'])
       await self.runtime_with_expression("is_empty(${start.input}['x'])", {'x': None})
       await self.runtime_with_expression("is_empty(${start.input}['x'][0])", {'x': [None]})

    async def test_expression_is_not_empty(self):
        await self.runtime_with_expression("is_not_empty(${start.input})", 'x')
        await self.runtime_with_expression("is_not_empty(${start.input})", {'a':'a'})
        await self.runtime_with_expression("is_not_empty(${start.input})", ['a'])
        await self.runtime_with_expression("is_not_empty(${start.input})", (1,2))
        with pytest.raises(JiuWenBaseException) as error:
            await self.runtime_with_expression("is_not_empty(${start.input})", None)
        print(error.value)
        assert error.value.error_code == StatusCode.BRANCH_COMPONENT_BRANCH_NOT_FOUND_ERROR.code

        with pytest.raises(JiuWenBaseException) as error:
            await self.runtime_with_expression("is_not_empty(${start.input})", 1.2)
        assert error.value.error_code == StatusCode.EXPRESSION_CONDITION_EVAL_ERROR.code
        print(error.value)

        await self.runtime_with_expression("is_not_empty(${start.input}[0])", ['x', 'y'])
        await self.runtime_with_expression("is_not_empty(${start.input}['x'])", {'x' : 'x'})
        await self.runtime_with_expression("is_not_empty(${start.input}['x'][0])", {'x' : ['x']})

    async def test_expression_length(self):
        with pytest.raises(JiuWenBaseException) as error:
            await self.runtime_with_expression("length(${start.input}) == 0", 0)
        assert error.value.error_code == StatusCode.EXPRESSION_CONDITION_EVAL_ERROR.code
        print(error.value)
        await self.runtime_with_expression("length(${start.input}) == 0", {})
        await self.runtime_with_expression("length(${start.input}) == 0", [])
        await self.runtime_with_expression("length(${start.input}) == 0", '')
        await self.runtime_with_expression("length(${start.input}) == 0", ())

    # async def test_branch_condition(self):
    #     workflow = Workflow()
    #     workflow.set_start_comp("start", Start(), inputs_schema={"input3": "${data3}", "input4": "${data4}"})
    #
    #     branch_comp = BranchComponent()
    #     branch_comp.add_branch(condition="(${start.input5}) || (${start.input4.k})", target=["end"])
    #     branch_comp.add_branch(condition="${start.input4.k3}", target=["end"])
    #     workflow.add_workflow_comp("branch_component", branch_comp)
    #
    #     workflow.add_workflow_comp("print_inputs", CommonNode("print_inputs"), inputs_schema={"data": "${start}"})
    #     workflow.set_end_comp("end", End(), inputs_schema={"end_out": "${print_inputs}"})
    #
    #     workflow.add_connection("start", "branch_component")
    #     workflow.add_connection("print_inputs", "end")
    #
    #     inputs = {"data4": {"k2": {"k": "v"}, "k3": {"k": True, "arr": [1]}}}
    #     print(await workflow.invoke(inputs, WorkflowRuntime()))