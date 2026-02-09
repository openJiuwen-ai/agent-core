# openjiuwen.core.runner

## class openjiuwen.core.runner

Runner提供了Workflow、Agent、Tool和Group的统一执行接口。


### start

```python
async def start(self) -> bool
```

启动Runner。


**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.runner import Runner
>>> asyncio.run(Runner.start())
```

### stop

```python
async def stop(self)
```

关闭Runner。


**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.runner import Runner
>>> asyncio.run(Runner.stop())
```

### resource_mgr

```python
@property
def resource_mgr(self) -> ResourceMgr
```

获取资源管理器实例，用于管理和注册Workflow、Agent、Tool等资源。

**返回**：

[**ResourceMgr**](./resource_manager/resource_manager.md)，资源管理器实例。

### run_agent

```python
async def run_agent(
    self,
    agent: str | BaseAgent | LegacyBaseAgent,
    inputs: Any,
    *,
    session: str | Session | None = None,
    context: ModelContext | None = None,
    envs: dict[str, Any] | None = None,
) -> Any
```

执行agent并返回其结果。

**参数**:

* **agent(str|BaseAgent|LegacyBaseAgent)**：agent的ID或agent实例。不可取值为`None`或`''`。
* **inputs(Any)**：执行agent的输入数据。
* **session(str|Session)**：会话ID或会话实例。默认为`None`，表示使用默认会话。
* **context(ModelContext)**：用于存储用户对话信息的上下文引擎。默认为`None`，表示不开启上下文引擎功能。
* **envs(dict[str, Any])**：执行环境配置，例如模型参数、系统变量等。默认为`None`。

**返回**：

**Any**，agent执行结果。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.common.constants.enums import ControllerType
>>> from openjiuwen.core.single_agent.legacy import WorkflowSchema
>>> from openjiuwen.core.application.workflow_agent import WorkflowAgentConfig, WorkflowAgent
>>> from openjiuwen.core.runner import Runner
>>> from openjiuwen.core.single_agent import AgentCard
>>> from openjiuwen.core.workflow import Start, End, Workflow, WorkflowCard
>>> from openjiuwen.core.workflow.workflow_config import WorkflowConfig
>>> from openjiuwen.core.workflow import generate_workflow_key
>>>
>>> # 创建工作流flow, 将flow注册到资源管理器
>>> workflow_key = generate_workflow_key("workflow_id", "1")
>>> flow = Workflow(workflow_config=WorkflowConfig(
...     card=WorkflowCard(id=workflow_key, name="简单工作流", version="1", description="this_is_a_demo")
... ))
>>> flow.set_start_comp("start", Start(), inputs_schema={"query": "${query}"})
>>> flow.set_end_comp("end", End(), inputs_schema={"result": "${start.query}"})
>>> flow.add_connection("start", "end")
>>>
>>> # 注册工作流（需要使用正确的 key）
>>> Runner.resource_mgr.add_workflow(
...     WorkflowCard(id=workflow_key, name="简单工作流", version="1"),
...     lambda _: flow
... )
>>>
>>> # 创建Agent
>>> workflow_agent_config = WorkflowAgentConfig(
...     id="agent_id",
...     version="1",
...     description="this_is_a_demo",
...     workflows=[WorkflowSchema(
...         id="workflow_id",
...         version="1",
...         name="简单工作流",
...         description="this_is_a_demo",
...         inputs={"query": {"type": "string"}},
...     )],
...     controller_type=ControllerType.WorkflowController
... )
>>> agent = WorkflowAgent(agent_config=workflow_agent_config)
>>>
>>> # 直接调用agent实例
>>> result = asyncio.run(Runner.run_agent(agent, inputs={"conversation_id": "id1", "query": "哈哈"}))
>>> print(result)
{'output': WorkflowOutput(result={'output': {'result': '哈哈'}}, state=<WorkflowExecutionState.COMPLETED: 'COMPLETED'>), 'result_type': 'answer'}
>>>
>>> # 通过id调用agent（需要先注册agent）
>>> Runner.resource_mgr.add_agent(AgentCard(id="agent_id"), lambda _: agent)
>>> result = asyncio.run(Runner.run_agent("agent_id", inputs={"conversation_id": "id1", "query": "哈哈"}))
>>> print(result)
{'output': WorkflowOutput(result={'output': {'result': '哈哈'}}, state=<WorkflowExecutionState.COMPLETED: 'COMPLETED'>), 'result_type': 'answer'}
```

### run_workflow

```python
async def run_workflow(
    self,
    workflow: str | Workflow,
    inputs: Any,
    *,
    session: str | Session | None = None,
    context: ModelContext | None = None,
    envs: dict[str, Any] | None = None,
) -> Any
```

执行工作流并返回其执行结果。

**参数**：

* **workflow(str|Workflow)**：工作流ID或者工作流实例。不可取值为`None`或`''`。
* **inputs(Any)**：执行工作流的输入数据。
* **session(str|Session)**：会话ID或会话实例。默认为`None`，表示使用默认会话。
* **context(ModelContext)**：用于存储用户对话信息的上下文引擎。默认为`None`，表示不开启上下文引擎功能。
* **envs(dict[str, Any])**：执行环境配置，例如模型参数、系统变量等。默认为`None`。

**返回**：

**Any**，工作流执行结果。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.workflow import (
...     Workflow, WorkflowCard,
...     Start, End
... )
>>> from openjiuwen.core.workflow.workflow_config import WorkflowConfig
>>> from openjiuwen.core.runner import Runner
>>> from openjiuwen.core.workflow import generate_workflow_key
>>>
>>> # 创建工作流
>>> def build_workflow(name, workflow_id, version):
...     card = WorkflowCard(
...         id=generate_workflow_key(workflow_id, version),
...         name=name,
...         version=version,
...     )
...     workflow_config = WorkflowConfig(card=card)
...     flow = Workflow(workflow_config=workflow_config)
...     flow.set_start_comp("start", Start(),
...                         inputs_schema={
...                             "query": "${query}"})
...     flow.set_end_comp("end", End(),
...                       inputs_schema={
...                           "result": "${start.query}"})
...     flow.add_connection("start", "end")
...     return flow
...
>>> # 直接运行workflow（无需注册）
>>> workflow = build_workflow("test_workflow", "test_workflow", "1")
>>> result = asyncio.run(Runner.run_workflow(workflow=workflow, inputs={"query": "query workflow"}))
>>> print(result)
result={'output': {'result': 'query workflow'}} state=<WorkflowExecutionState.COMPLETED: 'COMPLETED'>
>>> # 指定workflow的id，执行工作流，需要将workflow添加到资源管理器中
>>> register_key = generate_workflow_key("test_workflow2", "1")
>>> Runner.resource_mgr.add_workflow(
...     WorkflowCard(id=register_key, name="test_workflow", version="1"),
...     lambda _: workflow
... )
>>> result = asyncio.run(Runner.run_workflow(workflow=register_key, inputs={"query": "query workflow"}))
>>> print(result)
result={'output': {'result': 'query workflow'}} state=<WorkflowExecutionState.COMPLETED: 'COMPLETED'>
```

### run_agent_group

```python
async def run_agent_group(
    self,
    agent_group: str | BaseGroup,
    inputs: Any,
    *,
    session: str | Session | None = None,
    context: ModelContext | None = None,
    envs: dict[str, Any] | None = None,
) -> Any
```

执行AgentGroup并返回结果。

**参数**：

* **agent_group(str|BaseGroup)**：AgentGroup的ID或AgentGroup实例。不可取值为`None`或`''`。
* **inputs(Any)**: 执行AgentGroup的输入数据。
* **session(str|Session)**：会话ID或会话实例。默认为`None`，表示使用默认会话。
* **context(ModelContext)**：用于存储用户对话信息的上下文引擎。默认为`None`，表示不开启上下文引擎功能。
* **envs(dict[str, Any])**：执行环境配置，例如模型参数、系统变量等。默认为`None`。

**返回**：

**Any**，AgentGroup执行结果。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.multi_agent import BaseGroup, GroupCard, GroupConfig
>>> from openjiuwen.core.runner import Runner
>>> from typing import Any, AsyncIterator
>>>
>>> # 创建自定义 AgentGroup
>>> class SimpleGroup(BaseGroup):
...     async def invoke(self, message: Any, session=None) -> Any:
...         # 简单示例：直接返回输入
...         return {"result": f"processed: {message}"}
...
...     async def stream(self, message: Any, session=None) -> AsyncIterator[Any]:
...         yield {"chunk": "streaming result"}
...
>>> # 创建 GroupCard 和 GroupConfig
>>> card = GroupCard(
...     name="simple_group",
...     description="简单示例组",
...     topic="demo",
...     version="1.0.0"
... )
>>> config = GroupConfig()
>>>
>>> # 实例化 group
>>> group = SimpleGroup(card=card, config=config)
>>>
>>> # 直接调用 group 实例
>>> result = asyncio.run(Runner.run_agent_group(group, inputs={"query": "hello"}))
>>> print(result)
{'result': "processed: {'query': 'hello'}"}
>>>
>>> # 通过 id 调用（需先注册到资源管理器）
>>> Runner.resource_mgr.add_agent_group(card, lambda _: group)
>>> result = asyncio.run(Runner.run_agent_group("simple_group", inputs={"query": "hello"}))
>>> print(result)
{'result': "processed: {'query': 'hello'}"}
```

### run_agent_group_streaming

```python
async def run_agent_group_streaming(
    self,
    agent_group: str | BaseGroup,
    inputs: Any,
    *,
    session: str | Session | None = None,
    context: ModelContext | None = None,
    stream_modes: list[BaseStreamMode] | None = None,
    envs: dict[str, Any] | None = None,
)
```

流式执行AgentGroup并返回结果。

**参数**：

* **agent_group(str|BaseGroup)**：ID或AgentGroup实例。不可取值为`None`或`''`。
* **inputs**: 输入数据。
* **session(str|Session)**：会话ID或会话实例。默认为`None`，表示使用默认会话。
* **context(ModelContext)**：用于存储用户对话信息的上下文引擎。默认为`None`，表示不开启上下文引擎功能。
* **stream_modes(list[BaseStreamMode])**：流式输出的类型列表。默认为`None`，表示使用默认流式模式。
* **envs(dict[str, Any])**：执行环境配置，例如模型参数、系统变量等。默认为`None`。

**返回**：

**AsyncGenerator**，流式结果生成器。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.multi_agent import BaseGroup, GroupCard, GroupConfig
>>> from openjiuwen.core.runner import Runner
>>> from typing import Any, AsyncIterator
>>>
>>> # 创建自定义 AgentGroup（支持流式）
>>> class StreamingGroup(BaseGroup):
...     async def invoke(self, message: Any, session=None) -> Any:
...         return {"result": f"processed: {message}"}
...
...     async def stream(self, message: Any, session=None) -> AsyncIterator[Any]:
...         yield {"chunk": 1, "data": "first"}
...         yield {"chunk": 2, "data": "second"}
...
>>> # 创建 GroupCard 和 GroupConfig
>>> card = GroupCard(
...     name="streaming_group",
...     description="流式示例组",
...     topic="demo",
...     version="1.0.0"
... )
>>> config = GroupConfig()
>>>
>>> # 实例化 group
>>> group = StreamingGroup(card=card, config=config)
>>>
>>> # 流式调用
>>> async def run_streaming():
...     result = Runner.run_agent_group_streaming(group, inputs={"query": "hello"})
...     async for chunk in result:
...         print(chunk)
...
>>> asyncio.run(run_streaming())
{'chunk': 1, 'data': 'first'}
{'chunk': 2, 'data': 'second'}
>>>
>>> # 通过 id 流式调用（需先注册）
>>> Runner.resource_mgr.add_agent_group(card, lambda _: group)
>>> async def run_streaming_by_id():
...     result = Runner.run_agent_group_streaming("streaming_group", inputs={"query": "hello"})
...     async for chunk in result:
...         print(chunk)
...
>>> asyncio.run(run_streaming_by_id())
{'chunk': 1, 'data': 'first'}
{'chunk': 2, 'data': 'second'}
```

### release

```python
async def release(self, session_id: str)
```

清理指定`session_id`的缓存数据，如中断状态数据。

**参数**：

* **session_id(str)**: 对话ID。若为`None`，则不清理。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.runner import Runner
>>> asyncio.run(Runner.release(session_id="session_1"))
```