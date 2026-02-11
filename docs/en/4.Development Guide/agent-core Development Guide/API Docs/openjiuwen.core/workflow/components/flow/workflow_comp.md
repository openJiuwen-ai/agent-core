# openjiuwen.core.workflow

## class SubWorkflowComponent

```python
class SubWorkflowComponent(sub_workflow: Workflow)
```

`SubWorkflowComponent` is openJiuwen's built-in sub-workflow component, this component provides ability to embed sub-workflows, used to implement hierarchical management and flexible composition of complex business logic in workflows. When execution reaches sub-workflow component, this component will pass input data and configuration information to sub-workflow and start sub-workflow execution. After sub-workflow execution completes, will return results and continue executing main workflow.

**Parameters**:

* **sub_workflow** ([Workflow](../../workflow.md#class-workflow)): Sub-workflow instance, cannot be `None`.


**Example**:

```python
>>> import asyncio
>>> 
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.workflow.components import Session
>>> 
>>> from openjiuwen.core.workflow import Input, Output, SubWorkflowComponent, WorkflowCard, WorkflowComponent, Start, \
...     Workflow, End, create_workflow_session
... 
>>> 
>>> class CustomComponent(WorkflowComponent):
...     """Custom component"""
... 
...     def __init__(self):
...         super().__init__()
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         """Process input and return user query"""
...         query = inputs.get("query", "")
...         return {
...             "result": query
...         }
>>> 
>>> 
>>> # Initialize sub-workflow
>>> sub_workflow = Workflow()
>>> 
>>> # Add 3 components with IDs sub_start, sub_node, sub_end to sub-workflow
>>> sub_workflow.set_start_comp("sub_start", Start(), inputs_schema={"query": "${query}"})
>>> sub_workflow.add_workflow_comp("sub_custom_comp", CustomComponent(), inputs_schema={"query": "${sub_start.query}"})
>>> sub_workflow.set_end_comp("sub_end", End(), inputs_schema={"result": "${sub_custom_comp.result}"})
>>> 
>>> # Set sub-workflow topology connections, sub_start -> sub_custom_comp -> sub_end
>>> sub_workflow.add_connection("sub_start", "sub_custom_comp")
>>> sub_workflow.add_connection("sub_custom_comp", "sub_end")
>>> 
>>> # Initialize main workflow
>>> main_workflow = Workflow(card=WorkflowCard(id="test_workflow"))
>>> 
>>> # Use previously constructed sub-workflow instance to construct workflow execution component
>>> sub_workflow_comp = SubWorkflowComponent(sub_workflow)
>>> 
>>> # Add 3 components with IDs start, sub_workflow_comp, end to main workflow
>>> main_workflow.set_start_comp("start", Start(), inputs_schema={"query": "${user_inputs.query}"})
>>> main_workflow.add_workflow_comp("sub_workflow_comp", sub_workflow_comp, inputs_schema={"query": "${start.query}"})
>>> main_workflow.set_end_comp("end", End(), inputs_schema={"result": "${sub_workflow_comp.output.result}"})
>>> 
>>> # Set main workflow topology connections, start -> sub_workflow_comp -> end
>>> main_workflow.add_connection("start", "sub_workflow_comp")
>>> main_workflow.add_connection("sub_workflow_comp", "end")
>>> 
>>> 
>>> async def run_workflow():
...     # Create workflow runtime
...     runtime = create_workflow_session(session_id="test_session")
... 
...     # Construct input
...     inputs = {"user_inputs": {"query": "hello"}}
... 
...     # Invoke workflow
...     result = await main_workflow.invoke(inputs, runtime)
...     return result
... 
>>> 
>>> res = asyncio.run(run_workflow())
>>> print(f"main workflow with sub workflow run result: {res}")
main workflow with sub workflow run result: result={'output': {'result': 'hello'}} state=<WorkflowExecutionState.COMPLETED: 'COMPLETED'>
```
