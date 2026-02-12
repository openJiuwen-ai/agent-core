# Building WorkflowAgent

This section demonstrates how to build a complete weather query WorkflowAgent based on the openJiuwen platform, from creating pre-built components to orchestrating workflows. Through this example, you can learn the following information:

- How to create components, specifically including the following openJiuwen pre-built components: start component, end component, large model component, plugin component, intent recognition component, questioner component.
- How to create Workflow flowcharts.
- How to create and execute `WorkflowAgent`.

# Application Design Flow

WorkflowAgent is an Agent focused on multi-step, task-oriented process automation, efficiently executing complex tasks by strictly following user-predefined task processes. Users can pre-set clear task steps, execution conditions and role divisions, decompose tasks into multiple executable subtasks or tools, and gradually advance the entire workflow through topological connections and data transfer between components, ultimately outputting expected results. It focuses on achieving standardized and efficient task execution based on preset processes, suitable for scenarios with clear task structure that can be decomposed into multiple steps.

In this example, a weather query workflow is created to ensure accurate and efficient weather information querying according to preset process:

- Start component defines input parameter specifications of workflow.
- Intent recognition component judges whether user request intent is weather query. If it is weather query intent, routes to weather query branch for processing, otherwise routes to default branch to end flow.
- Large model component is used to rewrite user's original input query, automatically supplement current date information and convert place names to English, facilitating questioner component to extract date and place name information.
- Questioner component is used to extract date and place name information from user input information.
- Plugin component is used to use date and place name extracted by questioner as input parameters, call weather plugin for weather query.
- End component defines output result format of workflow.

![WorkflowAgent](../images/WorkflowAgent.png)

# Prerequisites

Python version should be higher than or equal to Python 3.11, version 3.11.4 is recommended. Please check Python version information before use. For detailed specification constraints, please refer to.

# Installing openJiuwen

Execute the following command to install openJiuwen:

```bash
pip install -U openjiuwen
```

# Creating Workflow Process

Overall process for creating Workflow is as follows: First initialize workflow through `Workflow`, specify `workflow_config` configuration parameters. Then define each component and register components to workflow, and set connections between components, thereby completing entire workflow creation. Weather query Workflow designed in this example is as follows: user input → intent recognition → query rewriting → parameter extraction → call weather API → return result. Example code is as follows:

## Initializing Workflow

Initialize workflow, specify `workflow_config` configuration parameters:

```python
from openjiuwen.core.workflow import Workflow, WorkflowCard
from openjiuwen.core.workflow.workflow_config import WorkflowConfig

# Initialize workflow and context
id = "test_weather_agent"
version = "1.0"
name = "weather"
workflow_config = WorkflowConfig(
    card=WorkflowCard(id=id, name=name, version=version,
                      description="Weather query workflow")
)
flow = Workflow(workflow_config=workflow_config)
```

## Registering Components to Workflow

Create core component instances according to task requirements, configure input/output rules of each component, and register components to workflow. This tutorial mainly uses the following openJiuwen pre-built components: start component, end component, large model component, plugin component, intent recognition component, questioner component.

### Start Component

Create start component object through `Start`. Start component, as the beginning of workflow, defines input parameter specifications of workflow. In this example, start component's input is fixed input parameter `query`, type is string, and parameter value references from workflow input. Example code is as follows:

```python
from openjiuwen.core.workflow import Start

def create_start_component():
	return Start()
```

> **Note**
> Here fixed input parameter `query` of start component is defined. Value of this parameter references from WorkflowAgent's input, i.e., when users call WorkflowAgent execution interfaces invoke, stream, input must include `query` field. For example, when `workflow_agent.invoke({"query": "你好"})`, then input received by start component is `{"query": "你好"}`.

### End Component

Create end component object through `End`. End component, as the termination of workflow, defines output result format of workflow. In this example, output result format is output text. Example code is as follows:

```python
from openjiuwen.core.workflow import End

def create_end_component():
	return End({"responseTemplate": "Final result: {{output}}"})
```

> **Note**
> Here end component configures output text template `responseTemplate`, so final result will be concatenated according to value of output variable `output` field, as end component's `responseContent` field output.

### Intent Recognition Component

Construct intent recognition component through `IntentDetectionComponent` for judging user intent. Intent recognition component provides `add_branch` method for defining routing to corresponding branch links under different intents. This example is used to judge whether user request intent is weather query. If it is weather query intent, routes to `llm` branch for processing, otherwise routes to `end` branch to end flow. Code is as follows:

```python
import os
from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.workflow import (
    IntentDetectionComponent, IntentDetectionCompConfig)

API_BASE = os.getenv("API_BASE", "your api base")
API_KEY = os.getenv("API_KEY", "your api key")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")

def create_model_config() -> ModelRequestConfig:
    """Construct model configuration based on environment variables."""
    return ModelRequestConfig(
        model=MODEL_NAME,
        temperature=0.8,
        top_p=0.9
    )

def create_model_client_config() -> ModelClientConfig:
    """Construct client model configuration based on environment variables."""
    return ModelClientConfig(
        client_provider=MODEL_PROVIDER,
        api_key=API_KEY,
        api_base=API_BASE,
        timeout=30,
        verify_ssl=False,
    )

def create_intent_detection_component() -> IntentDetectionComponent:
    """Create intent recognition component."""
    model_client_config = create_model_client_config()
    model_config = create_model_config()
    user_prompt = "请识别意图"
    config = IntentDetectionCompConfig(
        user_prompt=user_prompt,
        category_name_list=["查询某地天气"],
        model_client_config=model_client_config,
        model_config=model_config,
    )
    intent_component = IntentDetectionComponent(config)
    intent_component.add_branch("${intent.classification_id} == 0", ["llm"], "Default branch")
    intent_component.add_branch("${intent.classification_id} == 1", ["llm"], "Weather query branch")
    return intent_component
```

Note: Prompt template filling of intent recognition component depends on field information in `IntentDetectionCompConfig`, which can better guide large models to recognize user input requests.

> - `{{user_prompt}}`: Users can customize prompt of intent recognition component.
> - `{{category_name_list}}`: Is a **predefined intent category list**, used to limit functional classification range that intent recognition component can recognize. Must contain at least one element "Default intent".
> - `{{model}}`: Model information.

### Large Model Component

Construct large model component object through `LLMComponent`, guide large models to output results in predefined format by setting prompts. This example is used to rewrite user's original input query, automatically supplement current date information, facilitating subsequent questioner component to extract `location` and `date` field information, final output is text type. Example code is as follows:

```python
from datetime import datetime
from openjiuwen.core.workflow import LLMComponent, LLMCompConfig

def build_current_date():
    current_datetime = datetime.now()
    return current_datetime.strftime("%Y-%m-%d")

def create_llm_component() -> LLMComponent:
    """Create LLM component, only for extracting structured fields (location/date)."""
    model_client_config = create_model_client_config()
    model_config = create_model_config()
    current_date = build_current_date()
    user_prompt_prefix = "You are an AI assistant for query rewriting. Today's date is {}."
    user_prompt = "\nOriginal query: {{query}}\n\nHelp me rewrite the original query with the following requirements:\n1. Only convert place names to English, keep other information in Chinese;\n2. Default date is today;\n3. Time format is YYYY-MM-DD."
    config = LLMCompConfig(
        model_client_config=model_client_config,
        model_config=model_config,
        template_content=[{"role": "user", "content": user_prompt_prefix.format(current_date) + user_prompt}],
        response_format={"type": "text"},
        output_config={
            "query": {"type": "string", "description": "Rewritten query", "required": True}
        },
    )
    return LLMComponent(config)
```

### Questioner Component

Construct questioner component object through `QuestionerComponent`, supports extracting specified parameters from user input information; if parameter extraction is incomplete, supports actively asking questions and collecting user feedback information. This example mainly uses questioner's parameter extraction capability, extracts complete `location` and `date` field values at once. Example code is as follows:

```python
from openjiuwen.core.workflow import FieldInfo, QuestionerComponent, QuestionerConfig

def create_questioner_component() -> QuestionerComponent:
    """Create questioner component."""
    key_fields = [
        FieldInfo(field_name="location", description="Location", required=True),
        FieldInfo(field_name="date", description="Time", required=True, default_value="today"),
    ]
    model_client_config = create_model_client_config()
    model_config = create_model_config()
    config = QuestionerConfig(
        model_client_config=model_client_config,
        model_config=model_config,
        question_content="",
        extract_fields_from_response=True,
        field_names=key_fields,
        with_chat_history=False,
    )
    return QuestionerComponent(config)
```

Prompt template filling of questioner component depends on field information in `QuestionerConfig`, which can better guide large models to extract user-defined parameters to be extracted.

- `{{required_name}}` corresponds to `field_names`: Extract parameter names of parameters to be extracted, and concatenate into a text according to fixed template; current concatenation method result is: `"地点、时间2个必要信息"`.
- `{{required_params_list}}` also corresponds to `field_names`: Extract all parameter names and parameter description information of parameters to be extracted, and concatenate into a text according to fixed template; current concatenation method result is: `"location：地点\n date：时间"`.
- `{{extra_info}}` corresponds to `extra_prompt_for_fields_extraction`: Optional configuration, supports user-defined constraints.
- `{{example}}` corresponds to `example_content`: Optional configuration, supports user-defined examples.
- `{{dialogue_history}}` corresponds to dialogue history: Questioner component can obtain dialogue history information from context by default.

### Plugin Component

Construct plugin component node through `ToolComponent`, used to call plugins, associate actual `Tool` plugin object executing plugin logic through `bind_tool` method. This example is used to call weather component for weather query. Example code is as follows:

```python
from openjiuwen.core.workflow import ToolComponent, ToolComponentConfig
from openjiuwen.core.foundation.tool.param import Param
from openjiuwen.core.foundation.tool.service_api.restful_api import RestfulApi

def create_plugin_component() -> ToolComponent:
    """Create plugin component, can call external RESTful API."""
    tool_config = ToolComponentConfig()
    weather_card = RestfulApiCard(
        name="WeatherReporter",
        description="Weather query plugin",
        url="your weather search api url",
        method="GET",
        headers={},
        input_params={
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "Location for weather query, must be in English"
                },
                "date": {
                    "type": "string",
                    "description": "Time for weather query, format: YYYY-MM-DD"
                }
            },
            "required": ["location", "date"]
        },
    )
    weather_plugin = RestfulApi(card=weather_card)
    return ToolComponent(tool_config).bind_tool(weather_plugin)
```

The above section introduced instantiation process of each component. Below uniformly introduces registering components to workflow:

```python
# Instantiate each component
start = create_start_component()
intent = create_intent_detection_component()
llm = create_llm_component()
questioner = create_questioner_component()
plugin = create_plugin_component()
end = create_end_component()

# Register components to workflow
flow.set_start_comp(
    "start",
    start,
    inputs_schema={"query": "${query}"},
)
flow.add_workflow_comp(
    "intent",
    intent,
    inputs_schema={"query": "${start.query}"},
)
flow.add_workflow_comp(
    "llm",
    llm,
    inputs_schema={"query": "${start.query}"},
)
flow.add_workflow_comp(
    "questioner",
    questioner,
    inputs_schema={"query": "${llm.query}"}
)
flow.add_workflow_comp(
    "plugin",
    plugin,
    inputs_schema={
        "location": "${questioner.location}",
        "date": "${questioner.date}",
        "validated": True,
    },
)
flow.set_end_comp("end", end, inputs_schema={"output": "${plugin.data}"})
```

## Connecting Components

Set connection relationships between components through `flow.add_connection` method, complete workflow creation:

```python
# Connect components
flow.add_connection("start", "intent")
flow.add_connection("llm", "questioner")
flow.add_connection("questioner", "plugin")
flow.add_connection("plugin", "end")
```

# Creating WorkflowAgent

Create WorkflowAgentConfig object through initialization method of `WorkflowAgentConfig`, covering description information and other information related to `WorkflowAgent`. Example code is as follows:

```python
from openjiuwen.core.application.workflow_agent import WorkflowAgentConfig

agent_config = WorkflowAgentConfig(
    id="weather_agent",
    version="0.1.0",
    description="Weather query agent",
)
```

Create `WorkflowAgent` object through initialization method of `WorkflowAgent` specifying related configuration parameter information, then bind workflow object through `bind_workflows` method. Example code is as follows:

```python
from openjiuwen.core.application.workflow_agent import WorkflowAgent

workflow_agent = WorkflowAgent(agent_config)
workflow_agent.add_workflows([flow])
```

# Running WorkflowAgent

Can call `invoke` method to get reply to user query. Example code is as follows:

```python
import asyncio

async def weather_workflow(agent: WorkflowAgent):
    result = await workflow_agent.invoke({"conversation_id": "12345","query": "What's the weather like in Shanghai"})
    print(f"{result}")


asyncio.run(test_weather_workflow())
```

After successful query, will get the following result:

```text
{"responseContent":"Final result: {'city': 'Shanghai', 'country': 'CN', 'feels_like': 36.21, 'humidity': 86, 'temperature': 29.21, 'weather': 'Cloudy', 'wind_speed': 5.81}"}
```

# Complete Code

```python
import asyncio
import os
from datetime import datetime

from openjiuwen.core.application.workflow_agent import WorkflowAgentConfig, WorkflowAgent
from openjiuwen.core.workflow import (
    Workflow,
    Start,
    End,
    IntentDetectionComponent, IntentDetectionCompConfig,
    LLMComponent, LLMCompConfig,
    QuestionerComponent, FieldInfo, QuestionerConfig,
    ToolComponent, ToolComponentConfig,
)
from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.tool.service_api.restful_api import RestfulApi, RestfulApiCard
from openjiuwen.core.workflow import WorkflowCard
from openjiuwen.core.workflow.workflow_config import WorkflowConfig

API_BASE = os.getenv("API_BASE", "your api base")
API_KEY = os.getenv("API_KEY", "your api key")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")

def build_current_date():
    current_datetime = datetime.now()
    return current_datetime.strftime("%Y-%m-%d")

def create_model_config() -> ModelRequestConfig:
    """Construct model configuration based on environment variables."""
    return ModelRequestConfig(
        model=MODEL_NAME,
        temperature=0.8,
        top_p=0.9
    )

def create_model_client_config() -> ModelClientConfig:
    """Construct client model configuration based on environment variables."""
    return ModelClientConfig(
        client_provider=MODEL_PROVIDER,
        api_key=API_KEY,
        api_base=API_BASE,
        timeout=30,
        verify_ssl=False,
    )

def create_llm_component() -> LLMComponent:
    """Create LLM component, only for extracting structured fields (location/date)."""
    model_client_config = create_model_client_config()
    model_config = create_model_config()
    current_date = build_current_date()
    user_prompt_prefix = "You are an AI assistant for query rewriting. Today's date is {}."
    user_prompt = "\nOriginal query: {{query}}\n\nHelp me rewrite the original query with the following requirements:\n1. Only convert place names to English, keep other information in Chinese;\n2. Default date is today;\n3. Time format is YYYY-MM-DD."
    config = LLMCompConfig(
        model_client_config=model_client_config,
        model_config=model_config,
        template_content=[{"role": "user", "content": user_prompt_prefix.format(current_date) + user_prompt}],
        response_format={"type": "text"},
        output_config={
            "query": {"type": "string", "description": "Rewritten query", "required": True}
        },
    )
    return LLMComponent(config)

def create_intent_detection_component() -> IntentDetectionComponent:
    """Create intent recognition component."""
    model_client_config = create_model_client_config()
    model_config = create_model_config()
    user_prompt = "请识别意图"
    config = IntentDetectionCompConfig(
        user_prompt=user_prompt,
        category_name_list=["查询某地天气"],
        model_client_config=model_client_config,
        model_config=model_config,
    )
    intent_component = IntentDetectionComponent(config)
    intent_component.add_branch("${intent.classification_id} == 0", ["end"], "Default branch")
    intent_component.add_branch("${intent.classification_id} == 1", ["llm"], "Weather query branch")
    return intent_component

def create_questioner_component() -> QuestionerComponent:
    """Create questioner component."""
    key_fields = [
        FieldInfo(field_name="location", description="Location", required=True),
        FieldInfo(field_name="date", description="Time", required=True, default_value="today"),
    ]
    model_client_config = create_model_client_config()
    model_config = create_model_config()
    config = QuestionerConfig(
        model_client_config=model_client_config,
        model_config=model_config,
        question_content="",
        extract_fields_from_response=True,
        field_names=key_fields,
        with_chat_history=False,
    )
    return QuestionerComponent(config)

def create_start_component():
    """Create start component"""
    return Start({"inputs": [{"id": "query", "type": "String", "required": "true", "sourceType": "ref"}]})

def create_end_component():
    """Create end component"""
    return End({"responseTemplate": "Final result: {{output}}"})

def create_plugin_component() -> ToolComponent:
    """Create plugin component, can call external RESTful API."""
    tool_config = ToolComponentConfig()
    weather_card = RestfulApiCard(
        name="WeatherReporter",
        description="Weather query plugin",
        url="your weather search api url",
        method="GET",
        headers={},
        input_params={
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "Location for weather query, must be in English"
                },
                "date": {
                    "type": "string",
                    "description": "Time for weather query, format: YYYY-MM-DD"
                }
            },
            "required": ["location", "date"]
        },
    )
    weather_plugin = RestfulApi(card=weather_card)
    return ToolComponent(tool_config).bind_tool(weather_plugin)

def bind_agent():
    id = "weather_workflow"
    version = "1.0"
    name = "weather"
    workflow_config = WorkflowConfig(
        card=WorkflowCard(id=id, name=name, version=version,
                          description="Weather query workflow")
    )
    flow = Workflow(workflow_config=workflow_config)

    start = create_start_component()  # Start component
    intent = create_intent_detection_component()  # Intent recognition component

    llm = create_llm_component()  # LLM rewriting component
    questioner = create_questioner_component()  # Parameter collection component
    end = create_end_component()  # End component
    plugin = create_plugin_component()  # Plugin component

    # Register components to workflow
    flow.set_start_comp(
        "start",
        start,
        inputs_schema={"query": "${query}"},
    )
    flow.add_workflow_comp(
        "intent",
        intent,
        inputs_schema={"query": "${start.query}"},
    )

    flow.add_workflow_comp(
        "llm",
        llm,
        inputs_schema={"query": "${start.query}"},
    )
    flow.add_workflow_comp(
        "questioner",
        questioner,
        inputs_schema={"query": "${llm.query}"}
    )
    flow.add_workflow_comp(
        "plugin",
        plugin,
        inputs_schema={
            "location": "${questioner.location}",
            "date": "${questioner.date}",
            "validated": True,
        },
    )
    flow.set_end_comp("end", end, inputs_schema={"output": "${plugin.data}"})

    flow.add_connection("start", "intent")
    flow.add_connection("llm", "questioner")
    flow.add_connection("questioner", "plugin")
    flow.add_connection("plugin", "end")

    agent_config = WorkflowAgentConfig(
        id="weather_agent",
        version="0.1.0",
        description="Weather query agent",
    )

    workflow_agent = WorkflowAgent(agent_config)
    workflow_agent.add_workflows([flow])
    return workflow_agent

async def weather_workflow(agent: WorkflowAgent):
    result = await agent.invoke({"conversation_id": "12345", "query": "What's the weather like in Shanghai today"})
    print(result)

if __name__ == "__main__":
    os.environ.setdefault("RESTFUL_SSL_VERIFY", "false")  # Disable SSL verification only for local debugging, production environment must enable
    os.environ.setdefault("SSRF_PROTECT_ENABLED", "false")  # IP address validity verification only for local debugging, production environment must enable
    workflow_agent = bind_agent()
    asyncio.run(weather_workflow(workflow_agent))
```
