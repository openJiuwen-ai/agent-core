# Connect to LLM

Different models have their own strengths in reasoning ability, conversational fluency, and multi-turn interaction. Users can flexibly choose the most suitable model based on specific application scenarios—for example, selecting a model with stronger reasoning for complex logical tasks, or a model with more natural interaction for a smoother conversational experience.

openJiuwen provides a unified **model client + configuration** system through `openjiuwen.core.foundation.llm`. The recommended integration approach is:

- Use `ModelClientConfig` to describe "how to connect to the model service" (client_provider/api_base/api_key/custom_headers/SSL, etc.);
- Use `ModelRequestConfig` to describe "which model to use for this call + call parameters" (model/temperature/top_p, etc.);
- Use the unified entry class `Model`, and call via `invoke/stream`. The framework will automatically select the corresponding model client implementation based on `client_provider`.

## Using Model to Call Models

### Initialize the Model

Taking SiliconFlow vendor's model integration as an example:

```python
from openjiuwen.core.foundation.llm import (
    Model,
    ModelClientConfig,
    ModelRequestConfig,
)

# 1. Configure client connection information (client_provider/api_base/api_key, etc.)
model_client_config = ModelClientConfig(
    client_provider="SiliconFlow",              # Model provider identifier, framework automatically selects client implementation based on this value
    api_base="https://api.siliconflow.cn/v1",   # Model service URL
    api_key="sk-****************************",  # Authentication Token
    custom_headers={                            # Optional: inject custom headers for OpenAI-compatible requests
        "Token": "tenant-token",
        "UserID": "user-a",
    },
    verify_ssl=False                            # SSL verification disabled in example, recommended to enable in production
)

# 2. Configure the model and parameters for this request
model_request_config = ModelRequestConfig(
    model="Qwen/Qwen3-32B",     # Specific model name, determined by the list of models supported by the server
    temperature=0.7,
    top_p=0.9,
)

# 3. Create unified model entry
model = Model(
    model_client_config=model_client_config,
    model_config=model_request_config,
)
```

> **Note**
> - Users need to register accounts on SiliconFlow or OpenAI vendor websites to obtain available model `api_key` and model invocation URL address `api_base`.
> - `client_provider` currently has built-in support for `OpenAI` and `SiliconFlow`. The framework will automatically select the corresponding model client implementation based on this configuration.

### Configure Custom Headers

When the model service is based on an OpenAI-compatible protocol and requires extra request headers, you can use `custom_headers` to inject custom headers. Examples include multi-tenant identifiers, business-side user identifiers, or gateway extension headers.

#### Config-level Headers

You can configure default headers through `custom_headers` in `ModelClientConfig`. These headers will be automatically forwarded on every request made by the current model object:

```python
from openjiuwen.core.foundation.llm import ModelClientConfig

model_client_config = ModelClientConfig(
    client_provider="OpenAI",
    api_base="https://your-openai-compatible-endpoint/v1",
    api_key="sk-****************************",
    custom_headers={
        "Token": "tenant-token",
        "UserID": "user-a",
    },
)
```

#### Request-level Headers

If a single request needs to temporarily override or supplement headers, you can pass `custom_headers` to `invoke` or `stream`:

```python
response = await model.invoke(
    messages=messages,
    custom_headers={
        "userid": "user-b",   # Overrides the config-level UserID
        "X-Trace-Id": "trace-001",
    },
)
```

#### Header Merge Rules

- Request-level `custom_headers` override config-level headers with the same name.
- Header names are compared in a case-insensitive way. For example, `UserID` and `userid` are treated as the same header.
- The framework automatically filters protected headers: `Authorization`, `Host`, `Content-Length`, `Transfer-Encoding`, and `Connection`.
- Keys or values that are `None`, empty strings, or whitespace-only strings will not be forwarded.

#### Other Entry Points

In addition to constructing `ModelClientConfig` directly, you can also use this capability through other unified entry points:

```python
from openjiuwen.core.foundation.llm import init_model
from openjiuwen.core.single_agent.agents.react_agent import ReActAgentConfig

# Option 1: init_model
model = init_model(
    provider="OpenAI",
    model_name="qwen-plus",
    api_key="sk-****************************",
    api_base="https://your-openai-compatible-endpoint/v1",
    custom_headers={"Token": "tenant-token"},
)

# Option 2: ReActAgentConfig
react_agent_config = ReActAgentConfig()
react_agent_config.configure_custom_headers({
    "Token": "tenant-token",
    "UserID": "user-a",
})
```

### Prepare Model Input

Large models support the following main input parameters. When only `messages` is configured, the model will generate a reply directly based on context; when both `messages` and `tools` are configured, the model will combine context information with tool definitions to determine whether to call tools to fulfill user requests.

- **messages**: A list of messages arranged in conversational order. Each message object contains the sender's role (`role`) and message content (`content`). This is a **required** parameter. Input supports two common formats (`List[BaseMessage]`, `List[Dict]`):

**Method 1: Using BaseMessage type**

```python
from openjiuwen.core.foundation.llm import SystemMessage, UserMessage

messages = [
    SystemMessage(content="You are an AI assistant"),
    UserMessage(content="Hello")
]
```

**Method 2: Using dictionary type**

```python
messages = [
    {"role": "system", "content": "You are an AI assistant"},
    {"role": "user", "content": "Hello"}
]
```

- **tools**: A list of tools available to the large model. Each tool is defined using JSON Schema format, detailing required parameters, parameter types, and whether they are mandatory. This is an **optional** parameter, only used when tool calls need to be executed. Example code:

```python
# Tool definition
tools = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get current weather for a location",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "City and country e.g. Paris, France"
                },
                "units": {
                    "type": "string",
                    "enum": ["metric", "imperial"],
                    "description": "Temperature unit"
                }
            },
            "required": ["location"]
        }
    }
}]
```

### Call the Model

Large model invocation supports two methods: non-streaming `invoke` and streaming `stream`, both are **async methods**:

| Method | Description | Use Cases |
|--------|-------------|-----------|
| `invoke` | Async non-streaming call, returns complete result at once | Scenarios requiring complete result at once, not sensitive to real-time requirements |
| `stream` | Async streaming call, returns response content block by block | Scenarios requiring high-concurrency real-time pushing, async non-blocking output, or displaying while generating |

#### async invoke method

Asynchronously calls the large model, obtaining complete response result at once.

**Parameters**:

- **messages** (Union[str, List[BaseMessage], List[dict]]): Input messages for calling the large model. Required.
- **tools** (Union[List[ToolInfo], List[dict], None], optional): Specify the list of tools that can be called by the model. Default value: None.
- **temperature** (float, optional): Controls randomness of model output. Value range: [0, 1]. Overrides configuration in ModelRequestConfig. Default value: None (uses configured value).
- **top_p** (float, optional): Controls diversity of model output. Value range: [0, 1]. Overrides configuration in ModelRequestConfig. Default value: None (uses configured value).
- **model** (str, optional): Specify model name. Overrides configuration in ModelRequestConfig. Default value: None (uses configured value).
- **max_tokens** (int, optional): Maximum number of tokens to generate. Default value: None.
- **stop** (str, optional): Stop sequence. Default value: None.
- **output_parser** (BaseOutputParser, optional): Output parser for parsing model response content. Default value: None.
- **timeout** (float, optional): Timeout for this request. Overrides configuration in ModelClientConfig. Default value: None (uses configured value).
- **custom_headers** (Mapping[str, Any], optional): Custom headers attached to this request. These headers are merged with `ModelClientConfig.custom_headers` in a case-insensitive manner and override config-level headers with the same name. Default value: None.

**Returns**:

**AssistantMessage**, the message object returned by the large model, containing the following main fields:
- `role`: Role, fixed as `assistant`.
- `content`: Response content text.
- `tool_calls`: Tool call list (if any).
- `usage_metadata`: Usage statistics.
- `finish_reason`: Completion reason, `stop` indicates normal end, `tool_calls` indicates tool call needed.

**Example**:

```python
import asyncio
from openjiuwen.core.foundation.llm import (
    Model,
    ModelClientConfig,
    ModelRequestConfig,
    SystemMessage,
    UserMessage,
)


async def invoke_example():
    # Configure model client
    model_client_config = ModelClientConfig(
        client_provider="SiliconFlow",
        api_base="https://api.siliconflow.cn/v1",
        api_key="sk-****************************",
        verify_ssl=False,
    )
    model_request_config = ModelRequestConfig(
        model="Qwen/Qwen3-32B",
        temperature=0.7,
        top_p=0.95,
    )
    model = Model(
        model_client_config=model_client_config,
        model_config=model_request_config,
    )

    # Prepare model input data
    messages = [
        SystemMessage(content="You are an AI assistant"),
        UserMessage(content="Hangzhou weather")
    ]

    # Weather tool schema definition
    tools = [{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather for a location",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "City and country e.g. Paris, France"
                    },
                    "units": {
                        "type": "string",
                        "enum": ["metric", "imperial"],
                        "description": "Temperature unit"
                    }
                },
                "required": ["location"]
            }
        }
    }]

    # Call the model
    response = await model.invoke(messages=messages, tools=tools)
    print(response)


if __name__ == "__main__":
    asyncio.run(invoke_example())
```

**Output result**:

```
AssistantMessage(role='assistant', content='', tool_calls=[ToolCall(id='019afe192ceee268d980f8acd98ccbc1', type='function', name='get_weather', arguments='{"location": "Hangzhou, China"}')], usage_metadata=UsageMetadata(model_name='Qwen/Qwen3-32B', input_tokens=156, output_tokens=23, total_tokens=179), finish_reason='tool_calls')
```

#### async stream method

Asynchronously streams calls to the large model, returning response content block by block.

**Parameters**:

Parameters are the same as the `invoke` method, including the optional `custom_headers`.

**Returns**:

**AsyncIterator[AssistantMessageChunk]**, async iterator, each iteration returns a response chunk, containing the following main fields:
- `content`: Content increment of current chunk.
- `reasoning_content`: Reasoning content (if model supports).
- `tool_calls`: Tool call increment (if any).
- `usage_metadata`: Usage statistics (usually only included in the last chunk).
- `finish_reason`: Completion reason.

**Example**:

```python
import asyncio
from openjiuwen.core.foundation.llm import (
    Model,
    ModelClientConfig,
    ModelRequestConfig,
    SystemMessage,
    UserMessage,
)


async def stream_example():
    # Configure model client
    model_client_config = ModelClientConfig(
        client_provider="SiliconFlow",
        api_base="https://api.siliconflow.cn/v1",
        api_key="sk-****************************",
        verify_ssl=False,
    )
    model_request_config = ModelRequestConfig(
        model="Qwen/Qwen3-32B",
        temperature=0.7,
        top_p=0.95,
    )
    model = Model(
        model_client_config=model_client_config,
        model_config=model_request_config,
    )

    # Prepare model input data
    messages = [ 
        SystemMessage(content="You are an AI assistant"),
        UserMessage(content="Hello")
    ]

    # Use async for to iterate over async iterator
    async for chunk in model.stream(messages=messages):
        print(chunk.content, end="", flush=True)
    print()  # Newline


if __name__ == "__main__":
    asyncio.run(stream_example())
```

**Output result**:

```
Hello
! What can I
help you with
?
```
