# openjiuwen.dev_tools.tune.chat_agent.chat_agent

## func create_chat_agent_config

```python
create_chat_agent_config(agent_id: str, agent_version: str, description: str, model: LLMCallConfig) -> ChatAgentConfig
```

创建 ChatAgent 配置对象。

**参数**：

* **agent_id**(str)：Agent 唯一标识符。
* **agent_version**(str)：Agent 版本号。
* **description**(str)：Agent 描述信息。
* **model**(LLMCallConfig)：LLM 调用配置，包含 model、model_client、system_prompt、user_prompt 等。定义见 `openjiuwen.core.single_agent.legacy`。

**返回**：

**ChatAgentConfig**，ChatAgent 配置实例。

## func create_chat_agent

```python
create_chat_agent(agent_config: ChatAgentConfig, tools: List[Tool] = None) -> ChatAgent
```

根据配置创建 ChatAgent 实例，并绑定可选工具列表。

**参数**：

* **agent_config**(ChatAgentConfig)：Agent 配置。
* **tools**(List[Tool]，可选)：可绑定的工具列表。默认值：`None`。

**返回**：

**ChatAgent**，Agent 实例。

## class openjiuwen.dev_tools.tune.chat_agent.chat_config.ChatAgentConfig

继承自 AgentConfig，增加 `model` 字段。

* **model**(LLMCallConfig)：LLM 调用配置，必填。

## class openjiuwen.dev_tools.tune.chat_agent.chat_agent.ChatAgent

```python
ChatAgent(agent_config: ChatAgentConfig)
```

`ChatAgent` 是基于 LLM 的最简对话 Agent，内部封装 [LLMCall](../../openjiuwen.core/operator/llm_call/base.md#class-openjiuwencoreoperatorllm_callbasellmcall) 与 ContextEngine。支持 invoke 与 stream 两种调用方式，可与评估器、优化器配合用于提示词调优。

**参数**：

* **agent_config**(ChatAgentConfig)：Agent 配置，须包含有效的 model 配置。

### async invoke

```python
async invoke(inputs: Dict, session: Session = None) -> Dict
```

同步调用 Agent，返回模型输出与 tool_calls。`inputs` 中的 `conversation_id` 会作为会话 ID；未传 `session` 时内部自动创建并管理会话。

**参数**：

* **inputs**(Dict)：输入字典，键需与 user_prompt 模板占位符对应，如 `{"query": "用户问题"}`。可含 `conversation_id` 指定会话。
* **session**(Session，可选)：外部会话对象。默认值：`None`。

**返回**：

**Dict**，包含 `output`（模型文本回复）和 `tool_calls`（工具调用列表）。

**样例**：

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

流式调用 Agent，逐块产出 `{"output": str, "tool_calls": list}`。

**参数**：

* **inputs**(Dict)：输入字典，同 invoke。
* **session**(Session，可选)：外部会话对象。默认值：`None`。

**返回**：

**AsyncIterator[Dict]**，异步迭代器，每项包含 `output` 与 `tool_calls`。

### get_llm_calls

```python
get_llm_calls() -> Dict
```

返回内部 LLMCall 映射，用于与 JointOptimizer、InstructionOptimizer 等优化器绑定。键为 `"llm_call"`，值为 [LLMCall](../../openjiuwen.core/operator/llm_call/base.md#class-openjiuwencoreoperatorllm_callbasellmcall) 实例。

**返回**：

**Dict**，`{"llm_call": LLMCall}`。
