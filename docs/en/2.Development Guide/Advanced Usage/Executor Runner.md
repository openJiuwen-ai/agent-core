# Executor Runner

Runner is the unified entry point and control center for executing all core components of openJiuwen (including Workflow and Agent). It abstracts complex execution logic and provides developers with a concise, consistent, and powerful programming interface.

**Important Note**: Runner is a singleton class. All method calls and property accesses are automatically proxied to the global Runner instance. You don't need to instantiate Runner; simply call methods directly through the class name, for example: `Runner.start()`, `Runner.resource_mgr`.

The main functions of Runner include:

- Provide standard asynchronous invocation (invoke) and asynchronous streaming invocation (stream) entry points for Agent.
- Provide standard asynchronous invocation (invoke) and asynchronous streaming invocation (stream) entry points for Workflow.

## Agent Execution

Runner supports single-output and streaming-output execution for all Agents, including built-in Agents such as ReActAgent and WorkflowAgent, as well as user-defined Agents.

Below is an example using a `WorkflowAgent` to illustrate executing an `Agent` via `Runner`.

First, create a WorkflowAgent instance:

```python
from openjiuwen.core.common.constants.enums import ControllerType
from openjiuwen.core.application.workflow_agent import WorkflowAgentConfig, WorkflowAgent
from openjiuwen.core.workflow import End, Start, Workflow, WorkflowCard, generate_workflow_key
from openjiuwen.core.workflow.workflow_config import WorkflowConfig
from openjiuwen.core.runner.runner import Runner


def create_agent(runner):
    # Create workflow flow
    card = WorkflowCard(id="workflow_id", name="Simple Workflow", version="1",
                        description="this_is_a_demo")
    flow = Workflow(workflow_config=WorkflowConfig(card=card))
    flow.set_start_comp("start", Start(), inputs_schema={"query": "${query}"})
    flow.set_end_comp("end", End(), inputs_schema={"result": "${start.query}"})
    flow.add_connection("start", "end")

    # Register flow to resource manager (use generate_workflow_key to generate correct key)
    # Note: When registering, use id_version format key
    register_card = WorkflowCard(
        id=generate_workflow_key(card.id, card.version),
        name=card.name,
        version=card.version,
        description=card.description
    )
    runner.resource_mgr.add_workflow(register_card, lambda: flow)

    # Create Agent, use WorkflowCard to describe workflow input parameters
    workflow_card = WorkflowCard(
        id="workflow_id",
        version="1",
        name="Simple Workflow",
        description="this_is_a_demo",
        input_params={"query": {"type": "string"}},
    )
    workflow_agent_config = WorkflowAgentConfig(id="agent_id", version="1", description="this_is_a_demo",
                                                workflows=[workflow_card],
                                                controller_type=ControllerType.WorkflowController
                                                )
    agent = WorkflowAgent(agent_config=workflow_agent_config)
    return agent


# Runner is a singleton class, use the class name directly
agent = create_agent(Runner)
```

Then, call the `run_agent` interface to directly run the WorkflowAgent:

```python
import asyncio

print(asyncio.run(Runner.run_agent(agent=agent, inputs={"conversation_id": "id1", "query": "haha"})))
```

Execution result:

```python
{'output': WorkflowOutput(result={'output': {'result': 'haha'}},
                          state= < WorkflowExecutionState.COMPLETED: 'COMPLETED' >), 'result_type': 'answer'}
```

## Workflow Execution

Runner supports single-output and streaming-output execution for Workflow.

Below, we construct a simple workflow to introduce the process of executing a `Workflow` via `Runner`.

First, create a Workflow:

```python
from openjiuwen.core.workflow import End, Start, Workflow, WorkflowCard
from openjiuwen.core.workflow.workflow_config import WorkflowConfig


def build_workflow(name, workflow_id, version):
    flow = Workflow(workflow_config=WorkflowConfig(
        card=WorkflowCard(id=workflow_id, name=name, version=version,
                          description="this_is_a_demo")))
    flow.set_start_comp("start", Start(), inputs_schema={"query": "${query}"})
    flow.set_end_comp("end", End(), inputs_schema={"result": "${start.query}"})
    flow.add_connection("start", "end")
    return flow


workflow = build_workflow("test_workflow", "test_workflow", "1")
```

Then, call `Runner`'s `run_workflow` to directly run the `Workflow` (no need to explicitly construct a session, it will automatically create `WorkflowSession` internally):

```python
import asyncio
from openjiuwen.core.runner import Runner

result = asyncio.run(Runner.run_workflow(workflow=workflow, inputs={"query": "query workflow"}))
print(result)
```

Execution result:

```python
result = {'output': {'result': 'query workflow'}}
state = < WorkflowExecutionState.COMPLETED: 'COMPLETED' >
```

## Tool Execution

`Runner` drives Agents and Workflows only. A `Tool` — including the built-in `RestfulApi` and `LocalFunction`, as well as user-defined Tools of any kind — is executed directly through its own `invoke` / `stream` interface.

Below, we construct a `LocalFunction` tool for addition to illustrate how a `Tool` is executed.

First, create a Tool:

```python
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard

# Create a local tool
add_plugin = LocalFunction(
    card=ToolCard(
        id="add",
        name="add",
        description="Addition",
        input_params={
            "type": "object",
            "properties": {
                "a": {"description": "addend", "type": "number"},
                "b": {"description": "augend", "type": "number"},
            },
            "required": ["a", "b"],
        },
    ),
    func=lambda a, b: a + b,
)
```

Then, call `invoke` to execute the tool:

```python
import asyncio

print(asyncio.run(add_plugin.invoke({'a': 1, 'b': 2})))
```

Finally, execution result:

```python
3
```

## Multi-Agent Execution

Multi-agent collaboration is no longer executed through `Runner`. The `openjiuwen.agent_group` package and `Runner.run_agent_group` used by earlier releases have been removed; team-style collaboration is now provided by Agent Teams (`openjiuwen.agent_teams`), where a Leader coordinates Teammates over a configurable transport and storage layer.

For team construction, member roles, and the run / stream entry points, see [AgentTeams](../Agent%20Teams/AgentTeams.md).
