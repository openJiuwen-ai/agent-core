# Building ReActAgent

This section demonstrates how to build a `ReActAgent` application for weather querying based on openJiuwen. This application supports guiding large language models to generate plugin call commands through ReAct planning mode, then combining plugin execution results to generate final answers. Through this example, you will learn the following information:

- How to create prompts.
- How to use plugin modules.
- How to create and execute `ReActAgent`.

# Application Design Flow

`ReActAgent` is an Agent that follows the ReAct (Reasoning + Action) planning pattern, completing user tasks through iterative cycles of "Thought → Action → Observe".

1. Thought: `ReActController` calls LLM for task planning, parses whether LLM output contains tool execution instructions.
2. Action: Based on LLM output in thought, operate in two situations:
    - With tool execution instructions: Call tools, select and call appropriate tools (such as retrieval, databases, third-party APIs, code execution, etc.) to execute specific operations. In this use case, it calls a tool for querying weather based on time and location;
    - Without tool execution instructions: Use LLM output as final answer.
3. Observe: `ReActController` appends tool-returned Observation to conversation history, then calls LLM for next task planning.

`ReActAgent` can observe and feedback based on tool execution results, continuously adjust strategies, optimize reasoning paths, until task goal is achieved or final answer is obtained. Its powerful multi-turn reasoning and self-correction capabilities enable ReActAgent to have dynamic decision-making abilities and flexibly respond to environmental changes, suitable for diverse task scenarios requiring complex reasoning and strategy adjustment.

  <div align="center">
    <img src="../images/ReActAgent.png" alt="ReActAgent" width="70%">
  </div>

# Prerequisites

Python version should be higher than or equal to Python 3.11, version 3.11.4 is recommended. Please check Python version information before use.

# Installing openJiuwen

Execute the following command to install openJiuwen:

```bash
pip install -U openjiuwen
```

# Creating Prompt Templates

Create system prompt template for setting overall behavior and role positioning of `ReActAgent`. The following system prompt not only defines persona and task goals, but also provides current date information and gives constraint limitations, helping `ReActAgent` correctly understand current time to complete task goals when interacting with users. Example code is as follows:

```python
def build_current_date():
    """Get current date"""
    from datetime import datetime
    current_datetime = datetime.now()
    return current_datetime.strftime("%Y-%m-%d")

def _create_prompt_template():
    system_prompt = ("You are an AI assistant. Call appropriate tools when needed to help me complete tasks!\n"  # Persona & task goal
                     "Today's date is: {}\n"                                       # Current date
                     "Note: 1. If the user request does not specify a specific time, default to today.")  # Constraint limitations
    return [
        dict(role="system", content=system_prompt.format(build_current_date()))
    ]
```

# Creating Plugin Objects and Their Description Information

This example creates a weather query plugin and defines structure and requirements of its input parameters. First, encapsulate weather query service into a tool class that can be used by the framework through `RestfulApi` interface. Example code is as follows:

> **Note**
> For local test request services (starting with http), SSL verification can be disabled by configuring related environment variables. However, disabling SSL skips certificate verification, may encounter data tampering, man-in-the-middle attacks, leading to sensitive information leakage. Only allowed for temporary use in test environments. Production environments must enable SSL verification to ensure security.

```python
import os
from openjiuwen.core.foundation.tool import RestfulApi, RestfulApiCard

os.environ.setdefault("SSRF_PROTECT_ENABLED", "false")  # Disable IP verification only for local debugging, production environment must enable
os.environ.setdefault("RESTFUL_SSL_VERIFY", "false")  # Disable SSL verification only for local debugging, production environment must enable

def _create_tool():
    weather_card = RestfulApiCard(
        name="WeatherReporter",
        description="Weather query plugin",
        url="your weather search api url", # Weather query service deployment address
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
    return weather_plugin
```

## Configuring MCP Extension Plugins

openJiuwen supports creating plugins integrated with MCP (Model Context Protocol) extension protocol. This example provides configuration for a weather query MCP plugin based on SSE protocol.

```python
from openjiuwen.core.foundation.tool.mcp.base import McpServerConfig

mcp_config = McpServerConfig(
    server_id="query_weather_mcp",
    server_name="query_weather",
    server_path="http://127.0.0.1:8188/sse",
    client_type="sse",
    params={
        "type": "object",
        "title": "query_weatherArguments",
        "properties": {
            "location": {
                "title": "Location",
                "type": "string"
            }
        },
        "required": ["location"]
    }
)
```


# Creating ReActAgent

First use `create_react_agent_config` method provided by openJiuwen to quickly create `ReActAgentConfig` object for weather querying, covering configuration parameter information related to `ReActAgent`, such as prompt definitions and large model configuration information. Example code is as follows:

```python
from openjiuwen.core.single_agent import AgentCard, ReActAgentConfig, ReActAgent

model_config = ReactAgentImpl._create_model()
model_client_config = ReactAgentImpl._create_client_model()

react_agent_config = ReActAgentConfig(
    model_config_obj=model_config,
    model_client_config=model_client_config,
    prompt_template=prompt_template
)

agent_card = AgentCard(
    id="react_agent_123",
    description="AI Assistant",
)

react_agent = ReActAgent(card=agent_card).configure(react_agent_config)
```

Among them, `_create_model` and `_create_client_model` are used to define related configuration information of large models, such as model provider, model name, API invocation and model temperature:

```python
import os
from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig

API_BASE = os.getenv("API_BASE", "your api base")
API_KEY = os.getenv("API_KEY", "your api key")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")

def _create_model():
    return ModelRequestConfig(
        model=MODEL_NAME,
        temperature=0.8,
        top_p=0.9
    )

def _create_client_model():
    return ModelClientConfig(
        client_provider=MODEL_PROVIDER,
        api_key=API_KEY,
        api_base=API_BASE,
        timeout=30,
        verify_ssl=False,
    )
```

Then use constructor of `ReActAgent` class provided by openJiuwen to instantiate object, including weather query assistant configuration. Example code is as follows:

```python
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import AgentCard, ReActAgentConfig, ReActAgent

react_agent = ReActAgent(card=agent_card).configure(react_agent_config)
    tool = ReactAgentImpl._create_tool()
    Runner.resource_mgr.add_tool(tool)
    react_agent.ability_manager.add(tool.card)
```

# Running ReActAgent

After creating `ReActAgent` object, can call `invoke` method to get reply to user query. `ReActAgent`'s `invoke` method implements ReAct planning flow, generates plan through large model, judges whether tools need to be executed. If needed, executes tool calls to complete task, returns final result. Otherwise directly returns model output result, ends flow. Example code is as follows:

```python
import asyncio

result = asyncio.run(react_agent.invoke({"query": "Query the weather in Hangzhou"}))
print(f"ReActAgent final output result: {result.get("output")}")
```

After successful query, will get the following result:

```text
ReActAgent final output result:
Current weather conditions in Hangzhou:
- Weather phenomenon: Light rain
- Real-time temperature: 30.78℃
- Feels like temperature: 37.78℃
- Air humidity: 74%
- Wind speed: 0.77 m/s (approximately 2.8 km/h)

It is recommended to bring rain gear when going out, pay attention to rain and slip prevention. If you need other weather information, feel free to tell me~
```
# Complete Example Code Using MCP Service

```python
import asyncio
import os
from datetime import datetime

from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.single_agent import AgentCard, ReActAgentConfig, ReActAgent
from openjiuwen.core.runner.runner import Runner

API_BASE = os.getenv("API_BASE", "your api base")
API_KEY = os.getenv("API_KEY", "your api key")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")
os.environ.setdefault("SSRF_PROTECT_ENABLED", "false")  # Disable IP verification only for local debugging, production environment must enable
os.environ.setdefault("RESTFUL_SSL_VERIFY", "false")  # Disable SSL verification only for local debugging, production environment must enable

def build_current_date():
    current_datetime = datetime.now()
    return current_datetime.strftime("%Y-%m-%d")

class ReactAgentImpl:
    @staticmethod
    def _create_model():
        return ModelRequestConfig(
            model=MODEL_NAME,
            temperature=0.8,
            top_p=0.9
        )

    @staticmethod
    def _create_client_model():
        return ModelClientConfig(
           client_provider=MODEL_PROVIDER,
           api_key=API_KEY,
           api_base=API_BASE,
           timeout=30,
           verify_ssl=False,
        )

    @staticmethod
    def _create_prompt_template():
        system_prompt = "You are an AI assistant. Call appropriate tools when needed to help me complete tasks! Today's date is: {}\nNote: 1. If the user request does not specify a specific time, it defaults to today."
        return [
            dict(role="system", content=system_prompt.format(build_current_date()))
        ]

async def main():
    model_config = ReactAgentImpl._create_model()
    model_client_config = ReactAgentImpl._create_client_model()
    prompt_template = ReactAgentImpl._create_prompt_template()

    react_agent_config = ReActAgentConfig(
        model_config_obj=model_config,
        model_client_config=model_client_config,
        prompt_template=prompt_template
    )

    agent_card = AgentCard(
        id="react_agent_123",
        description="AI Assistant",
    )

    react_agent = ReActAgent(card=agent_card).configure(react_agent_config)
    mcp_config = McpServerConfig(
        server_id="query_weather_mcp",
        server_name="query_weather",
        server_path="your weather search mcp path",
        client_type="sse",
        params={
            "type": "object",
            "title": "query_weatherArguments",
            "properties": {
                "location": {
                    "title": "Location",
                    "type": "string"
                }
            },
            "required": ["location"]
        }
    )
    await Runner.resource_mgr.add_mcp_server(mcp_config, expiry_time=6000000)

    try:
        # Run ReActAgent
        react_agent.ability_manager.add(mcp_config)
        result = await Runner.run_agent(react_agent, {"query": "What's the weather like in Beijing"})
        print(f"ReActAgent final output result: {result}")
    finally:
        await Runner.resource_mgr.remove_mcp_server(server_id="query_weather_mcp")


asyncio.run(main())
```

Final output result is:

```
ReActAgent final output result:
According to the query results, the weather conditions in Beijing are as follows:
- Location: Beijing
- Temperature: 22℃
- Weather condition: Sunny

Today's weather in Beijing is sunny with a temperature of 22℃, it's a nice day!
```

# Complete Example Code Using Plugins

```python
import asyncio
import os
from datetime import datetime

from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig
from openjiuwen.core.foundation.tool import RestfulApi, RestfulApiCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import AgentCard, ReActAgentConfig, ReActAgent

API_BASE = os.getenv("API_BASE", "your api base")
API_KEY = os.getenv("API_KEY", "your api key")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")
os.environ.setdefault("SSRF_PROTECT_ENABLED", "false")  # Disable SSL verification only for local debugging, production environment must enable
os.environ.setdefault("RESTFUL_SSL_VERIFY", "false")  # Disable SSL verification only for local debugging, production environment must enable

def build_current_date():
    current_datetime = datetime.now()
    return current_datetime.strftime("%Y-%m-%d")

class ReactAgentImpl:
    @staticmethod
    def _create_model():
        return ModelRequestConfig(
            model=MODEL_NAME,
            temperature=0.8,
            top_p=0.9
        )

    @staticmethod
    def _create_client_model():
        return ModelClientConfig(
           client_provider=MODEL_PROVIDER,
           api_key=API_KEY,
           api_base=API_BASE,
           timeout=30,
           verify_ssl=False,
        )

    @staticmethod
    def _create_tool():
        weather_card = RestfulApiCard(
            name="WeatherReporter",
            description="Weather query plugin",
            url="user's path to weather service",
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
        return weather_plugin

    @staticmethod
    def _create_prompt_template():
        system_prompt = "You are an AI assistant. Call appropriate tools when needed to help me complete tasks! Today's date is: {}\nNote: 1. If the user request does not specify a specific time, it defaults to today."
        return [
            dict(role="system", content=system_prompt.format(build_current_date()))
        ]

async def main():
    model_config = ReactAgentImpl._create_model()
    model_client_config = ReactAgentImpl._create_client_model()
    prompt_template = ReactAgentImpl._create_prompt_template()

    react_agent_config = ReActAgentConfig(
        model_config_obj=model_config,
        model_client_config=model_client_config,
        prompt_template=prompt_template
    )

    agent_card = AgentCard(
        id="react_agent_123",
        description="AI Assistant",
    )

    react_agent = ReActAgent(card=agent_card).configure(react_agent_config)
    tool = ReactAgentImpl._create_tool()
    Runner.resource_mgr.add_tool(tool)
    react_agent.ability_manager.add(tool.card)

    result = await react_agent.invoke({"query": "Query the weather in Hangzhou"})
    print(f"ReActAgent final output result: {result}")

if __name__ == "__main__":
    asyncio.run(main())
```

Final output result is:

```
ReActAgent final output result:
Current weather conditions in Hangzhou:
- Weather phenomenon: Light rain
- Real-time temperature: 30.78℃
- Feels like temperature: 37.78℃
- Air humidity: 74%
- Wind speed: 0.77 m/s (approximately 2.8 km/h)

It is recommended to bring rain gear when going out, pay attention to rain and slip prevention. If you need other weather information, feel free to tell me~
```
