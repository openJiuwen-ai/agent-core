Multi-workflow switching is a core capability of the `WorkflowAgent` in the openJiuwen framework. It allows an agent to manage multiple workflows within the same session, supporting intelligent routing between workflows, concurrent interruption and recovery. It addresses the need for users to switch between different task scenarios in the same conversation, providing flexible multi-task management.

The core value of multi-workflow switching includes:

- Intelligent routing: Automatically select the appropriate workflow based on user intent
- Concurrent management: Support multiple workflows to be interrupted simultaneously without interfering with each other
- Seamless switching: Users can freely switch between different workflows, and the system automatically handles state management
- State recovery: Interrupted workflows can be resumed at any time, maintaining contextual continuity


# Multi-Workflow Switching Process

`WorkflowAgent` performs and manages workflows through `WorkflowController`, automatically selecting appropriate workflows based on user queries and supporting switching and recovery between workflows.

The development of `WorkflowAgent` is divided into the following two steps:

- Create `WorkflowAgent`: Create a configuration through `WorkflowAgentConfig` and dynamically add multiple workflow instances.
- Run `WorkflowAgent`: Execute queries through the `invoke` or `stream` methods, supporting intent recognition, switching, and recovery in multi-workflow scenarios.

## Create WorkflowAgent

Users can create a `WorkflowAgent` instance as needed and dynamically bind multiple workflows.

### Create WorkflowAgent Configuration

First, use `WorkflowAgentConfig` to create the agent configuration. This configuration supports multi-workflow scenarios. You can pass an empty list of workflows at creation time and dynamically add them later via the `add_workflows` method. Refer to [Building WorkflowAgent](../Agents/Building%20WorkflowAgent.md).

```python
import os

from openjiuwen.core.application.workflow_agent import WorkflowAgentConfig
from openjiuwen.core.foundation.llm import ModelConfig, BaseModelInfo

API_BASE = os.getenv("API_BASE", "your api base")
API_KEY = os.getenv("API_KEY", "your api key")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")

def create_model_config() -> ModelConfig:
    """Construct model configuration based on environment variables."""
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

# Create minimal configuration (workflows as empty list)
config = WorkflowAgentConfig(
    id="test_multi_workflow_jump_agent",
    version="0.1.0",
    description="Multi-workflow switching and recovery test",
    model=create_model_config(),
)
```

### Dynamically Add Multiple Workflows

After creating the `WorkflowAgent`, you can use the `add_workflows` method to dynamically add multiple workflow instances. This method automatically extracts `schema` information from the workflow instances and updates the configuration.

#### Create Workflow Instances

You first need to create workflow instances. The following example creates two workflows: a weather query workflow and a stock query workflow.

```python
from openjiuwen.core.application.workflow_agent import WorkflowAgent

agent = WorkflowAgent(config)
```

#### Use add_workflows to Add Dynamically

Use the `add_workflows` method to add workflow instances to the `WorkflowAgent`:

```python
from openjiuwen.core.application.workflow_agent import WorkflowAgent
from openjiuwen.core.workflow import (
    End, QuestionerComponent, QuestionerConfig, FieldInfo, Start, Workflow,
    WorkflowCard
)

def _create_start_component():
    return Start()

def _build_questioner_workflow(workflow_id: str,
                               workflow_name: str,
                               workflow_description: str,
                               question_field: str,
                               question_desc: str) -> Workflow:
    """
    Build a simple workflow that includes a questioner.

    Args:
        workflow_id: Workflow ID
        workflow_name: Workflow name
        question_field: Name of the field to ask
        question_desc: Description of the field to ask

    Returns:
        Workflow: A workflow consisting of start -> questioner -> end
    """
    workflow_card = WorkflowCard(
        id=workflow_id,
        name=workflow_name,
        version="1.0",
        description=workflow_description,
        input_params={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "User input"}},
            "required": ['query']
        }
    )
    flow = Workflow(card=workflow_card)

    # Create components
    start = _create_start_component()

    # Create questioner
    key_fields = [
        FieldInfo(field_name=question_field, description=question_desc, required=True),
    ]
    model_config = ModelRequestConfig(
        model=MODEL_NAME,
        temperature=0.8,
        top_p=0.9
    )

    model_client_config = ModelClientConfig(
        client_provider=MODEL_PROVIDER,
        api_key=API_KEY,
        api_base=API_BASE,
        timeout=30,
        verify_ssl=False,
    )

    questioner_config = QuestionerConfig(
        model_client_config=model_client_config,
        model_config=model_config,
        question_content="",
        extract_fields_from_response=True,
        field_names=key_fields,
        with_chat_history=False,
        extra_prompt_for_fields_extraction="",
        example_content="",
    )
    questioner = QuestionerComponent(questioner_config)

    # End component, returns the field values collected by the questioner
    end = End({"responseTemplate": f"{{{{{question_field}}}}}"})

    # Register components
    flow.set_start_comp("start", start, inputs_schema={"query": "${query}"})
    flow.add_workflow_comp("questioner", questioner, inputs_schema={"query": "${start.query}"})
    flow.set_end_comp("end", end, inputs_schema={question_field: f"${{questioner.{question_field}}}"})

    # Connect topology
    flow.add_connection("start", "questioner")
    flow.add_connection("questioner", "end")

    return flow


weather_workflow = _build_questioner_workflow(
    workflow_id="weather_flow",
    workflow_name="Weather Query",
    workflow_description="Query weather conditions, temperature, and meteorological information for a location",
    question_field="location",
    question_desc="Location"
)
stock_workflow = _build_questioner_workflow(
    workflow_id="stock_flow",
    workflow_name="Stock Query",
    workflow_description="Query stock prices, market trends, and stock movements",
    question_field="stock_code",
    question_desc="Stock Code"
)

# Create minimal configuration (workflows as empty list)
config = WorkflowAgentConfig(
    id="test_multi_workflow_jump_agent",
    version="0.1.0",
    description="Multi-workflow switching and recovery test",
    model=create_model_config(),
)
agent = WorkflowAgent(config)

# Use add_workflows to add dynamically (auto extract schema)
agent.add_workflows([weather_workflow, stock_workflow])
```

## Run WorkflowAgent

`WorkflowAgent` supports switching and recovery in multi-workflow scenarios. The following example demonstrates a complete multi-workflow switching and recovery process:

Scenario Description

1. The user initiates a weather query but does not provide the location, causing the workflow to interrupt and wait for input
2. The user switches to a stock query but does not provide the stock code, causing another workflow to be interrupted
3. The user provides the location information, resumes the weather query workflow, and completes it
4. The user provides the stock code, resumes the stock query workflow, and completes it

### Step 1: User query "Query weather"

- Intent recognition: Recognized as the weather query workflow
- Execute workflow: The workflow runs to the questioner component
- Interrupt: The questioner asks for location

```python
import asyncio

async def main():
    conversation_id = "test-jump-recovery-001"
    result1 = await agent.invoke({
        "query": "Query weather",
        "conversation_id": conversation_id
    })
    print(result1)

if __name__ == "__main__":
    asyncio.run(main())
```

Expected Output

```python
[
    OutputSchema(
        type='__interaction__',
        index=0,
        payload=InteractionOutput(
            id="questioner",
            value="Please provide the location information"
        )
    )
]
```

### Step 2: User query "View stocks"

- Intent recognition: Recognized as the stock query workflow (a different workflow)
- Check interruption: No interrupted tasks in the stock workflow
- Execute workflow: Execute the stock query workflow
- Interrupt: The questioner asks for the stock code

```python
import asyncio

async def main():
    conversation_id = "test-jump-recovery-001"
    result2 = await agent.invoke({
        "query": "View stocks",
        "conversation_id": conversation_id
    })
    print(result2)

if __name__ == "__main__":
    asyncio.run(main())
```

Expected Output

```python
[
    OutputSchema(
        type='__interaction__',
        index=0,
        payload=InteractionOutput(
            id="questioner",
            value="Please provide the stock code information"
        )
    )
]
```

### Step 3: User query "Query Beijing weather"

- Intent recognition: Recognized as the weather query workflow
- Check interruption: Finds an interrupted task in the weather workflow
- Resume task: Create InteractiveInput and resume the weather workflow
- Complete: The workflow continues from the questioner and returns the result

```python
import asyncio

async def main():
    conversation_id = "test-jump-recovery-001"
    result3 = await agent.invoke({
        "query": "Query Beijing weather",
        "conversation_id": conversation_id
    })
    print(result3)

if __name__ == "__main__":
    asyncio.run(main())
```

Expected Output (Completed)

```python
{
    "output": WorkflowOutput(
        state=WorkflowExecutionState.COMPLETED,
        result={
            "responseContent": "Beijing",  # Returns the user-provided location
            "output": {}
        }
    ),
    "result_type": "answer"
}
```

### Step 4: User query "View AAPL stock"

- Intent recognition: Recognized as the stock query workflow
- Check interruption: Finds an interrupted task in the stock workflow
- Resume task: Create InteractiveInput and resume the stock workflow
- Complete: The workflow continues from the questioner and returns the result

```python
import asyncio

async def main():
    conversation_id = "test-jump-recovery-001"
    result4 = await agent.invoke({
        "query": "View AAPL stock",
        "conversation_id": conversation_id
    })
    print(result4)

if __name__ == "__main__":
    asyncio.run(main())
```

Expected Output (Completed)

```python
{
    "output": WorkflowOutput(
        state=WorkflowExecutionState.COMPLETED,
        result={
            "responseContent": "AAPL",  # Returns the user-provided stock code
            "output": {}
        }
    ),
    "result_type": "answer"
}
```

# Complete Example Code
```python
import asyncio
import os

from openjiuwen.core.application.workflow_agent import WorkflowAgent
from openjiuwen.core.foundation.llm import ModelConfig, BaseModelInfo, ModelRequestConfig, ModelClientConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.workflow import (
    End, QuestionerComponent, QuestionerConfig, FieldInfo, Start, Workflow,
    WorkflowCard
)
from openjiuwen.core.single_agent.legacy import WorkflowAgentConfig


API_BASE = os.getenv("API_BASE", "your api url")
API_KEY = os.getenv("API_KEY", "your model token")
MODEL_NAME = os.getenv("MODEL_NAME", "your model name")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "your model provider")
os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")

SYSTEM_PROMPT_TEMPLATE = "You are an AI assistant for query rewriting. Today's date is {}."


def create_model_config() -> ModelConfig:
    """Construct model configuration based on environment variables."""
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
    return Start()

def _build_questioner_workflow(workflow_id: str,
                               workflow_name: str,
                               workflow_description: str,
                               question_field: str,
                               question_desc: str) -> Workflow:
    """
    Build a simple workflow that includes a questioner.

    Args:
        workflow_id: Workflow ID
        workflow_name: Workflow name
        question_field: Name of the field to ask
        question_desc: Description of the field to ask

    Returns:
        Workflow: A workflow consisting of start -> questioner -> end
    """
    workflow_card = WorkflowCard(
        id=workflow_id,
        name=workflow_name,
        version="1.0",
        description=workflow_description,
        input_params={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "User input"}},
            "required": ['query']
        }
    )
    flow = Workflow(card=workflow_card)

    # Create components
    start = _create_start_component()

    # Create questioner
    key_fields = [
        FieldInfo(field_name=question_field, description=question_desc, required=True),
    ]
    model_config = ModelRequestConfig(
        model=MODEL_NAME,
        temperature=0.8,
        top_p=0.9
    )

    model_client_config = ModelClientConfig(
        client_provider=MODEL_PROVIDER,
        api_key=API_KEY,
        api_base=API_BASE,
        timeout=30,
        verify_ssl=False,
    )

    questioner_config = QuestionerConfig(
        model_client_config=model_client_config,
        model_config=model_config,
        question_content="",
        extract_fields_from_response=True,
        field_names=key_fields,
        with_chat_history=False,
        extra_prompt_for_fields_extraction="",
        example_content="",
    )
    questioner = QuestionerComponent(questioner_config)

    # End component, returns the field values collected by the questioner
    end = End({"responseTemplate": f"{{{{{question_field}}}}}"})

    # Register components
    flow.set_start_comp("start", start, inputs_schema={"query": "${query}"})
    flow.add_workflow_comp("questioner", questioner, inputs_schema={"query": "${start.query}"})
    flow.set_end_comp("end", end, inputs_schema={question_field: f"${{questioner.{question_field}}}"})

    # Connect topology
    flow.add_connection("start", "questioner")
    flow.add_connection("questioner", "end")

    return flow


async def main():
    """
    Test switching and recovery across multiple workflows.

    Scenario:
    1. query1 -> workflow1 (weather query) -> questioner interrupts (asks for location)
    2. query2 -> intent recognition -> workflow2 (stock query) -> questioner interrupts (asks for stock code)
    3. query3 (InteractiveInput) -> resume workflow1, provide location -> complete
    4. query4 (InteractiveInput) -> resume workflow2, provide stock code -> complete
    """

    # Create two workflows with a questioner
    weather_workflow = _build_questioner_workflow(
        workflow_id="weather_flow",
        workflow_name="Weather Query",
        workflow_description="Query weather conditions, temperature, and meteorological information for a location",
        question_field="location",
        question_desc="Location"
    )
    stock_workflow = _build_questioner_workflow(
        workflow_id="stock_flow",
        workflow_name="Stock Query",
        workflow_description="Query stock prices, market trends, and stock movements",
        question_field="stock_code",
        question_desc="Stock Code"
    )

    # Create minimal configuration (workflows as empty list)
    config = WorkflowAgentConfig(
        id="test_multi_workflow_jump_agent",
        version="0.1.0",
        description="Multi-workflow switching and recovery test",
        model=create_model_config(),
    )
    agent = WorkflowAgent(config)

    # Use add_workflows to add dynamically (auto extract schema)
    agent.add_workflows([weather_workflow, stock_workflow])

    conversation_id = "test-jump-recovery-001"

    # ========== Step 1: query1 -> workflow1 -> interrupt ==========
    print("\n[Step 1] Send query1: Query weather")
    result1 = await Runner.run_agent(agent=agent, inputs={"query": "Query weather", "conversation_id": conversation_id})
    print("Step 1 result: ", result1)

    # ========== Step 2: query2 -> workflow2 -> interrupt ==========
    print("\n[Step 2] Send query2: View stocks")
    result2 = await Runner.run_agent(agent=agent, inputs={"query": "View stocks", "conversation_id": conversation_id})
    print(f"Step 2 result: {result2}")

    # ========== Step 3: query3 -> resume workflow1 ==========
    print("\n[Step 3] Send query3: Provide location to resume workflow1")
    result3 = await Runner.run_agent(agent=agent, inputs={"query": "Query Beijing weather", "conversation_id": conversation_id})
    print(f"Step 3 result: {result3}")

    # ========== Step 4: query4 -> resume workflow2 ==========
    print("\n[Step 4] Send query4: Provide stock code to resume workflow2")
    result4 = await Runner.run_agent(agent=agent, inputs={"query": "View AAPL stock", "conversation_id": conversation_id})
    print(f"Step 4 result: {result4}")


if __name__ == "__main__":
    asyncio.run(main())
```
