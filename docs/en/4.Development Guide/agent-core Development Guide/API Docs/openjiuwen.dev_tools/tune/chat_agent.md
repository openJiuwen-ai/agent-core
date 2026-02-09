# openjiuwen.dev_tools.tune.chat_agent.chat_agent

## func create_chat_agent_config

```python
create_chat_agent_config(agent_id: str, agent_version: str, description: str, model: LLMCallConfig) -> ChatAgentConfig
```

Creates ChatAgent configuration object.

**Parameters**:

* **agent_id** (str): Agent unique identifier.
* **agent_version** (str): Agent version number.
* **description** (str): Agent description information.
* **model** (LLMCallConfig): LLM invocation configuration, containing model, model_client, system_prompt, user_prompt, etc. Definition see `openjiuwen.core.single_agent.legacy`.

**Returns**:

**ChatAgentConfig**, ChatAgent configuration instance.

## func create_chat_agent

```python
create_chat_agent(agent_config: ChatAgentConfig, tools: List[Tool] = None) -> ChatAgent
```

Creates ChatAgent instance according to configuration, and binds optional tool list.

**Parameters**:

* **agent_config** (ChatAgentConfig): Agent configuration.
* **tools** (List[Tool], optional): List of tools that can be bound. Default value: `None`.

**Returns**:

**ChatAgent**, Agent instance.

## class openjiuwen.dev_tools.tune.chat_agent.chat_config.ChatAgentConfig

Inherits from AgentConfig, adds `model` field.

* **model** (LLMCallConfig): LLM invocation configuration, required.

## class openjiuwen.dev_tools.tune.chat_agent.chat_agent.ChatAgent

```python
ChatAgent(agent_config: ChatAgentConfig)
```

`ChatAgent` is the simplest dialogue Agent based on LLM, internally encapsulates [LLMCall](../../openjiuwen.core/operator/llm_call/base.md#class-openjiuwencoreoperatorllm_callbasellmcall) and ContextEngine. Supports invoke and stream two invocation methods, can work with evaluator and optimizer for prompt tuning.

**Parameters**:

* **agent_config** (ChatAgentConfig): Agent configuration, must contain valid model configuration.

### async invoke

```python
async invoke(inputs: Dict, session: Session = None) -> Dict
```

Synchronously invokes Agent, returns model output and tool_calls. `conversation_id` in `inputs` will be used as session ID; when `session` is not passed, internally automatically creates and manages session.

**Parameters**:

* **inputs** (Dict): Input dictionary, keys need to correspond to user_prompt template placeholders, e.g., `{"query": "用户问题"}`. Can contain `conversation_id` to specify session.
* **session** (Session, optional): External session object. Default value: `None`.

**Returns**:

**Dict**, containing `output` (model text reply) and `tool_calls` (tool call list).

**Example**:

```python
>>> import os
>>> import asyncio
>>> from openjiuwen.dev_tools.tune.chat_agent import create_chat_agent_config, create_chat_agent
>>> from openjiuwen.core.single_agent.legacy import LLMCallConfig
>>> from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig
>>>
>>> API_BASE = os.getenv("API_BASE", "your api base")
>>> API_KEY = os.getenv("API_KEY", "your api key")
>>> MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4o-mini")
>>> MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "OpenAI")
>>>
>>> model_config = ModelRequestConfig(model=MODEL_NAME)
>>> model_client_config = ModelClientConfig(
...     client_provider=MODEL_PROVIDER,
...     api_base=API_BASE,
...     api_key=API_KEY,
... )
>>> llm_config = LLMCallConfig(
...     model=model_config,
...     model_client=model_client_config,
...     system_prompt=[{"role": "system", "content": "你是一个助手。"}],
...     user_prompt=[{"role": "user", "content": "{{query}}"}],
... )
>>> config = create_chat_agent_config(
...     agent_id="chat_agent",
...     agent_version="1.0.0",
...     description="",
...     model=llm_config,
... )
>>> agent = create_chat_agent(config)
>>>
>>> async def main():
...     result = await agent.invoke({"query": "你好"})
...     return result["output"]
>>>
>>> asyncio.run(main())
'你好！有什么可以帮助你的？'
```

### async stream

```python
async stream(inputs: Dict, session: Session = None) -> AsyncIterator[Any]
```

Streamingly invokes Agent, produces `{"output": str, "tool_calls": list}` block by block.

**Parameters**:

* **inputs** (Dict): Input dictionary, same as invoke.
* **session** (Session, optional): External session object. Default value: `None`.

**Returns**:

**AsyncIterator[Dict]**, async iterator, each item contains `output` and `tool_calls`.

### get_llm_calls

```python
get_llm_calls() -> Dict
```

Returns internal LLMCall mapping, used to bind with JointOptimizer, InstructionOptimizer and other optimizers. Key is `"llm_call"`, value is [LLMCall](../../openjiuwen.core/operator/llm_call/base.md#class-openjiuwencoreoperatorllm_callbasellmcall) instance.

**Returns**:

**Dict**, `{"llm_call": LLMCall}`.
