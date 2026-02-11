# Executor Runner

Runner is the unified entry point and control center for executing all core components of openJiuwen (including Workflow, Agent, AgentGroup, and Tool). It abstracts complex execution logic and provides developers with a concise, consistent, and powerful programming interface.

**Important Note**: Runner is a singleton class. All method calls and property accesses are automatically proxied to the global Runner instance. You don't need to instantiate Runner; simply call methods directly through the class name, for example: `Runner.start()`, `Runner.resource_mgr`.

The main functions of Runner include:

- Provide standard asynchronous invocation (invoke) and asynchronous streaming invocation (stream) entry points for Agent.
- Provide standard asynchronous invocation (invoke) and asynchronous streaming invocation (stream) entry points for Workflow.
- Provide standard asynchronous invocation (invoke) and asynchronous streaming invocation (stream) entry points for Tool.
- Provide standard asynchronous invocation (invoke) and asynchronous streaming invocation (stream) entry points for AgentGroup.

## Agent Execution

Runner supports single-output and streaming-output execution for all Agents, including built-in Agents such as ReActAgent and WorkflowAgent, as well as user-defined Agents.

Below is an example using a `WorkflowAgent` to illustrate executing an `Agent` via `Runner`.

First, create a WorkflowAgent instance:

```python
from openjiuwen.core.common.constants.enums import ControllerType
from openjiuwen.core.single_agent.legacy import WorkflowSchema
from openjiuwen.core.application.workflow_agent import WorkflowAgentConfig, WorkflowAgent
from openjiuwen.core.workflow import End, Start, Workflow, WorkflowCard, generate_workflow_key
from openjiuwen.core.workflow.workflow_config import WorkflowConfig
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.common import BaseCard


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

    # Create Agent
    workflow_agent_config = WorkflowAgentConfig(id="agent_id", version="1", description="this_is_a_demo",
                                                workflows=[WorkflowSchema(
                                                    id="workflow_id",
                                                    version="1",
                                                    name="Simple Workflow",
                                                    description="this_is_a_demo",
                                                    inputs={"query": {"type": "string"}},
                                                )],
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

Runner supports single-output and streaming-output execution for all Tools, including built-in `RestfulApi` and `LocalFunction`, as well as user-defined Tools of any kind.

Below, we construct a `LocalFunction` tool for addition to illustrate the process of executing a `Tool` via `Runner`.

First, create a Tool:

```python
from openjiuwen.core.utils.tool.function.function import LocalFunction, Param

# Create a local tool
add_plugin = LocalFunction(
    name="add",
    description="Addition",
    params=[
        Param(name="a", description="addend", type="number", required=True),
        Param(name="b", description="augend", type="number", required=True),
    ],
    func=lambda a, b: a + b
)
```

Then, use `Runner.run_tool` to execute the tool:

```python
import asyncio
from openjiuwen.core.runner.runner import Runner

print(asyncio.run(Runner.run_tool(tool=add_plugin, inputs={'a':1, 'b':2})))
```

Finally, execution result:

```python
3
```

## AgentGroup Execution

AgentGroup is the core component for managing collaboration among multiple agents. Runner supports single-output and streaming-output execution for AgentGroup.

Below, we use a `HierarchicalGroup` for banking services as an example to show how to execute an `AgentGroup` via `Runner`.

First, create three `WorkflowAgent`s for transfer service, balance inquiry, and wealth management, then create a `HierarchicalGroup`, and bind the three `WorkflowAgent`s to it.

```python
import os

from openjiuwen.agent.config.workflow_config import WorkflowAgentConfig
from openjiuwen.agent.workflow_agent.workflow_agent import WorkflowAgent
from openjiuwen.core.component.end_comp import End
from openjiuwen.core.component.start_comp import Start
from openjiuwen.core.workflow.base import Workflow
from openjiuwen.core.workflow.workflow_config import WorkflowConfig, WorkflowMetadata
from openjiuwen.core.component.common.configs.model_config import ModelConfig
from openjiuwen.core.utils.llm.base import BaseModelInfo
from openjiuwen.core.component.questioner_comp import QuestionerComponent, QuestionerConfig, FieldInfo
from openjiuwen.agent.config.base import AgentConfig
from openjiuwen.agent_group.hierarchical_group.agents.main_controller import HierarchicalMainController
from openjiuwen.core.agent.agent import ControllerAgent
from openjiuwen.agent_group.hierarchical_group import (
    HierarchicalGroup,
    HierarchicalGroupConfig
)

API_BASE = os.getenv("API_BASE", "your api base")
API_KEY = os.getenv("API_KEY", "your api key")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")


def _create_model_config() -> ModelConfig:
    """Create model configuration"""
    return ModelConfig(
        model_provider=MODEL_PROVIDER,
        model_info=BaseModelInfo(
            model=MODEL_NAME,
            api_base=API_BASE,
            api_key=API_KEY,
            temperature=0.7,
            top_p=0.9,
            timeout=120,
        ),
    )

def _create_start_component():
    """Create Start component"""
    return Start({
        "inputs": [
            {
                "id": "query",
                "type": "String",
                "required": "true",
                "sourceType": "ref"
            }
        ]
    })


def _build_financial_workflow(
        workflow_id: str,
        workflow_name: str,
        workflow_desc: str,
        field_name: str,
        field_desc: str
) -> Workflow:
    """
    Build a financial business workflow (with an interruption node)

    Args:
        workflow_id: Workflow ID
        workflow_name: Workflow name
        workflow_desc: Workflow description
        field_name: Question field name
        field_desc: Question field description

    Returns:
        Workflow: A workflow including start -> questioner -> end
    """
    workflow_config = WorkflowConfig(
        metadata=WorkflowMetadata(
            name=workflow_name,
            id=workflow_id,
            version="1.0",
            description=workflow_desc,
        )
    )
    flow = Workflow(workflow_config=workflow_config)

    # Create components
    start = _create_start_component()

    # Create questioner (interruption node)
    key_fields = [
        FieldInfo(
            field_name=field_name,
            description=field_desc,
            required=True
        ),
    ]
    model_config = _create_model_config()
    questioner_config = QuestionerConfig(
        model=model_config,
        question_content="",
        extract_fields_from_response=True,
        field_names=key_fields,
        with_chat_history=False,
    )
    questioner = QuestionerComponent(questioner_config)

    # End component
    end = End({"responseTemplate": f"{workflow_name} completed: {{{{{field_name}}}}}"})

    # Register components
    flow.set_start_comp("start", start, inputs_schema={"query": "${query}"})
    flow.add_workflow_comp(
        "questioner", questioner, inputs_schema={"query": "${start.query}"}
    )
    flow.set_end_comp(
        "end", end, inputs_schema={field_name: f"${{questioner.{field_name}}}"}
    )

    # Connection topology: start -> questioner -> end
    flow.add_connection("start", "questioner")
    flow.add_connection("questioner", "end")

    return flow

def _create_workflow_agent(
        agent_id: str,
        description: str,
        workflow: Workflow
) -> WorkflowAgent:
    """Create WorkflowAgent"""
    config = WorkflowAgentConfig(
        id=agent_id,
        version="1.0",
        description=description,
        workflows=[],
        model=_create_model_config(),
    )
    agent = WorkflowAgent(config)
    agent.add_workflows([workflow])
    return agent

# Create financial business workflows
transfer_workflow = _build_financial_workflow(
    workflow_id="transfer_flow",
    workflow_name="Transfer Service",
    workflow_desc="Handle user transfer requests, support transfers to specified accounts",
    field_name="amount",
    field_desc="Transfer amount (number)"
)

balance_workflow = _build_financial_workflow(
    workflow_id="balance_flow",
    workflow_name="Balance Inquiry",
    workflow_desc="Query user account balance information",
    field_name="account",
    field_desc="Account number"
)

invest_workflow = _build_financial_workflow(
    workflow_id="invest_flow",
    workflow_name="Wealth Management Service",
    workflow_desc="Provide recommendations and purchases of financial products",
    field_name="product",
    field_desc="Financial product name"
)

# Create WorkflowAgent
transfer_agent = _create_workflow_agent(
    agent_id="transfer_agent",
    description="Transfer service, handles user transfer requests",
    workflow=transfer_workflow
)

balance_agent = _create_workflow_agent(
    agent_id="balance_agent",
    description="Balance inquiry service, queries user account balance",
    workflow=balance_workflow
)

invest_agent = _create_workflow_agent(
    agent_id="invest_agent",
    description="Wealth management service, provides product recommendations and purchases",
    workflow=invest_workflow
)

config = HierarchicalGroupConfig(
    group_id="financial_group",
    leader_agent_id="main_controller"
)
hierarchical_group = HierarchicalGroup(config)


main_config = AgentConfig(
    id="main_controller",
    description="Financial services main controller, identifies user intent and dispatches tasks"
)
main_controller = HierarchicalMainController()
main_agent = ControllerAgent(main_config, controller=main_controller)


# Add all agents to the group
hierarchical_group.add_agent("main_controller", main_agent)
hierarchical_group.add_agent("transfer_agent", transfer_agent)
hierarchical_group.add_agent("balance_agent", balance_agent)
hierarchical_group.add_agent("invest_agent", invest_agent)
```

Then, start Runner and execute the AgentGroup.

```python
import asyncio
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.agent.message.message import Message

asyncio.run(Runner.start())

conversation_id = "session_001"
hierarchical_group.group_controller.subscribe("test_query1", ["transfer_agent"])
message1 = Message.create_user_message(
    content="Transfer money to Zhang San",
    conversation_id=conversation_id
)
# Broadcast the message to all agents subscribed to the test_query1 topic
message1.message_type = "test_query1"
result1 = asyncio.run(Runner.run_agent_group(hierarchical_group, message1))
print(result1)

asyncio.run(Runner.stop())
```

Finally, obtain the output:

```python
[OutputSchema(type='__interaction__', index=0, payload=InteractionOutput(id='questioner', value='Please provide information related to the transfer amount (number)'))]
```
