# openjiuwen.core.runner

## class openjiuwen.core.runner

Runner provides a unified execution interface for Workflow, Agent.

Runner is a singleton class. All method calls and property accesses are automatically proxied to the global Runner instance (`GLOBAL_RUNNER`). You don't need to instantiate Runner; simply call methods directly through the class name.


### start

```python
@classmethod
async start(cls) -> bool
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
async stop(cls)
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

**Returns**: [**AsyncCallbackFramework**](./callback/framework.md), the async callback framework instance.

### set_config

```python
@classmethod
set_config(cls, config: RunnerConfig) -> None
```

Set the Runner configuration.

**Parameters**: **config** (RunnerConfig): Configuration object.

### get_config

```python
@classmethod
get_config(cls) -> RunnerConfig
```

Get the current Runner configuration.

**Returns**: **RunnerConfig**, current configuration object.

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
>>> # Create Agent, use WorkflowCard to describe workflow input parameters
>>> workflow_card = WorkflowCard(
...     id="workflow_id",
...     version="1",
...     name="Simple Workflow",
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

### release

```python
@classmethod
async release(cls, session_id: str)
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
