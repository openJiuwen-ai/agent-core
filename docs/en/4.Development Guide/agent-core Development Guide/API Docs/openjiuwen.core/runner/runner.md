# openjiuwen.core.runner

## class openjiuwen.core.runner

Runner provides a unified execution interface for Workflow, Agent, Tool, and Group.

Runner is a singleton class. All method calls and property accesses are automatically proxied to the global Runner instance (`GLOBAL_RUNNER`). You don't need to instantiate Runner; simply call methods directly through the class name.


### start

```python
@classmethod
async def start(cls) -> bool
```

Start the Runner.


**Example**:

```python
>>> import asyncio
>>> from openjiuwen.core.runner import Runner
>>> asyncio.run(Runner.start())
```

### stop

```python
@classmethod
async def stop(cls)
```

Stop the Runner.


**Example**:

```python
>>> import asyncio
>>> from openjiuwen.core.runner import Runner
>>> asyncio.run(Runner.stop())
```

### resource_mgr

```python
resource_mgr: ResourceMgr
```

Get the resource manager instance for managing and registering resources such as Workflow, Agent, Tool, etc.

This is a class property that can be accessed directly via `Runner.resource_mgr`.

**Returns**:

[**ResourceMgr**](./resource_manager/resource_manager.md), resource manager instance.

### callback_framework

```python
callback_framework: AsyncCallbackFramework
```

Get the async callback framework instance for registering event callbacks, chaining, filtering, and metrics. See [callback module](callback/callback.README.md).

**Returns**: **AsyncCallbackFramework**, the async callback framework instance.

### set_config

```python
@classmethod
def set_config(cls, config: RunnerConfig) -> None
```

Set the Runner configuration.

**Parameters**: **config** (RunnerConfig): Configuration object.

### get_config

```python
@classmethod
def get_config(cls) -> RunnerConfig
```

Get the current Runner configuration.

**Returns**: **RunnerConfig**, current configuration object.

### run_agent

```python
@classmethod
async def run_agent(
    cls,
    agent: str | BaseAgent | LegacyBaseAgent,
    inputs: Any,
    *,
    session: str | Session | None = None,
    context: ModelContext | None = None,
    envs: dict[str, Any] | None = None,
) -> Any
```

Execute an agent and return its result.

**Parameters**:

* **agent** (str | BaseAgent | LegacyBaseAgent): Agent ID or agent instance. Cannot be `None` or `''`.
* **inputs** (Any): Input data for executing the agent.
* **session** (str | Session): Session ID or session instance. Default is `None`, indicating use of default session.
* **context** (ModelContext): Context engine for storing user conversation information. Default is `None`, indicating context engine functionality is not enabled.
* **envs** (dict[str, Any]): Execution environment configuration, such as model parameters, system variables, etc. Default is `None`.

**Returns**:

**Any**, agent execution result.

**Example**:

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
>>> # Create workflow flow, register flow to resource manager
>>> workflow_key = generate_workflow_key("workflow_id", "1")
>>> flow = Workflow(workflow_config=WorkflowConfig(
...     card=WorkflowCard(id=workflow_key, name="Simple Workflow", version="1", description="this_is_a_demo")
... ))
>>> flow.set_start_comp("start", Start(), inputs_schema={"query": "${query}"})
>>> flow.set_end_comp("end", End(), inputs_schema={"result": "${start.query}"})
>>> flow.add_connection("start", "end")
>>>
>>> # Register workflow (need to use correct key)
>>> Runner.resource_mgr.add_workflow(
...     WorkflowCard(id=workflow_key, name="Simple Workflow", version="1"),
...     lambda _: flow
... )
>>>
>>> # Create Agent
>>> workflow_agent_config = WorkflowAgentConfig(
...     id="agent_id",
...     version="1",
...     description="this_is_a_demo",
...     workflows=[WorkflowSchema(
...         id="workflow_id",
...         version="1",
...         name="Simple Workflow",
...         description="this_is_a_demo",
...         inputs={"query": {"type": "string"}},
...     )],
...     controller_type=ControllerType.WorkflowController
... )
>>> agent = WorkflowAgent(agent_config=workflow_agent_config)
>>>
>>> # Invoke agent instance directly
>>> result = asyncio.run(Runner.run_agent(agent, inputs={"conversation_id": "id1", "query": "hello"}))
>>> print(result)
{'output': WorkflowOutput(result={'output': {'result': 'hello'}}, state=<WorkflowExecutionState.COMPLETED: 'COMPLETED'>), 'result_type': 'answer'}
>>>
>>> # Invoke agent by id (need to register agent first)
>>> Runner.resource_mgr.add_agent(AgentCard(id="agent_id"), lambda _: agent)
>>> result = asyncio.run(Runner.run_agent("agent_id", inputs={"conversation_id": "id1", "query": "hello"}))
>>> print(result)
{'output': WorkflowOutput(result={'output': {'result': 'hello'}}, state=<WorkflowExecutionState.COMPLETED: 'COMPLETED'>), 'result_type': 'answer'}
```

### run_workflow

```python
@classmethod
async def run_workflow(
    cls,
    workflow: str | Workflow,
    inputs: Any,
    *,
    session: str | Session | None = None,
    context: ModelContext | None = None,
    envs: dict[str, Any] | None = None,
) -> Any
```

Execute a workflow and return its execution result.

**Parameters**:

* **workflow** (str | Workflow): Workflow ID or workflow instance. Cannot be `None` or `''`.
* **inputs** (Any): Input data for executing the workflow.
* **session** (str | Session): Session ID or session instance. Default is `None`, indicating use of default session.
* **context** (ModelContext): Context engine for storing user conversation information. Default is `None`, indicating context engine functionality is not enabled.
* **envs** (dict[str, Any]): Execution environment configuration, such as model parameters, system variables, etc. Default is `None`.

**Returns**:

**Any**, workflow execution result.

**Example**:

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
>>> # Create workflow
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
>>> # Run workflow directly (no registration needed)
>>> workflow = build_workflow("test_workflow", "test_workflow", "1")
>>> result = asyncio.run(Runner.run_workflow(workflow=workflow, inputs={"query": "query workflow"}))
>>> print(result)
result={'output': {'result': 'query workflow'}} state=<WorkflowExecutionState.COMPLETED: 'COMPLETED'>
>>> # Specify workflow id, execute workflow, need to add workflow to resource manager
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
@classmethod
async def run_agent_group(
    cls,
    agent_group: str | BaseGroup,
    inputs: Any,
    *,
    session: str | Session | None = None,
    context: ModelContext | None = None,
    envs: dict[str, Any] | None = None,
) -> Any
```

Execute AgentGroup and return the result.

**Parameters**:

* **agent_group** (str | BaseGroup): AgentGroup ID or AgentGroup instance. Cannot be `None` or `''`.
* **inputs** (Any): Input data for executing AgentGroup.
* **session** (str | Session): Session ID or session instance. Default is `None`, indicating use of default session.
* **context** (ModelContext): Context engine for storing user conversation information. Default is `None`, indicating context engine functionality is not enabled.
* **envs** (dict[str, Any]): Execution environment configuration, such as model parameters, system variables, etc. Default is `None`.

**Returns**:

**Any**, AgentGroup execution result.

**Example**:

```python
>>> import asyncio
>>> from openjiuwen.core.multi_agent import BaseGroup, GroupCard, GroupConfig
>>> from openjiuwen.core.runner import Runner
>>> from typing import Any, AsyncIterator
>>>
>>> # Create custom AgentGroup
>>> class SimpleGroup(BaseGroup):
...     async def invoke(self, message: Any, session=None) -> Any:
...         # Simple example: directly return input
...         return {"result": f"processed: {message}"}
...
...     async def stream(self, message: Any, session=None) -> AsyncIterator[Any]:
...         yield {"chunk": "streaming result"}
...
>>> # Create GroupCard and GroupConfig
>>> card = GroupCard(
...     name="simple_group",
...     description="Simple example group",
...     topic="demo",
...     version="1.0.0"
... )
>>> config = GroupConfig()
>>>
>>> # Instantiate group
>>> group = SimpleGroup(card=card, config=config)
>>>
>>> # Invoke group instance directly
>>> result = asyncio.run(Runner.run_agent_group(group, inputs={"query": "hello"}))
>>> print(result)
{'result': "processed: {'query': 'hello'}"}
>>>
>>> # Invoke by id (need to register to resource manager first)
>>> Runner.resource_mgr.add_agent_group(card, lambda _: group)
>>> result = asyncio.run(Runner.run_agent_group("simple_group", inputs={"query": "hello"}))
>>> print(result)
{'result': "processed: {'query': 'hello'}"}
```

### run_agent_group_streaming

```python
@classmethod
async def run_agent_group_streaming(
    cls,
    agent_group: str | BaseGroup,
    inputs: Any,
    *,
    session: str | Session | None = None,
    context: ModelContext | None = None,
    stream_modes: list[BaseStreamMode] | None = None,
    envs: dict[str, Any] | None = None,
)
```

Execute AgentGroup in streaming mode and return the result.

**Parameters**:

* **agent_group** (str | BaseGroup): ID or AgentGroup instance. Cannot be `None` or `''`.
* **inputs**: Input data.
* **session** (str | Session): Session ID or session instance. Default is `None`, indicating use of default session.
* **context** (ModelContext): Context engine for storing user conversation information. Default is `None`, indicating context engine functionality is not enabled.
* **stream_modes** (list[BaseStreamMode]): List of streaming output types. Default is `None`, indicating use of default streaming mode.
* **envs** (dict[str, Any]): Execution environment configuration, such as model parameters, system variables, etc. Default is `None`.

**Returns**:

**AsyncGenerator**, streaming result generator.

**Example**:

```python
>>> import asyncio
>>> from openjiuwen.core.multi_agent import BaseGroup, GroupCard, GroupConfig
>>> from openjiuwen.core.runner import Runner
>>> from typing import Any, AsyncIterator
>>>
>>> # Create custom AgentGroup (supports streaming)
>>> class StreamingGroup(BaseGroup):
...     async def invoke(self, message: Any, session=None) -> Any:
...         return {"result": f"processed: {message}"}
...
...     async def stream(self, message: Any, session=None) -> AsyncIterator[Any]:
...         yield {"chunk": 1, "data": "first"}
...         yield {"chunk": 2, "data": "second"}
...
>>> # Create GroupCard and GroupConfig
>>> card = GroupCard(
...     name="streaming_group",
...     description="Streaming example group",
...     topic="demo",
...     version="1.0.0"
... )
>>> config = GroupConfig()
>>>
>>> # Instantiate group
>>> group = StreamingGroup(card=card, config=config)
>>>
>>> # Streaming invocation
>>> async def run_streaming():
...     result = Runner.run_agent_group_streaming(group, inputs={"query": "hello"})
...     async for chunk in result:
...         print(chunk)
...
>>> asyncio.run(run_streaming())
{'chunk': 1, 'data': 'first'}
{'chunk': 2, 'data': 'second'}
>>>
>>> # Streaming invocation by id (need to register first)
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
@classmethod
async def release(cls, session_id: str)
```

Clean up cached data for the specified `session_id`, such as interruption state data.

**Parameters**:

* **session_id** (str): Conversation ID. If `None`, no cleanup is performed.

**Example**:

```python
>>> import asyncio
>>> from openjiuwen.core.runner import Runner
>>> asyncio.run(Runner.release(session_id="session_1"))
```
