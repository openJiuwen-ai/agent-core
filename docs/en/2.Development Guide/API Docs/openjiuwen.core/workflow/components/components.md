# openjiuwen.core.workflow

## class openjiuwen.core.workflow.components.component.WorkflowComponent

> **Note**: Component classes are exported through the `openjiuwen.core.workflow` module. It is recommended to use `from openjiuwen.core.workflow import ComponentComposable` for import.

`ComponentComposable` is the abstract base class for custom workflow components. All custom components need to inherit from this class and implement logic for adding themselves to the graph and converting to executable units (`ComponentExecutable`).

### add_component(graph: Graph, node_id: str, wait_for_all: bool = False) -> None:

Define how to add the current component to the specified `graph`.

**Parameters**:

- **graph** ([Graph](../../graph/graph.md#class-graph)): `graph` instance of `workflow`.
- **node_id** (str): Unique `node_id` of this component in `graph`.
- **wait_for_all** (bool): Whether this component needs to wait for all preceding components to complete execution, `True` means wait, `False` means do not wait. Default value: `False`.

**Example**:

```python
>>> import asyncio
>>> import random
>>> 
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.workflow import ComponentComposable, BranchRouter, End, Start, Workflow, Input, Output
>>> from openjiuwen.core.workflow.components import Session
>>> from openjiuwen.core.workflow import create_workflow_session
>>> from openjiuwen.core.graph import Graph
>>> 
>>> 
>>> # Custom component `CustomIntentComponent` implements the `add_component` interface to customize built-in conditional edges for this component, and custom implements the `add_branch` interface to bind target nodes for built-in conditional edges.
>>> # Create custom component and implement add_component interface
>>> class CustomIntentComponent(ComponentComposable):
...     def __init__(self, default_intents):
...         super().__init__()
...         self._intents = default_intents
...         self._router = BranchRouter()
... 
...     def add_component(self, graph: Graph, node_id: str, wait_for_all: bool = False) -> None:
...         graph.add_node(node_id, self.to_executable(), wait_for_all=wait_for_all)
...         graph.add_conditional_edges(node_id, self._router)
... 
...     def add_branch(self, condition: str, target: list[str], branch_id:str):
...         self._router.add_branch(condition=condition, target=target, branch_id=branch_id)
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         self._router.set_session(session)
...         return {'result': self._intents[random.randint(0, len(self._intents) - 1)]}
>>> 
>>> 
>>> class TravelComponent(ComponentComposable):
...     def __init__(self, node_id):
...         super().__init__()
...         self.node_id = node_id
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         print(f'[{self.node_id}] inputs = {inputs}')
...         return inputs
>>> 
>>> 
>>> class EatComponent(ComponentComposable):
...     def __init__(self, node_id):
...         super().__init__()
...         self.node_id = node_id
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         print(f'[{self.node_id}] inputs = {inputs}')
...         return inputs
>>> 
>>> 
>>> async def run_workflow():
...     # Create workflow
...     flow = Workflow()
...     flow.set_start_comp("start", Start(), inputs_schema={"query": "${user_inputs.query}"})
... 
...     intent_component = CustomIntentComponent(["出行", "餐饮"])
...     intent_component.add_branch(condition="${intent.result} == '出行'", target=['travel'], branch_id='1')
...     intent_component.add_branch(condition="${intent.result} == '餐饮'", target=['eat'], branch_id='2')
...     flow.add_workflow_comp("intent", intent_component, inputs_schema={"query": "${start.query}"})
...     flow.add_workflow_comp("eat", EatComponent("eat"), inputs_schema={"intent": '${intent.result}'})
...     flow.add_workflow_comp("travel", TravelComponent("travel"), inputs_schema={"intent": '${intent.result}'})
...     flow.set_end_comp("end", End(), inputs_schema={"eat": "${eat.intent}", "travel": "${travel.intent}"})
...     flow.add_connection("start", "intent")
...     flow.add_connection("eat", "end")
...     flow.add_connection("travel", "end")
... 
...     session = create_workflow_session()
...     output = await flow.invoke({"user_inputs": {"query": "去新疆"}}, session)
...     print(output.result)
>>> 
>>> if __name__ == '__main__':
...     asyncio.get_event_loop().run_until_complete(run_workflow())
[eat] inputs = {'intent': '餐饮'}
{'output': {'eat': '餐饮'}}
```

### to_executable() -> Executable

Return the executable instance corresponding to this component. openJiuwen supports user-defined implementation of `ComponentExecutable` as the return value of this interface, used for runtime scheduling and execution.

**Returns**:

**[Executable](../../graph/graph.md)**, execution instance of this component.

**Example**:

```python

>>> import asyncio
>>>
>>> from openjiuwen.core.context_engine import ModelContext 
>>> from openjiuwen.core.graph.executable import Executable
>>> from openjiuwen.core.workflow.components import Session
>>> from openjiuwen.core.workflow import create_workflow_session
>>> from openjiuwen.core.workflow import ComponentExecutable, End, Input, Output, Start, Workflow, ComponentComposable
>>> 
>>> # `CalculateComponent` inherits `ComponentComposable`, is a component for mathematical operations, implements `to_executable` method to provide `ComputeExecutor` instance that implements calculation logic, implements `add_component` method to add `CalculateComponent` node to workflow.
>>> # Create custom component and implement to_executable interface
>>> class CalculateComponent(ComponentComposable):
...     def to_executable(self) -> Executable:
...         return ComputeExecutor()
>>> 
>>> class ComputeExecutor(ComponentExecutable):
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         return {'result': self.__calculate__(data=inputs.get('data'))}
... 
...     def __calculate__(self, data):
...         operator = data.get('operator')
...         a = data.get('a')
...         b = data.get('b')
...         if operator == 'add':
...             return a + b
...         if operator == 'multiply':
...             return a * b
...         if operator == 'substract':
...             return a - b
...         return a / b
>>> 
>>> async def run_workflow():
...     # Create workflow and execute
...     flow = Workflow()
...     flow.set_start_comp("start", Start(), inputs_schema={"a": "${user_inputs.a}", "b": "${user_inputs.b}", "operator": "${user_inputs.operator}"})
...     flow.add_workflow_comp("compute", CalculateComponent(), inputs_schema={"data": "${start}"})
...     flow.set_end_comp("end", End(), inputs_schema={"result": "${compute.result}"})
...     flow.add_connection("start", "compute")
...     flow.add_connection("compute", "end")
...     session = create_workflow_session()
...     output = await flow.invoke({"user_inputs": {"a": 1, "b": 2, "operator": "add"}}, session)
...     print(output.result)
>>> 
>>> 
>>> if __name__ == '__main__':
...     asyncio.run(run_workflow())
{'output': {'result': 3}}
```


## class WorkflowComponent

## class openjiuwen.core.workflow.components.Session

```python
class openjiuwen.core.workflow.components.Session(self, session: NodeSession, stream_mode: bool = False)
```

Session during component execution, no need to manually create, the `invoke/stream/collect/transform` interface parameters of components carry this session.

**Parameters**:

- **session** (NodeSession): Internal component session.
- **stream_mode** (bool, optional): Whether it is a streaming input interface, default is `False`.

### get_workflow_id

```python
get_workflow_id(self)
```

Get the `id` of the workflow to which the current component belongs.

**Returns**:

**str**: Get the `id` of the workflow to which the current component belongs.

**Example**:

```python
>>> import asyncio
>>> from openjiuwen.core.workflow import WorkflowComponent
>>> from openjiuwen.core.workflow import End
>>> from openjiuwen.core.workflow import Start
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.workflow import Input, Output
>>> from openjiuwen.core.workflow.components import Session
>>> from openjiuwen.core.workflow import create_workflow_session
>>> from openjiuwen.core.workflow import Workflow
>>> from openjiuwen.core.workflow import WorkflowCard
>>> 
>>> 
>>> class CustomComponent(WorkflowComponent):
...    def __init__(self, workflow_id):
...        super().__init__()
...        self._workflow_id = workflow_id
...
...    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...        print(f"workflow_id is: {session.get_workflow_id()}")
...        return {}
... 
>>> flow = Workflow(card=WorkflowCard(id="workflow1"))
>>> flow.set_start_comp("start", Start())
>>> flow.add_workflow_comp("custom", CustomComponent("custom"))
>>> flow.set_end_comp("end", End())
>>> flow.add_connection("start", "custom")
>>> flow.add_connection("custom", "end")
>>> 
>>> if __name__ == "__main__":
...    asyncio.run(flow.invoke(inputs={}, session=create_workflow_session()))
workflow_id is: workflow1
```

### get_component_id
```python
get_component_id(self)
```

Get the `id` of the current component.

**Returns**:

**str**: The `id` of the current component.

**Example**:

```python
>>> import asyncio
>>> from openjiuwen.core.workflow import WorkflowComponent
>>> from openjiuwen.core.workflow import End
>>> from openjiuwen.core.workflow import Start
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.workflow import Input, Output
>>> from openjiuwen.core.workflow.components import Session
>>> from openjiuwen.core.workflow import create_workflow_session
>>> from openjiuwen.core.workflow import Workflow
>>> 
>>> 
>>> class CustomComponent(WorkflowComponent):
...    def __init__(self, workflow_id):
...        super().__init__()
...        self._workflow_id = workflow_id
...
...    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...        print(f"component_id: {session.get_component_id()}")
...        return {}
... 
>>> flow = Workflow()
>>> flow.set_start_comp("start", Start())
>>> flow.add_workflow_comp("custom", CustomComponent("custom"))
>>> flow.set_end_comp("end", End())
>>> flow.add_connection("start", "custom")
>>> flow.add_connection("custom", "end")
>>> 
>>> if __name__ == "__main__":
...    asyncio.run(flow.invoke(inputs={}, session=create_workflow_session()))
component_id: custom
```

### get_component_type

```python
get_component_type(self)
```

Get the type identifier of the current component.

**Returns**:

**str**: The type of the current component.

**Example**:

```python
>>> import asyncio
>>> from openjiuwen.core.workflow import WorkflowComponent
>>> from openjiuwen.core.workflow import End
>>> from openjiuwen.core.workflow import Start
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.workflow import Input, Output
>>> from openjiuwen.core.workflow.components import Session
>>> from openjiuwen.core.workflow import create_workflow_session
>>> from openjiuwen.core.workflow import Workflow
>>> 
>>> 
>>> class CustomComponent(WorkflowComponent):
...    def __init__(self, workflow_id):
...        super().__init__()
...        self._workflow_id = workflow_id
...
...    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...        print(f"component_type: {session.get_component_id()}")
...        return {}
... 
>>> flow = Workflow()
>>> flow.set_start_comp("start", Start())
>>> flow.add_workflow_comp("custom", CustomComponent("custom"))
>>> flow.set_end_comp("end", End())
>>> flow.add_connection("start", "custom")
>>> flow.add_connection("custom", "end")
>>> 
>>> if __name__ == "__main__":
...    asyncio.run(flow.invoke(inputs={}, session=create_workflow_session()))
component_type: CustomComponent
```

### trace

```python
trace(self, data: dict)
```

Output runtime information as streaming messages, and stream out to Agent or Workflow in [TraceSchema](../../session/stream/stream.md#class-openjiuwencoresessionstreamtraceschema) format, facilitating users to deeply understand internal runtime mechanisms, ensure processes proceed as expected, and quickly locate issues, improving work efficiency.

**Parameters**:

- **data** (dict): Runtime data that needs to be traced and recorded. This data will be filled into the `onInvokeData` field in the payload structure of openJiuwen's built-in streaming message format [TraceSchema](../../session/stream/stream.md#class-openjiuwencoresessionstreamtraceschema). When it is `None`, it means there is no runtime data.

**Example**:

```python
>>> import asyncio
>>> 
>>> from openjiuwen.core.workflow import WorkflowComponent
>>> from openjiuwen.core.workflow import End
>>> from openjiuwen.core.workflow import Start
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.workflow import Output, Input
>>> from openjiuwen.core.workflow.components import Session
>>> from openjiuwen.core.workflow import create_workflow_session
>>> from openjiuwen.core.session.stream import BaseStreamMode
>>> from openjiuwen.core.workflow import Workflow
>>> 
>>> # 1. Custom implement a component that records trace stream information
>>> class StreamNode(WorkflowComponent):
...     def __init__(self):
...         super().__init__()
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         await session.trace(dict(content="记录trace信息"))
...         return inputs
... 
>>> # 2. Create workflow
>>> flow = Workflow()
>>> flow.set_start_comp("start", Start(), inputs_schema={"a": "${a}"})
>>> flow.add_workflow_comp("a", StreamNode(), inputs_schema={"aa": "${start.a}"})
>>> flow.set_end_comp("end", End())
>>> flow.add_connection("start", "a")
>>> flow.add_connection("a", "end")
>>> 
>>> async def run_workflow():
...     # 3. Execute workflow and get streaming output data
...     async for chunk in flow.stream({"a": 1}, create_workflow_session(), stream_modes=[BaseStreamMode.TRACE]):
...         if 'onInvokeData' in chunk.payload and chunk.payload['onInvokeData'] and chunk.payload['status'] == 'running':
...             print(chunk.payload["onInvokeData"])
... 
>>> asyncio.run(run_workflow())
[{'content':'记录trace信息'}]
```

### trace_error

```python
trace_error(self, error: Exception)
```

Output error information during runtime, and stream out to Workflow or Agent in [TraceSchema](../../session/stream/stream.md#class-openjiuwencoresessionstreamtraceschema) format.

**Parameters**:

- **error** (Exception): Error exception information. This data will be filled into the `error` field in the payload structure of openJiuwen's built-in streaming message format [TraceSchema](../../session/stream/stream.md#class-openjiuwencoresessionstreamtraceschema).

**Exceptions**:

- **JiuWenBaseException**: openJiuwen exception base class, for detailed information and solutions, see [StatusCode](../../common/exception/status_code.md).

**Example**:

```python
>>> import asyncio
>>>
>>> from openjiuwen.core.workflow import WorkflowComponent
>>> from openjiuwen.core.workflow import End
>>> from openjiuwen.core.workflow import Start
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.workflow import Output, Input
>>> from openjiuwen.core.workflow.components import Session
>>> from openjiuwen.core.workflow import create_workflow_session
>>> from openjiuwen.core.session.stream import BaseStreamMode
>>> from openjiuwen.core.workflow import Workflow
>>> from openjiuwen.core.common.exception.exception import JiuWenBaseException
>>>
>>> # 1. Create custom component and stream out a trace error streaming message
>>> class StreamNode(WorkflowComponent):
...     def __init__(self):
...         super().__init__()
...     
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         await session.trace_error(JiuWenBaseException(error_code=-1, message="这是一条错误信息"))
...         return inputs
...     
>>> # 2. Create workflow
>>> flow = Workflow()
>>> flow.set_start_comp("start", Start(), inputs_schema={"a": "${a}"})
>>> flow.add_workflow_comp("a", StreamNode(), inputs_schema={"aa": "${start.a}"})
>>> flow.set_end_comp("end", End())
>>> flow.add_connection("start", "a")
>>> flow.add_connection("a", "end")
>>>
>>> # 3. Execute workflow
>>> async def run_workflow():
...     async for chunk in flow.stream({"a": 1}, create_workflow_session(), stream_modes=[BaseStreamMode.TRACE]):
...         # Used to filter debug information with error information and not the last frame
...         if 'error' in chunk.payload and chunk.payload["error"] is not None and chunk.payload.get("outputs") is None:
...             print(chunk.payload["error"])
>>> 
>>> asyncio.run(run_workflow())
{'error_code': -1, 'message': '这是一条错误信息'}
```

### interact

```python
interact(self, value)
```

Interrupt current Agent or Workflow execution, interact with user, and wait for user to provide supplementary input [InteractionInput](../../session/session.md#class-openjiuwencoresessioninteractiveinput), then continue execution from the interruption point. This interface currently only supports use in non-streaming input mode `invoke` and `stream` interfaces of components.
When a workflow has a question node, the question node generates a question requirement, this interface can be called, and the question information is returned to the user as value, until the user inputs a new answer, the workflow resumes execution from the position where this interface was called.

**Parameters**:

- **value** (Any): Interruption prompt information fed back to the user, if the value is `None`, it means there is no interruption prompt information. Depending on the scenario where the interruption occurs, the location where this information is ultimately returned to the user also differs.
  - When interruption occurs during workflow `invoke` execution, value will be filled into a `WorkflowChunk`'s `payload` (type is `InteractionOutput`), and returned as an item in the workflow execution result `WorkflowOutput.result` list.
  - When interruption occurs during workflow `stream` execution, value will be filled into a frame of `OutputSchema` format streaming information's `payload` (type is `InteractionOutput`), and streamed out of the workflow, the type of this streaming information is `INTERACTION`.

**Returns**:

**Any**: User's supplementary input `raw_inputs` or `value` in [InteractionInput](../../session/session.md#class-openjiuwencoresessioninteractiveinput).

**Examples**:

- Example 1: Interruption interaction occurs during workflow `invoke` execution.

```python
>>> import asyncio
>>> 
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
>>> # 1.  Create a node with interruption interaction
>>> class InteractiveNode(WorkflowComponent):
...     def __init__(self):
...         super().__init__()
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         cmd = inputs.get("cmd")
...         # Ask user, wait for user answer
...         result = await session.interact(f"Do you want to execute the command '{cmd}'?")
...         return {"confirm_result": result}
... 
>>> # 2. Create simple workflow
>>> flow = Workflow()
>>> flow.set_start_comp("start", Start(), inputs_schema={"command1": "${cmd1}"})
>>> flow.add_workflow_comp("interactive_node1", InteractiveNode(), inputs_schema={"cmd": "${start.command1}"}, outputs_schema={"result": "${confirm_result}"})
>>> flow.set_end_comp("end", End(), inputs_schema={"result1": "${interactive_node1.result}"})
>>> flow.add_connection("start", "interactive_node1")
>>> flow.add_connection("interactive_node1", "end")
>>> 
>>> session_id = "session_id"
>>> #3.  Execute workflow
>>> output = asyncio.run(flow.invoke({"cmd1": "delete all files"}, create_workflow_session(session_id=session_id)))
>>> 
>>> if isinstance(output.result[0].payload, InteractionOutput):
...     # Get interruption node id
...     node_id = output.result[0].payload.id
...     print(f'node_id={output.result[0].payload.id}, value={output.result[0].payload.value}')
... 
node_id=interactive_node1, value=Do you want to execute the command 'delete all files'?
>>> 
>>> # 4. User inputs interactive supplementary information to interruption node
>>> user_input = InteractiveInput()
>>> user_input.update(node_id, "Yes")
>>> 
>>> # 5. Execute invoke
>>> output = asyncio.run(flow.invoke(user_input, create_workflow_session(session_id=session_id)))
>>> print(output.result.get("output").get("result1"))
Yes
```

- Example 2: Interruption interaction occurs during workflow `stream` execution.

```python
>>> import asyncio
>>> 
>>> from openjiuwen.core.common.constants.constant import INTERACTION
>>> from openjiuwen.core.workflow import WorkflowComponent
>>> from openjiuwen.core.workflow import End
>>> from openjiuwen.core.workflow import Start
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.workflow import Input, Output
>>> from openjiuwen.core.session import InteractiveInput
>>> from openjiuwen.core.workflow.components import Session
>>> from openjiuwen.core.workflow import create_workflow_session
>>> from openjiuwen.core.session.stream import BaseStreamMode, OutputSchema
>>> from openjiuwen.core.workflow import Workflow
>>> 
>>> # 1. Custom interactive node
>>> class InteractiveStreamNode(WorkflowComponent):
...     def __init__(self):
...         super().__init__()
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         # Interrupt execution, wait for user supplementary information
...         result = await session.interact("Please enter any key")
... 
...         # Output user's supplementary userInput information as a frame of streaming message
...         await session.write_stream(OutputSchema(type="output", index=0, payload=result))
...         return result
... 
>>> # 2. Create workflow and trigger stream execution
>>> flow = Workflow()
>>> flow.set_start_comp("start", Start(), inputs_schema={"a": "${inputs.a}",})
>>> flow.add_workflow_comp("interactive_node1", InteractiveStreamNode(),inputs_schema={ "aa": "${start.a}"},)
>>> flow.set_end_comp("end", End(), inputs_schema={"result": "${interactive_node1.aa}"})
>>> flow.add_connection("start", "interactive_node1")
>>> flow.add_connection("interactive_node1", "end")
>>> 
>>> # 3. Execute stream
>>> async def run_workflow():
...     interaction_node_id = None
...     session_id = "ss"
...     async for res in flow.stream({"inputs": {"a": 1}}, create_workflow_session(session_id=session_id), stream_modes=[BaseStreamMode.OUTPUT]):
...         if res.type == INTERACTION:
...             interaction_node_id = res.payload.id
...             interaction_msg = res.payload.value
...             print(interaction_node_id)
...             print(interaction_msg)
...     user_input = InteractiveInput()
...     user_input.update(interaction_node_id, {"aa": "any key"})
...     async for res in flow.stream(user_input, create_workflow_session(session_id=session_id), stream_modes=[BaseStreamMode.OUTPUT]):
...         if res.type == "output":
...             print(res.payload)
... 
>>> asyncio.run(run_workflow())
interactive_node1
Please enter any key
{"aa": "any key"}
```

### get_executable_id

```python
get_executable_id(self) -> str
```

Return the globally unique ID for the component.

**Returns**:

**str**: Globally unique ID of the component.

**Example**:

```python
>>> import asyncio
>>> from openjiuwen.core.workflow import WorkflowComponent
>>> from openjiuwen.core.workflow import End
>>> from openjiuwen.core.workflow import Start
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.workflow import Input, Output
>>> from openjiuwen.core.workflow.components import Session
>>> from openjiuwen.core.workflow import create_workflow_session
>>> from openjiuwen.core.workflow import Workflow
>>>
>>> class CustomComponent(WorkflowComponent):
...    def __init__(self, workflow_id):
...        super().__init__()
...        self._workflow_id = workflow_id
...
...    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...        print(f"executable_id: {session.get_executable_id()}")
...        return {}
...
>>> flow = Workflow()
>>> flow.set_start_comp("start", Start())
>>> flow.add_workflow_comp("custom", CustomComponent("custom"))
>>> flow.set_end_comp("end", End())
>>> flow.add_connection("start", "custom")
>>> flow.add_connection("custom", "end")
>>>
>>> if __name__ == "__main__":
...    asyncio.run(flow.invoke(inputs={}, session=create_workflow_session()))
executable_id: custom
```

### get_session_id

```python
get_session_id(self) -> str
```

Get the unique session identifier for this workflow execution.

**Returns**:

**str**: Unique session identifier of the current workflow.

**Example**:

```python
>>> import asyncio
>>> from openjiuwen.core.workflow import WorkflowComponent
>>> from openjiuwen.core.workflow import End
>>> from openjiuwen.core.workflow import Start
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.workflow import Input, Output
>>> from openjiuwen.core.workflow.components import Session
>>> from openjiuwen.core.workflow import create_workflow_session
>>> from openjiuwen.core.workflow import Workflow
>>>
>>> class CustomComponent(WorkflowComponent):
...    def __init__(self, workflow_id):
...        super().__init__()
...        self._workflow_id = workflow_id
...
...    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...        print(f"session_id: {session.get_session_id()}")
...        return {}
...
>>> flow = Workflow()
>>> flow.set_start_comp("start", Start())
>>> flow.add_workflow_comp("custom", CustomComponent("custom"))
>>> flow.set_end_comp("end", End())
>>> flow.add_connection("start", "custom")
>>> flow.add_connection("custom", "end")
>>>
>>> if __name__ == "__main__":
...    asyncio.run(flow.invoke(inputs={}, session=create_workflow_session(session_id="123")))
session_id: 123
```

### update_state

```python
update_state(self, data: dict)
```

Update the state data of the current component, takes effect after component execution completes, will add if field does not exist.

**Parameters**:

- **data** (dict): Dictionary of key-value pairs to add or update. If data is None or {}, it means no update.

**Example**:

Update state information

```python
>>> import asyncio
>>> 
>>> from openjiuwen.core.workflow import WorkflowComponent
>>> from openjiuwen.core.workflow import End
>>> from openjiuwen.core.workflow import LoopGroup, LoopComponent
>>> from openjiuwen.core.workflow import Start
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.workflow import Input, Output
>>> from openjiuwen.core.workflow.components import Session
>>> from openjiuwen.core.workflow import create_workflow_session
>>> from openjiuwen.core.workflow import Workflow
>>> 
>>> 
>>> class CustomStateComponent(WorkflowComponent):
...     def __init__(self, node_id):
...         super().__init__()
...         self._node_id = node_id
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         my_state = session.get_state({"recall_times": "${recall_times}", "messages": "${messages}"})
...         if not my_state:
...             my_state = {}
...         if not my_state.get("recall_times"):
...             my_state["recall_times"] = 0
...         my_state["recall_times"] = my_state["recall_times"] + 1
...         if not my_state.get("messages"):
...             my_state["messages"] = []
...         my_state["messages"].append(inputs.get("query"))
...         print(f"[{self._node_id}] current state is {my_state}")
...         session.update_state(my_state)
...         return inputs
... 
>>> # 2.  Create workflow and execute, workflow contains loop component, loop contains custom component
>>> flow = Workflow()
>>> flow.set_start_comp("start", Start(), inputs_schema={"query": "${user_inputs.query}"})
>>> flow.set_end_comp("end", End())
>>> 
>>> loop_group = LoopGroup()
>>> loop_group.start_nodes(["s"])
>>> loop_group.end_nodes(["a"])
>>> loop_group.add_workflow_comp("s", Start(), inputs_schema={"query": "${l.query}"})
>>> loop_group.add_workflow_comp("a", CustomStateComponent("a"), inputs_schema={"query": "${start.query}"})
>>> loop_group.add_connection("s", "a")
>>> flow.add_workflow_comp("l", LoopComponent(loop_group, {}),inputs_schema={"loop_type": "number", "loop_number": "${user_inputs.loop_number}", "query": "${start.query}"})
>>> flow.add_connection("start", "l")
>>> flow.add_connection("l", "end")
>>> 
>>> # 3. Execute workflow
>>> asyncio.run(flow.invoke({"user_inputs": {"query": "你好", "loop_number": 3}}, create_workflow_session()))
[a] current state is {'recall_times': 1, 'messages': ['你好']}
[a] current state is {'recall_times': 2, 'messages': ['你好', '你好']}
[a] current state is {'recall_times': 3, 'messages': ['你好', '你好', '你好']}
```

### get_state

```python
get_state(self, key: Union[str, list, dict] = None) -> Any
```

- Get the state information of the current component.

**Parameters**:

- **key** (Union[str, list, dict], optional): Key to query the corresponding value in state data, default `None`, means get all state information. Based on the value type of `key`, supports multiple ways to get state data.
  - When `key` is `str` type, means get state data under the `key` path, key can be a nested path structure, e.g., `"user_inputs.query"`.
  - When `key` is `dict`\ `list` type, means get multiple state data, this `dict`\ `list` contains multiple variables wrapped with `"${}"`, each variable is a path to state data.

**Returns**:

**Any**: Returned state value. Based on the type of `key`, return results are:

- When `key` is `str` type, returns state information under that path, returns `None` if it doesn't exist.
- When `key` is `dict`\ `list` type, this interface will replace all variables within `${}` in the input `dict`\ `list` with values of variables at corresponding paths, replaces with `None` if the corresponding variable path doesn't exist, finally returns the result after replacement.

**Example**:

```python
>>> import asyncio
>>> 
>>> from openjiuwen.core.workflow import WorkflowComponent
>>> from openjiuwen.core.workflow import End
>>> from openjiuwen.core.workflow import LoopGroup, LoopComponent
>>> from openjiuwen.core.workflow import Start
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.workflow import Input, Output
>>> from openjiuwen.core.workflow.components import Session
>>> from openjiuwen.core.workflow import create_workflow_session
>>> from openjiuwen.core.workflow import Workflow
>>> 
>>> class CustomStateComponent(WorkflowComponent):
...    def __init__(self, node_id):
...        super().__init__()
...        self._node_id = node_id
...
...    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...        # Get single state
...        recall_times = session.get_state("recall_times")
...        # Get multiple states
...        my_state = session.get_state({"recall_times": "${recall_times}", "messages": "${messages}"})
...        # Get list state
...        my_state_list = session.get_state(["${recall_times}", "${messages}"])
...        print(f'recall_times: {recall_times}, my_state: {my_state}, my_state_list: {my_state_list}')
...        if not my_state:
...            my_state = {}
...        if not my_state.get("recall_times"):
...            my_state["recall_times"] = 0
...        my_state["recall_times"] = my_state["recall_times"] + 1
...        if not my_state.get("messages"):
...            my_state["messages"] = []
...        my_state["messages"].append(inputs.get("query"))
...        session.update_state(my_state)
...        return inputs
...
>>> # 2.  Create workflow and execute, workflow contains loop component, loop contains custom component
>>> flow = Workflow()
>>> flow.set_start_comp("start", Start(), inputs_schema={"query": "${user_inputs.query}"})
>>> flow.set_end_comp("end", End())
>>> 
>>> loop_group = LoopGroup()
>>> loop_group.start_nodes(["s"])
>>> loop_group.end_nodes(["a"])
>>> loop_group.add_workflow_comp("s", Start(), inputs_schema={"query": "${l.query}"})
>>> loop_group.add_workflow_comp("a", CustomStateComponent("a"), inputs_schema={"query": "${start.query}"})
>>> loop_group.add_connection("s", "a")
>>> flow.add_workflow_comp("l", LoopComponent(loop_group, {}),inputs_schema={"loop_type": "number", "loop_number": "${user_inputs.loop_number}", "query": "${start.query}"})
>>> flow.add_connection("start", "l")
>>> flow.add_connection("l", "end")
>>> 
>>> # 3. Execute workflow
>>> 
>>> asyncio.run(flow.invoke({"user_inputs": {"query": "你好", "loop_number": 3}}, create_workflow_session()))
recall_times: None, my_state: None, my_state_list: None
recall_times: 1, my_state: {'recall_times': 1, 'messages': ['你好']}, my_state_list: [1, ['你好']]
recall_times: 2, my_state: {'recall_times': 2, 'messages': ['你好', '你好']}, my_state_list: [2, ['你好', '你好']]
```

### update_global_state

```python
update_global_state(self, data: dict)
```

Update **globally shared** state, takes effect after current component execution completes.

**Parameters**:

- **data** (dict): Dictionary of key-value pairs to add or update. If input is None or {}, it means no update.

**Example**:

```python
>>> import asyncio
>>> 
>>> from openjiuwen.core.workflow import WorkflowComponent
>>> from openjiuwen.core.workflow import Start
>>> from openjiuwen.core.workflow import BranchComponent
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.workflow import Input, Output
>>> from openjiuwen.core.workflow.components import Session
>>> from openjiuwen.core.workflow import create_workflow_session
>>> from openjiuwen.core.workflow import Workflow
>>> 
>>> # 1. Implement custom component for global state +10 operation each time
>>> class AddTenComponent(WorkflowComponent):
...     def __init__(self, node_id):
...         super().__init__()
...         self._node_id = node_id
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         call_times = session.get_state("call_times")
...         # Get current global state num
...         num = session.get_global_state("num")
...         if not num:
...             num = 0
...         if not call_times:
...             call_times = 0
...         call_times += 1
...         print(f"begin to invoke, call_times={call_times}, num={num}, operation: num = {num} + 10")
...         num += 10
...         session.update_global_state({"num": num})
...         # Update current global state
...         session.update_state({"call_times": call_times})
...         return inputs 
... 
>>> # 2. Custom component for reading global state num when workflow ends
>>> class CustomEnd(WorkflowComponent):
...     def __init__(self, node_id):
...         super().__init__()
...         self._node_id = node_id
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         print(f'->[{self._node_id}] get num = {session.get_global_state("num")}')
...         return inputs
... 
>>> # 3. Create workflow and execute workflow
>>> flow = Workflow()
>>> flow.set_start_comp("start", Start())
>>> flow.set_end_comp("end", CustomEnd("end"))
>>> flow.add_workflow_comp("a", AddTenComponent("a"))
>>> 
>>> sw = BranchComponent()
>>> sw.add_branch("${num} <= 30", ["a"], "1")
>>> sw.add_branch("${num} > 30", ["end"], "2")
>>> flow.add_workflow_comp("sw", sw)
>>> 
>>> flow.add_connection("start", "a")
>>> flow.add_connection("a", "sw")
>>> asyncio.run(flow.invoke({"a": 2}, create_workflow_session()))
begin to invoke, call_times=1, num=0, operation: num = 0 + 10
begin to invoke, call_times=2, num=10, operation: num = 10 + 10
begin to invoke, call_times=3, num=20, operation: num = 20 + 10
begin to invoke, call_times=4, num=30, operation: num = 30 + 10
->[end] get num = 40
```

### get_global_state

```python
get_global_state(self, key: Union[str, list, dict] = None) -> Any
```

Read globally shared state data, supports flexible data extraction.

**Parameters**:

- **key** (Union[str, list, dict], optional): Key to query the corresponding value in state data, default `None`, means get all state information. Based on the value type of `key`, supports multiple ways to get state data.
  - When `key` is `str` type, means get state data under the `key` path, key can be a nested path structure, e.g., `"user_inputs.query"`.
  - When `key` is `dict`\ `list` type, means get multiple state data, this `dict`\ `list` contains multiple variables wrapped with `"${}"`, each variable is a path to state data.

**Returns**:

Based on the type of `key`, return results are:

- When `key` is `str` type, returns state information under that path, returns `None` if it doesn't exist.
- When `key` is `dict`\ `list` type, this interface will replace all variables within `${}` in the input `dict`\ `list` with values of variables at corresponding paths, replaces with `None` if the corresponding variable path doesn't exist, finally returns the result after replacement.

**Example**:

```python
>>> import asyncio
>>> 
>>> from openjiuwen.core.workflow import WorkflowComponent
>>> from openjiuwen.core.workflow import BranchComponent
>>> from openjiuwen.core.workflow import Start
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.workflow import Output, Input
>>> from openjiuwen.core.workflow.components import Session
>>> from openjiuwen.core.workflow import create_workflow_session
>>> from openjiuwen.core.workflow import Workflow
>>> 
>>> # 1. Implement custom component for global state +10 operation each time
>>> class AddTenComponent(WorkflowComponent):
...     def __init__(self, node_id):
...         super().__init__()
...         self._node_id = node_id
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         call_times = session.get_state("call_times")
... 
...         # Get current global state num
...         num = session.get_global_state("num")
...         # Get multiple global states
...         num_list = session.get_global_state({"current_call_time": "${current_call_time}", "num": "${num}"})
...         print(f"get call times: {call_times}, num: {num}, num_list: {num_list}")
... 
...         if not num:
...             num = 0
...         if not call_times:
...             call_times = 0
...         call_times += 1
...         num += 10
...         session.update_global_state({"num": num, "current_call_times": call_times})
...         # Update current global state
...         session.update_state({"call_times": call_times})
...         return inputs
... 
>>> # 2. Custom component for reading global state num when workflow ends
>>> class CustomEnd(WorkflowComponent):
...     def __init__(self, node_id):
...         super().__init__()
...         self._node_id = node_id
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         print(f'->[{self._node_id}] get num = {session.get_global_state("num")}')
...         return inputs
...  
>>> # 3. Create workflow and execute workflow
>>> flow = Workflow()
>>> flow.set_start_comp("start", Start())
>>> flow.set_end_comp("end", CustomEnd("end"))
>>> flow.add_workflow_comp("a", AddTenComponent("a"))
>>> 
>>> sw = BranchComponent()
>>> sw.add_branch("${num} <= 30", ["a"], "1")
>>> sw.add_branch("${num} > 30", ["end"], "2")
>>> flow.add_workflow_comp("sw", sw)
>>> 
>>> flow.add_connection("start", "a")
>>> flow.add_connection("a", "sw")
>>> asyncio.run(flow.invoke({"a": 2}, create_workflow_session()))
get call times: None, num: None, num_list: {'current_call_time': None, 'num': None}
get call times: 1, num: 10, num_list: {'current_call_time': None, 'num': 10}
get call times: 2, num: 20, num_list: {'current_call_time': None, 'num': 20}
get call times: 3, num: 30, num_list: {'current_call_time': None, 'num': 30}
->[end] get num = 40
```

### dump_state

```python
dump_state(self) -> dict
```

Exports the complete state of the current workflow and returns a dictionary containing all session state information.

**Returns**:

**dict**: A dictionary containing the full set of current workflow states.

**Example**:

```python
>>> import asyncio
>>> 
>>> from openjiuwen.core.workflow import WorkflowComponent
>>> from openjiuwen.core.workflow import BranchComponent
>>> from openjiuwen.core.workflow import Start
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.workflow import Output, Input
>>> from openjiuwen.core.workflow.components import Session
>>> from openjiuwen.core.workflow import create_workflow_session
>>> from openjiuwen.core.workflow import Workflow
>>> 
>>> # 1. Implement custom component for global state +10 operation each time
>>> class AddTenComponent(WorkflowComponent):
...     def __init__(self, node_id):
...         super().__init__()
...         self._node_id = node_id
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         call_times = session.get_state("call_times")
... 
...         # Get current global state num
...         num = session.get_global_state("num")
... 
...         if not num:
...             num = 0
...         if not call_times:
...             call_times = 0
...         call_times += 1
...         num += 10
...         session.update_global_state({"num": num, "current_call_times": call_times})
...         # Update current global state
...         session.update_state({"call_times": call_times})
... 
...         # Print current state
...         print(f'dump state in {self._node_id}: {session.dump_state()}')
...         return inputs
... 
>>> # 2. Custom component for reading global state num when workflow ends
>>> class CustomEnd(WorkflowComponent):
...     def __init__(self, node_id):
...         super().__init__()
...         self._node_id = node_id
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         # Print current state
...         print(f'dump state in {self._node_id}: {session.dump_state()}')
...         return inputs
...  
>>> # 3. Create workflow and execute workflow
>>> flow = Workflow()
>>> flow.set_start_comp("start", Start())
>>> flow.set_end_comp("end", CustomEnd("end"))
>>> flow.add_workflow_comp("a", AddTenComponent("a"))
>>> 
>>> sw = BranchComponent()
>>> sw.add_branch("${num} <= 30", ["a"], "1")
>>> sw.add_branch("${num} > 30", ["end"], "2")
>>> flow.add_workflow_comp("sw", sw)
>>> 
>>> flow.add_connection("start", "a")
>>> flow.add_connection("a", "sw")
>>> asyncio.run(flow.invoke({"a": 2}, create_workflow_session()))
dump state in a: {'io_state': {'a': 2}, 'io_state_updates': {}, 'global_state': {'a': 2, 'start': {'output': 'llm'}}, 'global_state_updates': {'a': [{'num': 10, 'current_call_times': 1}]}, 'comp_state': {}, 'comp_state_updates': {'a': [{'a': {'call_times': 1}}]}, 'workflow_state': {}, 'workflow_state_updates': {}, 'trace_state': {}}
dump state in a: {'io_state': {'a': 2, 'sw': {}}, 'io_state_updates': {}, 'global_state': {'a': 2, 'start': {'output': 'llm'}, 'num': 10, 'current_call_times': 1}, 'global_state_updates': {'a': [{'num': 20, 'current_call_times': 2}]}, 'comp_state': {'a': {'call_times': 1}}, 'comp_state_updates': {'a': [{'a': {'call_times': 2}}]}, 'workflow_state': {}, 'workflow_state_updates': {}, 'trace_state': {}}
dump state in a: {'io_state': {'a': 2, 'sw': {}}, 'io_state_updates': {}, 'global_state': {'a': 2, 'start': {'output': 'llm'}, 'num': 20, 'current_call_times': 2}, 'global_state_updates': {'a': [{'num': 30, 'current_call_times': 3}]}, 'comp_state': {'a': {'call_times': 2}}, 'comp_state_updates': {'a': [{'a': {'call_times': 3}}]}, 'workflow_state': {}, 'workflow_state_updates': {}, 'trace_state': {}}
dump state in a: {'io_state': {'a': 2, 'sw': {}}, 'io_state_updates': {}, 'global_state': {'a': 2, 'start': {'output': 'llm'}, 'num': 30, 'current_call_times': 3}, 'global_state_updates': {'a': [{'num': 40, 'current_call_times': 4}]}, 'comp_state': {'a': {'call_times': 3}}, 'comp_state_updates': {'a': [{'a': {'call_times': 4}}]}, 'workflow_state': {}, 'workflow_state_updates': {}, 'trace_state': {}}
dump state in end: {'io_state': {'a': 2, 'sw': {}}, 'io_state_updates': {}, 'global_state': {'a': 2, 'start': {'output': 'llm'}, 'num': 40, 'current_call_times': 4}, 'global_state_updates': {}, 'comp_state': {'a': {'call_times': 4}}, 'comp_state_updates': {}, 'workflow_state': {}, 'workflow_state_updates': {}, 'trace_state': {}}
```

### write_stream

```python
write_stream(self, data: Union[dict, OutputSchema])
```

Output openJiuwen standard format [OutputSchema](../../session/stream/stream.md#class-openjiuwencoresessionstreamoutputschema) streaming messages, and stream out to Agent or Workflow.

**Parameters**:

- **data** (Union[dict, OutputSchema]): Streaming message. This message has strict format requirements.
  - Directly provide `OutputSchema` object, [OutputSchema](../../session/stream/stream.md#class-openjiuwencoresessionstreamoutputschema) is openJiuwen's built-in standard streaming output format.
  - Can provide `dict` type fields, but must include fields `'type'`, `'index'`, `'payload'` defined in [OutputSchema](../../session/stream/stream.md#class-openjiuwencoresessionstreamoutputschema), and data types are the same.

**Exceptions**:

- **JiuWenBaseException**: openJiuwen exception base class, for detailed information and solutions, see [StatusCode](../../common/exception/status_code.md).

**Example**:

Create custom node StreamNode, invoke outputs two streaming messages

```python
>>> import asyncio
>>> 
>>> from openjiuwen.core.workflow import WorkflowComponent
>>> from openjiuwen.core.workflow import End
>>> from openjiuwen.core.workflow import Start
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.workflow import Input, Output
>>> from openjiuwen.core.workflow.components import Session
>>> from openjiuwen.core.workflow import create_workflow_session
>>> from openjiuwen.core.session.stream import OutputSchema, BaseStreamMode
>>> from openjiuwen.core.workflow import Workflow
>>> 
>>> class StreamNode(WorkflowComponent):
...     def __init__(self):
...         super().__init__()
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         await session.write_stream(OutputSchema(type='end node stream', index=0, payload='done'))
...         await session.write_stream(dict(type='end node stream', index=1, payload='done'))
...         return inputs
... 
>>> # Create simple workflow
>>> flow = Workflow()
>>> flow.set_start_comp("start", Start())
>>> flow.add_workflow_comp("a", StreamNode())
>>> flow.set_end_comp("end", End())
>>> flow.add_connection("start", "a")
>>> flow.add_connection("a", "end")
>>>  
>>> async def run_workflow():
...     # Get streaming messages
...     async for chunk in flow.stream({}, create_workflow_session(), stream_modes=[BaseStreamMode.OUTPUT]):
...         print(chunk)
... 
>>> asyncio.run(run_workflow())
type='end node stream' index=0 payload='done'
type='end node stream' index=1 payload='done'
```

### write_custom_stream

```python
write_custom_stream(self, data: dict)
```

Output custom format [CustomSchema](../../session/stream/stream.md#class-openjiuwencoresessionstreamcustomschema) streaming messages, and stream out of Workflow.

**Parameters**:

- **data** (dict): Streaming message content.

**Exceptions**:

- **JiuWenBaseException**: openJiuwen exception base class, for detailed information and solutions, see [StatusCode](../../common/exception/status_code.md).

**Example**:

Create custom node StreamNode, invoke outputs a custom format streaming message

```python
>>> import asyncio
>>> 
>>> from openjiuwen.core.workflow import WorkflowComponent
>>> from openjiuwen.core.workflow import End
>>> from openjiuwen.core.workflow import Start
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.workflow import Input, Output
>>> from openjiuwen.core.workflow.components import Session
>>> from openjiuwen.core.workflow import create_workflow_session
>>> from openjiuwen.core.session.stream import BaseStreamMode
>>> from openjiuwen.core.workflow import Workflow
>>> 
>>> # 1. Custom implement a streaming output component
>>> class StreamNode(WorkflowComponent):
...     def __init__(self):
...         super().__init__()
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         await session.write_custom_stream(dict(content="这是一条流式消息", idx=1, message="消息"))
...         return inputs
... 
>>> # 2. Create simple workflow
>>> flow = Workflow()
>>> flow.set_start_comp("start", Start())
>>> flow.add_workflow_comp("a", StreamNode())
>>> flow.set_end_comp("end", End())
>>> flow.add_connection("start", "a")
>>> flow.add_connection("a", "end")
>>> # 3. Get streaming messages
>>> async def run_workflow():
...     async for chunk in flow.stream({}, create_workflow_session(), stream_modes=[BaseStreamMode.CUSTOM]):
...         print(chunk)
...
>>> asyncio.run(run_workflow())
content="这是一条流式消息" idx=1 message="消息"
```

### get_env

```python
get_env(self, key) -> Optional[Any]
```

Get the value of environment variables configured for this workflow execution.

**Parameters**:

- **key** (str): Environment variable key.

**Returns**:

**Optional[Any]**, the value of environment variables configured for this workflow execution.

**Example**:
```python
>>> import asyncio
>>> from openjiuwen.core.workflow import WorkflowComponent
>>> from openjiuwen.core.workflow import End
>>> from openjiuwen.core.workflow import Start
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.workflow import Input, Output
>>> from openjiuwen.core.workflow.components import Session
>>> from openjiuwen.core.workflow import create_workflow_session
>>> from openjiuwen.core.workflow import Workflow
>>> from openjiuwen.core.session import WORKFLOW_EXECUTE_TIMEOUT
>>> 
>>> class CustomComponent(WorkflowComponent):
...    def __init__(self, workflow_id):
...        super().__init__()
...        self._workflow_id = workflow_id
...
...    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...        print(f"workflow exec timeout is: {session.get_env(WORKFLOW_EXECUTE_TIMEOUT)}")
...        return {}
...
>>> flow = Workflow()
>>> flow.set_start_comp("start", Start())
>>> flow.add_workflow_comp("custom", CustomComponent("custom"))
>>> flow.set_end_comp("end", End())
>>> flow.add_connection("start", "custom")
>>> flow.add_connection("custom", "end")
>>>
>>> if __name__ == "__main__":
...    asyncio.run(flow.invoke(inputs={}, session=create_workflow_session(envs={WORKFLOW_EXECUTE_TIMEOUT: 100})))
workflow exec timeout is: 100
```
