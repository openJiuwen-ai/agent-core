# openjiuwen.core.session

## class openjiuwen.core.session.InteractiveInput

```python
class openjiuwen.core.session.InteractiveInput(raw_inputs: Any)
```

中断场景下构造用户与工作流的交互输入。

**参数**：

- **raw_inputs**(Any)：用户交互输入的信息，并且作为工作流中断恢复时返回的信息。传入None会抛出异常。

**异常**:

- **JiuWenBaseException**：openJiuwen异常基类，具体详细信息和解决方法，参见[StatusCode](../common/exception/status_code.md)。

**样例**：

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
>>> # 创建一个带中断交互的工作流组件
>>> class InteractiveNode(WorkflowComponent):
...     def __init__(self):
...         super().__init__()
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         cmd = inputs.get("cmd")
...         # 向用户提问, 等待用户回答
...         user_input_ = await session.interact(f"Do you want to execute the command '{cmd}'?")
...         return {"result": user_input_}
...  
>>> # 创建工作流，执行工作流触发中断交互流程
>>> flow = Workflow()
>>> flow.set_start_comp("start", Start(), inputs_schema={"command1": "${cmd1}"})
>>> flow.add_workflow_comp("interactive_node1", InteractiveNode(), inputs_schema={"cmd": "${start.command1}"})
>>> flow.set_end_comp("end", End(), inputs_schema={"result1": "${interactive_node1.result}"})
>>> flow.add_connection("start", "interactive_node1")
>>> flow.add_connection("interactive_node1", "end")
>>> 
>>> session_id = uuid.uuid4().hex
>>> 
>>> # 执行工作流
>>> output = asyncio.run(flow.invoke({"cmd1": "delete all files"}, create_workflow_session(session_id=session_id)))
>>> 
>>> # 判断结果是否为中断交互输出
>>> if isinstance(output.result[0].payload, InteractionOutput):
...     # 获取中断组件的提示信息
...     print(output.result[0].payload.value)
...
Do you want to execute the command 'delete all files'?
>>> 
>>> # 创建`InteractiveInput`进行交互输入,  并恢复工作流执行
>>> 
>>> user_input = InteractiveInput(raw_inputs='是的')
>>> output = asyncio.run(flow.invoke(user_input, create_workflow_session(session_id=session_id)))
>>> print(output.result.get("output").get("result1"))
是的
```

> **说明**
>
> - output为工作流输出，详细见[工作流输出介绍](../workflow/workflow.md)。
> - 工作流首次输出是`Do you want to execute the command '{delete all files}'`，此时output里的result为列表，列表中每个元素为流式输出的元素，详细见[流式输出介绍](../../../../agent-core开发指南/高阶用法/Session/流式输出.md)；payload中的内容为中断输出，详细见[中断输出介绍](#class-openjiuwencoresessioninteractionoutput)。
> - 通过`InteractiveInput`构造用户交互输入后，再次输出是`是的`，此时output里的result为结束组件的输出，详细见[结束组件介绍](../workflow/components/flow/end_comp.md)。

### update

```python
update(self, node_id: str, value: Any)
```

为指定组件提供用户交互输入信息，一般用于具有多个中断恢复的组件的工作流，当发生交互中断时，需要为每个中断组件提供不同的交互信息。如果已通过构造函数和`raw_inputs`构造，则调用该接口会抛出异常。

**参数**：

- **node_id**(str)：中断组件的id。传入None会抛出异常。
- **value**(Any)：用户输入，会作为[interact]接口的返回值。传入None会抛出异常。

**异常**:

- **JiuWenBaseException**：openJiuwen异常基类，具体详细信息和解决方法，参见[StatusCode](../common/exception/status_code.md)。

**样例**：

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
>>> # 创建一个带有中断交互功能的组件
>>> class InteractiveNode(WorkflowComponent):
...     def __init__(self):
...         super().__init__()
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         cmd = inputs.get("cmd")
...         # 向用户提问, 等待用户回答
...         user_input_ = await session.interact(f"Do you want to execute the command '{cmd}'?")
...         return {"result": user_input_}
... 
>>> # 构建具有两个中断交互功能的工作流, 并执行工作流，触发两个组件组件的中断流程
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
>>> # 为中断组件组件提供不同的交互输入信息
>>> print("----------第二轮------------")
----------第二轮------------
>>> user_input = InteractiveInput()
>>> user_input.update(node_id_1, "Yes")
>>> user_input.update(node_id_2, "No")
>>> 
>>> output = asyncio.run(flow.invoke(user_input, create_workflow_session(session_id=session_id)))
>>> print(output.result.get("output"))
{'result1': 'Yes', 'result2': 'No'}
>>> 
```

> **说明**
>
> - 其中output为工作流输出，详细见[工作流输出介绍](../workflow/workflow.md)。
> - 工作流首次输出包含`interactive_node1`、`Do you want to execute the command 'delete all files'?`、`interactive_node2`、`Do you want to execute the command 'kill all processes'?`，此时output的result为列表，列表中每个元素为流式输出的元素，详细见[流式输出介绍](../../../../agent-core开发指南/高阶用法/Session/流式输出.md)；payload中的内容为中断输出，详细见[中断输出介绍](#class-openjiuwencoresessioninteractionoutput)。
> - 通过`InteractiveInput`构造用户交互输入后，再次输出为`{'result1': 'Yes', 'result2': 'No'}`，此时output的result为结束组件的输出，详细见[结束组件介绍](../workflow/components/flow/end_comp.md)。

## class openjiuwen.core.session.InteractionOutput

```python
class openjiuwen.core.session.InteractionOutput
```

工作流与用户的交互输出的数据类，主要用在中断场景下。一般作为[WorkflowChunk](../workflow/workflow.md)的`payload`。

- **id**(str)：中断组件的id。
- **value**(Any)：中断组件发生中断的提示信息。

**样例**：

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
>>> # 创建一个带中断交互的工作流组件
>>> class InteractiveNode(WorkflowComponent):
...     def __init__(self):
...         super().__init__()
... 
...     async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
...         cmd = inputs.get("cmd")
...         # 向用户提问, 等待用户回答
...         user_input_ = await session.interact(f"Do you want to execute the command '{cmd}'?")
...         return {"user_input": user_input_}
... 
>>> # 创建工作流，执行工作流触发中断交互流程
>>> flow = Workflow()
>>> flow.set_start_comp("start", Start(), inputs_schema={"command1": "${cmd1}"})
>>> flow.add_workflow_comp("interactive_node1", InteractiveNode(), inputs_schema={"cmd": "${start.command1}"})
>>> flow.set_end_comp("end", End(), inputs_schema={ "result1": "${interactive_node1.result}"})
>>> flow.add_connection("start", "interactive_node1")
>>> flow.add_connection("interactive_node1", "end")
>>> 
>>> session_id = uuid.uuid4().hex
>>> # 执行工作流
>>> output = asyncio.run(flow.invoke({"cmd1": "delete all files"}, create_workflow_session(session_id=session_id)))
>>> 
>>> # 判断结果是否为中断交互输出信息
>>> if isinstance(output.result[0].payload, InteractionOutput):
...     # 获取中断组件的提示信息
...     print(output.result[0].payload.id)
...
interactive_node1
>>>     print(output.result[0].payload.value)
Do you want to execute the command 'delete all files'
```

> **说明**
>
> - 其中output为工作流输出，详细见[工作流输出介绍](../workflow/workflow.md)。
> - 工作流输出包含`interactive_node1`、`Do you want to execute the command '{delete all files}'`，此时output的result为列表，列表中每个元素为流式输出的元素，详细见[流式输出介绍](../../../../agent-core开发指南/高阶用法/Session/流式输出.md)；payload中的内容为中断输出，即本章介绍的`InteractionOutput`。
