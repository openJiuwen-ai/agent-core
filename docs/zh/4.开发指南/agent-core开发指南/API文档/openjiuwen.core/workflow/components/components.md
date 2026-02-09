# openjiuwen.core.workflow

## class openjiuwen.core.workflow.components.component.WorkflowComponent

> **注意**：组件类通过 `openjiuwen.core.workflow` 模块导出。建议使用 `from openjiuwen.core.workflow import ComponentComposable` 导入。

`ComponentComposable`是自定义工作流组件的抽象基类。所有自定义组件需继承该类，并实现将自身加入图以及转换为可执行单元（`ComponentExecutable`）的逻辑。

### add_component(graph: Graph, node_id: str, wait_for_all: bool = False) -> None:

定义如何将当前组件加入到指定`graph`。

**参数**：

- **graph**([Graph](../../graph/graph.md#class-graph))：`workflow`的`graph`实例。
- **node_id**(str)：本组件在`graph`中唯一的`node_id`。
- **wait_for_all**(bool)：本组件是否需要等待前置组件全部执行完成，`True`表示等待，`False`表示不等待。默认值：`False`。

**样例**：

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
>>> # 自定义组件`CustomIntentComponent`实现了`add_component`接口，用于为本组件定制内置的条件边，并自定义实现了接口`add_branch`用于绑定内置条件边的目标节点。
>>> # 创建自定义组件，并实现add_component接口
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
...     # 创建工作流
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

返回该组件对应的可执行实例，openJiuwen支持用户自定义实现`ComponentExecutable`，作为本接口的返回，用于运行时调度执行。

**返回**：

**[Executable](../../graph/graph.md)**，本组件的执行实例。

**样例**：

```python

>>> import asyncio
>>>
>>> from openjiuwen.core.context_engine import ModelContext 
>>> from openjiuwen.core.graph.executable import Executable
>>> from openjiuwen.core.workflow.components import Session
>>> from openjiuwen.core.workflow import create_workflow_session
>>> from openjiuwen.core.workflow import ComponentExecutable, End, Input, Output, Start, Workflow, ComponentComposable
>>> 
>>> # `CalculateComponent`继承`ComponentComposable`，是用于数学运算的组件，实现`to_executable`方法提供了实现计算逻辑的`ComputeExecutor`实例，实现`add_component`方法将`CalculateComponent`节点添加到工作流中。
>>> # 创建自定义组件，并实现to_executable接口
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
...     # 创建工作流，并执行
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

组件执行时的会话，不需要手动创建，组件的`invoke/stream/collect/transform`接口参数携带了这个会话。

**参数**：

- **session** (NodeSession)：内部的组件session。
- **stream_mode** (bool, 可选)：是否为流式输入接口，默认为`False`。

### get_workflow_id

```python
get_workflow_id(self)
```

获取当前组件所属工作流的`id`。

**返回**：

**str**：获取当前组件所属工作流的`id`。

**样例**：

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

获取当前组件的`id`。

**返回**：

**str**：当前组件的`id`。

**样例**：

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

获取当前组件的类型标识。

**返回**：

**str**：当前组件的类型。

**样例**：

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

输出运行信息的流式消息，并以[TraceSchema](../../session/stream/stream.md#class-openjiuwencoresessionstreamtraceschema)格式的流式消息流出到Agent或者Workflow外，方便用户深入了解内部的运行机制，确保流程按照预期进行，同时能够快速找到问题所在，提高工作效率。

**参数**：

- **data**(dict)：需要溯源记录的运行数据。该数据将会填充到openJiuwen内置的流式消息格式[TraceSchema](../../session/stream/stream.md#class-openjiuwencoresessionstreamtraceschema)中payload结构中的`onInvokeData`字段，当为`None`时，表示没有运行数据。

**样例**：

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
>>> # 1. 自定义实现一个记录trace流信息的组件
>>> class StreamNode(WorkflowComponent):
...     def __init__(self):
...         super().__init__()
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         await session.trace(dict(content="记录trace信息"))
...         return inputs
... 
>>> # 2. 创建工作流
>>> flow = Workflow()
>>> flow.set_start_comp("start", Start(), inputs_schema={"a": "${a}"})
>>> flow.add_workflow_comp("a", StreamNode(), inputs_schema={"aa": "${start.a}"})
>>> flow.set_end_comp("end", End())
>>> flow.add_connection("start", "a")
>>> flow.add_connection("a", "end")
>>> 
>>> async def run_workflow():
...     # 3. 执行工作流，并获取流式输出数据
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

输出运行过程中的错误信息，并以[TraceSchema](../../session/stream/stream.md#class-openjiuwencoresessionstreamtraceschema)格式的流式消息输出到Workflow或者Agent外。

**参数**：

- **error**(Exception)：错误异常信息。该数据将会填充到openJiuwen内置的流式消息格式[TraceSchema](../../session/stream/stream.md#class-openjiuwencoresessionstreamtraceschema)中payload结构中的`error`字段。

**异常**：

- **JiuWenBaseException**：openJiuwen异常基类，具体详细信息和解决方法，参见[StatusCode](../../common/exception/status_code.md)。

**样例**：

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
>>> # 1. 创建自定义组件，并且流出一条trace error的流式消息
>>> class StreamNode(WorkflowComponent):
...     def __init__(self):
...         super().__init__()
...     
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         await session.trace_error(JiuWenBaseException(error_code=-1, message="这是一条错误信息"))
...         return inputs
...     
>>> # 2. 创建工作流
>>> flow = Workflow()
>>> flow.set_start_comp("start", Start(), inputs_schema={"a": "${a}"})
>>> flow.add_workflow_comp("a", StreamNode(), inputs_schema={"aa": "${start.a}"})
>>> flow.set_end_comp("end", End())
>>> flow.add_connection("start", "a")
>>> flow.add_connection("a", "end")
>>>
>>> # 3. 执行工作流
>>> async def run_workflow():
...     async for chunk in flow.stream({"a": 1}, create_workflow_session(), stream_modes=[BaseStreamMode.TRACE]):
...         # 用于过滤带有error信息、且不为最后一帧的调测信息
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

中断当前Agent或者Workflow执行，与用户进行交互，并等待用户补充输入[InteractionInput](../../session/session.md#class-openjiuwencoresessioninteractiveinput)后，从中断位置继续执行。该接口目前仅支持在组件的非流式输入模式下的`invoke`和`stream`接口中使用。
当工作流中具有提问节点，提问节点产生提问需求，可以调用该接口，并将问题信息作为value返回给用户，直到该用户输入新的回答，工作流从调用该接口的位置恢复执行。

**参数**：

- **value**(Any)：反馈给用户的中断提示信息，若取值为`None`，表示没有中断提示信息。根据中断发生的场景不同，该信息最终返回给用户的位置也有差异。
  - 当中断发生在工作流的`invoke`执行过程中，value会被填充到一个`WorkflowChunk`的`payload`(类型为`InteractionOutput`)中，并作为工作流执行结果`WorkflowOutput.result`列表的一个item返回。
  - 当中断发生在工作流的`stream`执行过程中，value会被填充到一帧`OutputSchema`格式的流式信息的`payload`(类型为`InteractionOutput`)中，并流出到工作流外，该流式信息的type为`INTERACTION`。

**返回**：

**Any**：用户补充输入[InteractionInput](../../session/session.md#class-openjiuwencoresessioninteractiveinput)中的`raw_inputs`或者`value`。

**样例**：

- 样例一：工作流的`invoke`执行过程中，出现中断交互。

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
>>> # 1.  创建一个带中断交互的节点
>>> class InteractiveNode(WorkflowComponent):
...     def __init__(self):
...         super().__init__()
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         cmd = inputs.get("cmd")
...         # 向用户提问, 等待用户回答
...         result = await session.interact(f"Do you want to execute the command '{cmd}'?")
...         return {"confirm_result": result}
... 
>>> # 2. 创建简单工作流
>>> flow = Workflow()
>>> flow.set_start_comp("start", Start(), inputs_schema={"command1": "${cmd1}"})
>>> flow.add_workflow_comp("interactive_node1", InteractiveNode(), inputs_schema={"cmd": "${start.command1}"}, outputs_schema={"result": "${confirm_result}"})
>>> flow.set_end_comp("end", End(), inputs_schema={"result1": "${interactive_node1.result}"})
>>> flow.add_connection("start", "interactive_node1")
>>> flow.add_connection("interactive_node1", "end")
>>> 
>>> session_id = "session_id"
>>> #3.  执行工作流
>>> output = asyncio.run(flow.invoke({"cmd1": "delete all files"}, create_workflow_session(session_id=session_id)))
>>> 
>>> if isinstance(output.result[0].payload, InteractionOutput):
...     # 获取中断节点id
...     node_id = output.result[0].payload.id
...     print(f'node_id={output.result[0].payload.id}, value={output.result[0].payload.value}')
... 
node_id=interactive_node1, value=Do you want to execute the command 'delete all files'?
>>> 
>>> # 4. 用户向中断节点输入交互补充信息
>>> user_input = InteractiveInput()
>>> user_input.update(node_id, "Yes")
>>> 
>>> # 5. 执行invoke
>>> output = asyncio.run(flow.invoke(user_input, create_workflow_session(session_id=session_id)))
>>> print(output.result.get("output").get("result1"))
Yes
```

- 样例二：工作流的`stream`执行过程中，出现中断交互。

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
>>> # 1. 自定义交互节点
>>> class InteractiveStreamNode(WorkflowComponent):
...     def __init__(self):
...         super().__init__()
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         # 中断执行，等待用户补充信息
...         result = await session.interact("Please enter any key")
... 
...         # 将用户补充的userInput信息作为一帧流式消息输出
...         await session.write_stream(OutputSchema(type="output", index=0, payload=result))
...         return result
... 
>>> # 2. 创建工作流, 并触发执行stream
>>> flow = Workflow()
>>> flow.set_start_comp("start", Start(), inputs_schema={"a": "${inputs.a}",})
>>> flow.add_workflow_comp("interactive_node1", InteractiveStreamNode(),inputs_schema={ "aa": "${start.a}"},)
>>> flow.set_end_comp("end", End(), inputs_schema={"result": "${interactive_node1.aa}"})
>>> flow.add_connection("start", "interactive_node1")
>>> flow.add_connection("interactive_node1", "end")
>>> 
>>> # 3. 执行stream
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

返回为组件的全局唯一ID。

**返回**：

**str**：组件的全局唯一ID。

**样例**：

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

获取本次工作流执行的唯一会话标识。

**返回**：

**str**：当前工作流的唯一会话标识。

**样例**：

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

更新当前组件的状态数据的状态数据，在组件执行完生效，若不存在字段会新增。

**参数**：

- **data**(dict)：新增或更新的键值对字典。若data为None或{}，表示不更新。

**样例**：

更新状态信息

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
>>> # 2.  创建工作流并执行，工作流中含有loop组件，loop中包含自定义组件
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
>>> # 3. 执行工作流
>>> asyncio.run(flow.invoke({"user_inputs": {"query": "你好", "loop_number": 3}}, create_workflow_session()))
[a] current state is {'recall_times': 1, 'messages': ['你好']}
[a] current state is {'recall_times': 2, 'messages': ['你好', '你好']}
[a] current state is {'recall_times': 3, 'messages': ['你好', '你好', '你好']}
```

### get_state

```python
get_state(self, key: Union[str, list, dict] = None) -> Any
```

- 获取当前组件的状态信息。

**参数**：

- **key** (Union[str, list, dict], 可选)：查询状态数据中对应value的key，默认`None`，表示获取全部的状态信息。根据`key`的取值类型，支持多种方式获取状态数据。
  - 当`key`为`str`类型，表示获取`key`路径下的状态数据，key可以为嵌套路径结构，例如`"user_inputs.query"`。
  - 当`key`为`dict`\ `list`类型，表示获取多个状态数据，该`dict`\ `list`中存在多个用`"${}"`包裹的变量，每个变量都是一个状态数据的路径。

**返回**：

**Any**：返回的状态值。根据`key`的类型的不同，返回结果为：

- 当`key`为`str`类型，返回为该路径下的状态信息，若不存在，则返回`None`。
- 当`key`为`dict`\ `list`类型，该接口会将输入的`dict`\ `list`中所有`${}`内的变量替换成相应路径的变量的值，若对应变量路径不存在，则替换为`None`，最终返回为替换完的结果。

**样例**：

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
...        # 获取单个状态
...        recall_times = session.get_state("recall_times")
...        # 获取多个状态
...        my_state = session.get_state({"recall_times": "${recall_times}", "messages": "${messages}"})
...        # 获取列表状态
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
>>> # 2.  创建工作流并执行，工作流中含有loop组件，loop中包含自定义组件
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
>>> # 3. 执行工作流
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

更新**全局共享**状态，生效时间为当前组件执行完。

**参数**：

- **data**(dict)：新增或更新的键值对字典。若入参为None或{}，表示不更新。

**样例**：

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
>>> # 1. 实现自定义组件, 用于全局状态每次进行+10的操作 
>>> class AddTenComponent(WorkflowComponent):
...     def __init__(self, node_id):
...         super().__init__()
...         self._node_id = node_id
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         call_times = session.get_state("call_times")
...         # 获取当前全局状态num
...         num = session.get_global_state("num")
...         if not num:
...             num = 0
...         if not call_times:
...             call_times = 0
...         call_times += 1
...         print(f"begin to invoke, call_times={call_times}, num={num}, operation: num = {num} + 10")
...         num += 10
...         session.update_global_state({"num": num})
...         # 更新当前全局状态
...         session.update_state({"call_times": call_times})
...         return inputs 
... 
>>> # 2. 自定义组件，用于工作流结束时读取全局状态num
>>> class CustomEnd(WorkflowComponent):
...     def __init__(self, node_id):
...         super().__init__()
...         self._node_id = node_id
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         print(f'->[{self._node_id}] get num = {session.get_global_state("num")}')
...         return inputs
... 
>>> # 3. 创建工作流，并且执行工作流
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

读取全局共享状态数据，支持灵活的数据提取。

**参数**：

- **key** (Union[str, list, dict], 可选)：查询状态数据中对应value的key，默认`None`，表示获取全部的状态信息。根据`key`的取值类型，支持多种方式获取状态数据。
  - 当`key`为`str`类型，表示获取`key`路径下的状态数据，key可以为嵌套路径结构，例如`"user_inputs.query"`。
  - 当`key`为`dict`\ `list`类型，表示获取多个状态数据，该`dict`\ `list`中存在多个用`"${}"`包裹的变量，每个变量都是一个状态数据的路径。

**返回**：

根据`key`的类型的不同，返回结果为：

- 当`key`为`str`类型，返回为该路径下的状态信息，若不存在，则返回`None`。
- 当`key`为`dict`\ `list`类型，该接口会将输入的`dict`\ `list`中所有`${}`内的变量替换成相应路径的变量的值，若对应变量路径不存在，则替换为`None`，最终返回为替换完的结果。

**样例**：

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
>>> # 1. 实现自定义组件, 用于全局状态每次进行+10的操作
>>> class AddTenComponent(WorkflowComponent):
...     def __init__(self, node_id):
...         super().__init__()
...         self._node_id = node_id
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         call_times = session.get_state("call_times")
... 
...         # 获取当前全局状态num
...         num = session.get_global_state("num")
...         # 获取多个全局状态
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
...         # 更新当前全局状态
...         session.update_state({"call_times": call_times})
...         return inputs
... 
>>> # 2. 自定义组件，用于工作流结束时读取全局状态num
>>> class CustomEnd(WorkflowComponent):
...     def __init__(self, node_id):
...         super().__init__()
...         self._node_id = node_id
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         print(f'->[{self._node_id}] get num = {session.get_global_state("num")}')
...         return inputs
...  
>>> # 3. 创建工作流，并且执行工作流
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

### write_stream

```python
write_stream(self, data: Union[dict, OutputSchema])
```

输出openJiuwen标准格式[OutputSchema](../../session/stream/stream.md#class-openjiuwencoresessionstreamoutputschema)流式消息，并流出到Agent或者Workflow外。

**参数**：

- **data** (Union[dict, OutputSchema])：流式消息。该消息有严格的格式规定。
  - 直接提供`OutputSchema`对象，[OutputSchema](../../session/stream/stream.md#class-openjiuwencoresessionstreamoutputschema)为openJiuwen内置的标准流式输出格式。
  - 可以提供`dict`类型的字段，但是必须包含[OutputSchema](../../session/stream/stream.md#class-openjiuwencoresessionstreamoutputschema)中定义的字段`'type'`、`'index'`、`'payload'`，并且数据类型相同。

**异常**：

- **JiuWenBaseException**：openJiuwen异常基类，具体详细信息和解决方法，参见[StatusCode](../../common/exception/status_code.md)。

**样例**：

创建自定义节点StreamNode，invoke中输出了两条流式消息

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
>>> # 创建简单工作流
>>> flow = Workflow()
>>> flow.set_start_comp("start", Start())
>>> flow.add_workflow_comp("a", StreamNode())
>>> flow.set_end_comp("end", End())
>>> flow.add_connection("start", "a")
>>> flow.add_connection("a", "end")
>>>  
>>> async def run_workflow():
...     # 获取流式消息
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

输出自定义格式[CustomSchema](../../session/stream/stream.md#class-openjiuwencoresessionstreamcustomschema)的流式消息，并流出到Workflow外。

**参数**：

- **data** (dict)：流式消息内容。

**异常**：

- **JiuWenBaseException**：openJiuwen异常基类，具体详细信息和解决方法，参见[StatusCode](../../common/exception/status_code.md)。

**样例**：

创建自定义节点StreamNode，invoke中输出了一条自定义格式的流式消息

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
>>> # 1. 自定义实现一个流式输出的组件
>>> class StreamNode(WorkflowComponent):
...     def __init__(self):
...         super().__init__()
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         await session.write_custom_stream(dict(content="这是一条流式消息", idx=1, message="消息"))
...         return inputs
... 
>>> # 2. 创建简单工作流
>>> flow = Workflow()
>>> flow.set_start_comp("start", Start())
>>> flow.add_workflow_comp("a", StreamNode())
>>> flow.set_end_comp("end", End())
>>> flow.add_connection("start", "a")
>>> flow.add_connection("a", "end")
>>> # 3. 获取流式消息
>>> async def run_workflow():
...     async for chunk in flow.stream({}, create_workflow_session(), stream_modes=[BaseStreamMode.CUSTOM]):
...         print(chunk)
...
>>> asyncio.run(run_workflow())
content="这是一条流式消息" idx=1 message="消息"
```

### get_callback_manager

```python
get_callback_manager(self) -> CallbackManager
```

获取本次工作流执行的回调函数管理器。

**返回**：

**CallbackManager**：当前工作流执行的回调函数管理器。

**样例**：

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
...        callback_manager = session.get_callback_manager()
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
```

### get_env

```python
get_env(self, key) -> Optional[Any]
```

获取本次工作流执行配置的环境变量的值。

**参数**：

- **key** (str)：环境变量的键。

**返回**：

**Optional[Any]**，为本次工作流执行配置的环境变量的值。

**样例**：
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