# openjiuwen.core.session

## class openjiuwen.core.session.InteractiveInput

```python
class openjiuwen.core.session.InteractiveInput(raw_inputs: Any)
```

Construct user interaction input with workflows in interruption scenarios.

**Parameters:**

- **raw_inputs** (Any): User interaction input information, which is also returned when the workflow resumes from interruption. Passing None will raise an exception.

**Exceptions:**

- **JiuWenBaseException**: openJiuwen base exception class. For detailed information and solutions, see [StatusCode](../common/exception/status_code.md).

**Example:**

```python
>>> import asyncio
>>> import uuid

>>> from openjiuwen.core.workflow import WorkflowComponent
>>> from openjiuwen.core.workflow import End
>>> from openjiuwen.core.workflow import Start
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.workflow import Input, Output
>>> from openjiuwen.core.session import InteractionOutput
>>> from openjiuwen.core.session import InteractiveInput
>>> from openjiuwen.core.workflow.components import Session
>>> from openjiuwen.core.workflow import create_workflow_session
>>> from openjiuwen.core.workflow import Workflow
>>> 
>>> 
>>> # Create a workflow component with interruption interaction
>>> class InteractiveNode(WorkflowComponent):
...     def __init__(self):
...         super().__init__()
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         cmd = inputs.get("cmd")
...         # Ask the user and wait for their answer
...         user_input_ = await session.interact(f"Do you want to execute the command '{cmd}'?")
...         return {"result": user_input_}
...  
>>> # Create workflow, execute workflow to trigger interruption interaction flow
>>> flow = Workflow()
>>> flow.set_start_comp("start", Start(), inputs_schema={"command1": "${cmd1}"})
>>> flow.add_workflow_comp("interactive_node1", InteractiveNode(), inputs_schema={"cmd": "${start.command1}"})
>>> flow.set_end_comp("end", End(), inputs_schema={"result1": "${interactive_node1.result}"})
>>> flow.add_connection("start", "interactive_node1")
>>> flow.add_connection("interactive_node1", "end")
>>> 
>>> session_id = uuid.uuid4().hex
>>> 
>>> # Execute workflow
>>> output = asyncio.run(flow.invoke({"cmd1": "delete all files"}, create_workflow_session(session_id=session_id)))
>>> 
>>> # Check if result is interruption interaction output
>>> if isinstance(output.result[0].payload, InteractionOutput):
...     # Get interruption component's prompt message
...     print(output.result[0].payload.value)
...
Do you want to execute the command 'delete all files'?
>>> 
>>> # Create `InteractiveInput` for interaction input and resume workflow execution
>>> 
>>> user_input = InteractiveInput(raw_inputs='Yes')
>>> output = asyncio.run(flow.invoke(user_input, create_workflow_session(session_id=session_id)))
>>> print(output.result.get("output").get("result1"))
Yes
```

> **Note**
>
> - output is the workflow output, see [Workflow Output Introduction](../workflow/workflow.md) for details.
> - The first workflow output is `Do you want to execute the command '{delete all files}'`. At this point, the result in output is a list, where each element is a streaming output element. See [Streaming Output Introduction](../../../../agent-core Development Guide/Advanced Usage/Session/Streaming Output.md) for details. The content in payload is the interruption output. See [Interruption Output Introduction](#class-openjiuwencoresessioninteractionoutput) for details.
> - After constructing user interaction input through `InteractiveInput`, the next output is `Yes`. At this point, the result in output is the end component's output. See [End Component Introduction](../workflow/components/flow/end_comp.md) for details.

### update

```python
update(self, node_id: str, value: Any)
```

Provide user interaction input information for a specified component. Generally used for workflows with multiple interruption recovery components. When an interaction interruption occurs, different interaction information needs to be provided for each interruption component. If already constructed through the constructor and `raw_inputs`, calling this interface will raise an exception.

**Parameters:**

- **node_id** (str): ID of the interruption component. Passing None will raise an exception.
- **value** (Any): User input, which will be returned by the [interact] interface. Passing None will raise an exception.

**Exceptions:**

- **JiuWenBaseException**: openJiuwen base exception class. For detailed information and solutions, see [StatusCode](../common/exception/status_code.md).

**Example:**

```python
>>> import asyncio
>>> import uuid
>>> 
>>> from openjiuwen.core.workflow import WorkflowComponent
>>> from openjiuwen.core.workflow import End
>>> from openjiuwen.core.workflow import Start
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.workflow import Input, Output
>>> from openjiuwen.core.session import InteractiveInput
>>> from openjiuwen.core.workflow.components import Session
>>> from openjiuwen.core.workflow import create_workflow_session
>>> from openjiuwen.core.workflow import Workflow
>>> 
>>> # Create a component with interruption interaction functionality
>>> class InteractiveNode(WorkflowComponent):
...     def __init__(self):
...         super().__init__()
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         cmd = inputs.get("cmd")
...         # Ask the user and wait for their answer
...         user_input_ = await session.interact(f"Do you want to execute the command '{cmd}'?")
...         return {"result": user_input_}
... 
>>> # Build a workflow with two interruption interaction functionalities, execute workflow to trigger interruption flow for both components
>>> flow = Workflow()
>>> flow.set_start_comp("start", Start(), inputs_schema={"command1": "${cmd1}", "command2": "${cmd2}"})
>>> flow.add_workflow_comp("interactive_node1", InteractiveNode(), inputs_schema={"cmd": "${start.command1}"})
>>> flow.add_workflow_comp("interactive_node2", InteractiveNode(), inputs_schema={"cmd": "${start.command2}"})
>>> flow.set_end_comp("end", End(),inputs_schema={"result1": "${interactive_node1.result}", "result2": "${interactive_node2.result}"})
>>> flow.add_connection("start", "interactive_node1")
>>> flow.add_connection("start", "interactive_node2")
>>> flow.add_connection("interactive_node1", "end")
>>> flow.add_connection("interactive_node2", "end")
>>> 
>>> session_id = uuid.uuid4().hex
>>> output = asyncio.run(flow.invoke({"cmd1": "delete all files", "cmd2": "kill all processes"}, create_workflow_session(session_id=session_id)))
>>> 
>>> node_id_1 = output.result[0].payload.id
>>> print(node_id_1)
interactive_node1
>>> print(output.result[0].payload.value)
Do you want to execute the command 'delete all files'?
>>> 
>>> node_id_2 = output.result[1].payload.id
>>> print(node_id_2)
interactive_node2
>>> print(output.result[1].payload.value)
Do you want to execute the command 'kill all processes'?
>>> 
>>> # Provide different interaction input information for interruption components
>>> print("----------Round 2------------")
----------Round 2------------
>>> user_input = InteractiveInput()
>>> user_input.update(node_id_1, "Yes")
>>> user_input.update(node_id_2, "No")
>>> 
>>> output = asyncio.run(flow.invoke(user_input, create_workflow_session(session_id=session_id)))
>>> print(output.result.get("output"))
{'result1': 'Yes', 'result2': 'No'}
>>> 
```

> **Note**
>
> - output is the workflow output, see [Workflow Output Introduction](../workflow/workflow.md) for details.
> - The first workflow output contains `interactive_node1`, `Do you want to execute the command 'delete all files'?`, `interactive_node2`, `Do you want to execute the command 'kill all processes'?`. At this point, the result in output is a list, where each element is a streaming output element. See [Streaming Output Introduction](../../../../agent-core Development Guide/Advanced Usage/Session/Streaming Output.md) for details. The content in payload is the interruption output. See [Interruption Output Introduction](#class-openjiuwencoresessioninteractionoutput) for details.
> - After constructing user interaction input through `InteractiveInput`, the next output is `{'result1': 'Yes', 'result2': 'No'}`. At this point, the result in output is the end component's output. See [End Component Introduction](../workflow/components/flow/end_comp.md) for details.

## class openjiuwen.core.session.InteractionOutput

```python
class openjiuwen.core.session.InteractionOutput
```

Data class for workflow-user interaction output, mainly used in interruption scenarios. Generally used as the `payload` of [WorkflowChunk](../workflow/workflow.md).

- **id** (str): ID of the interruption component.
- **value** (Any): Prompt message when the interruption component triggers an interruption.

**Example:**

```python
>>> import asyncio
>>> import uuid
>>> 
>>> from openjiuwen.core.workflow import WorkflowComponent
>>> from openjiuwen.core.workflow import End
>>> from openjiuwen.core.workflow import Start
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.workflow import Input, Output
>>> from openjiuwen.core.session import InteractionOutput
>>> from openjiuwen.core.workflow.components import Session
>>> from openjiuwen.core.workflow import create_workflow_session
>>> from openjiuwen.core.workflow import Workflow
>>> 
>>> 
>>> # Create a workflow component with interruption interaction
>>> class InteractiveNode(WorkflowComponent):
...     def __init__(self):
...         super().__init__()
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         cmd = inputs.get("cmd")
...         # Ask the user and wait for their answer
...         user_input_ = await session.interact(f"Do you want to execute the command '{cmd}'?")
...         return {"user_input": user_input_}
... 
>>> # Create workflow, execute workflow to trigger interruption interaction flow
>>> flow = Workflow()
>>> flow.set_start_comp("start", Start(), inputs_schema={"command1": "${cmd1}"})
>>> flow.add_workflow_comp("interactive_node1", InteractiveNode(), inputs_schema={"cmd": "${start.command1}"})
>>> flow.set_end_comp("end", End(), inputs_schema={ "result1": "${interactive_node1.result}"})
>>> flow.add_connection("start", "interactive_node1")
>>> flow.add_connection("interactive_node1", "end")
>>> 
>>> session_id = uuid.uuid4().hex
>>> # Execute workflow
>>> output = asyncio.run(flow.invoke({"cmd1": "delete all files"}, create_workflow_session(session_id=session_id)))
>>> 
>>> # Check if result is interruption interaction output information
>>> if isinstance(output.result[0].payload, InteractionOutput):
...     # Get interruption component's prompt message
...     print(output.result[0].payload.id)
...
interactive_node1
>>>     print(output.result[0].payload.value)
Do you want to execute the command 'delete all files'
```

> **Note**
>
> - output is the workflow output, see [Workflow Output Introduction](../workflow/workflow.md) for details.
> - The workflow output contains `interactive_node1`, `Do you want to execute the command '{delete all files}'`. At this point, the result in output is a list, where each element is a streaming output element. See [Streaming Output Introduction](../../../../agent-core Development Guide/Advanced Usage/Session/Streaming Output.md) for details. The content in payload is the interruption output, which is the `InteractionOutput` introduced in this chapter.
