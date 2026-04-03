# openjiuwen.core.runner

## class openjiuwen.core.runner

Runner提供了Workflow、Agent的统一执行接口。

Runner是一个单例类，所有方法调用和属性访问都会自动代理到全局的Runner实例（`GLOBAL_RUNNER`）。无需实例化Runner，直接通过类名调用即可。


### start

```python
@classmethod
async start(cls) -> bool
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
@classmethod
async stop(cls)
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
resource_mgr: ResourceMgr
```

获取资源管理器实例，用于管理和注册Workflow、Agent、Tool等资源。

这是一个类属性，可以直接通过 `Runner.resource_mgr` 访问。

**返回**：

[**ResourceMgr**](./resource_manager/resource_manager.md)，资源管理器实例。

### callback_framework

```python
callback_framework: AsyncCallbackFramework
```

获取异步回调框架实例，用于注册事件回调、链式执行、过滤与指标等。详见 [callback 模块](./callback/callback.README.md)。

**返回**：[**AsyncCallbackFramework**](./callback/framework.md)，异步回调框架实例。

### set_config

```python
@classmethod
set_config(cls, config: RunnerConfig) -> None
```

设置 Runner 的配置。

**参数**：**config**(RunnerConfig)：包含配置项的对象。

### get_config

```python
@classmethod
get_config(cls) -> RunnerConfig
```

获取当前 Runner 的配置。

**返回**：**RunnerConfig**，当前配置对象。

### run_agent

```python
@classmethod
async run_agent(
    cls,
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

* **agent**(str|BaseAgent|LegacyBaseAgent)：agent的ID或agent实例。不可取值为`None`或`''`。
* **inputs**(Any)：执行agent的输入数据。
* **session**(str|Session)：会话ID或会话实例。默认为`None`，表示使用默认会话。
* **context**(ModelContext)：用于存储用户对话信息的上下文引擎。默认为`None`，表示不开启上下文引擎功能。
* **envs**(dict[str, Any])：执行环境配置，例如模型参数、系统变量等。默认为`None`。

**返回**：

**Any**，agent执行结果。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.common.constants.enums import ControllerType
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
>>> # 创建Agent，使用 WorkflowCard 描述工作流输入参数
>>> workflow_card = WorkflowCard(
...     id="workflow_id",
...     version="1",
...     name="简单工作流",
...     description="this_is_a_demo",
...     input_params={"query": {"type": "string"}},
... )
>>> workflow_agent_config = WorkflowAgentConfig(
...     id="agent_id",
...     version="1",
...     description="this_is_a_demo",
...     workflows=[workflow_card],
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
@classmethod
async run_workflow(
    cls,
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

* **workflow**(str|Workflow)：工作流ID或者工作流实例。不可取值为`None`或`''`。
* **inputs**(Any)：执行工作流的输入数据。
* **session**(str|Session)：会话ID或会话实例。默认为`None`，表示使用默认会话。
* **context**(ModelContext)：用于存储用户对话信息的上下文引擎。默认为`None`，表示不开启上下文引擎功能。
* **envs**(dict[str, Any])：执行环境配置，例如模型参数、系统变量等。默认为`None`。

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

### release

```python
@classmethod
async release(cls, session_id: str)
```

清理指定`session_id`的缓存数据，如中断状态数据。

**参数**：

* **session_id**(str): 对话ID。若为`None`，则不清理。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.runner import Runner
>>> asyncio.run(Runner.release(session_id="session_1"))
```