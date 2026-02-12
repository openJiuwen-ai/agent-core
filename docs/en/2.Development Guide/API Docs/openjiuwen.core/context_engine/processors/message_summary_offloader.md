# openjiuwen.core.context_engine.processor.offloader.message_summary_offloader

## class openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.MessageSummaryOffloaderConfig

Configuration class for `MessageSummaryOffloader`. When the number of messages or tokens exceeds the threshold, generates a summary via LLM for eligible large messages before offloading, preserving more semantics compared to [MessageOffloader](./message_offloader.md).

* **messages_threshold** (int | None, optional): Triggers offload when the number of messages in memory exceeds this value. Default value: `None`.
* **tokens_threshold** (int, optional): Triggers offload when the cumulative token count exceeds this value. Default value: `20000`.
* **large_message_threshold** (int, optional): Messages with token count exceeding this value are considered "large messages" and can be offloaded. Default value: `1000`.
* **offload_message_type** (list[Literal["user", "assistant", "tool"]], optional): List of message roles that can be offloaded. Default value: `["tool"]`.
* **messages_to_keep** (int | None, optional): Guaranteed minimum number of recent messages to keep. Default value: `None`.
* **keep_last_round** (bool, optional): Whether to always keep the most recent user-assistant dialogue round. Default value: `True`.
* **model** (ModelRequestConfig | None, optional): Model request configuration for performing summarization. Default value: `None`.
* **model_client** (ModelClientConfig | None, optional): Model service configuration for performing summarization. Default value: `None`.
* **customized_summary_prompt** (str | None, optional): Custom summary prompt; uses built-in prompt when `None`. Default value: `None`.

**Constraints**: If both `messages_to_keep` and `messages_threshold` are set, `messages_to_keep` must be less than `messages_threshold`. `model` and `model_client` must be configured when performing summarization.

## class openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.MessageSummaryOffloader

```python
MessageSummaryOffloader(config: MessageSummaryOffloaderConfig)
```

`MessageSummaryOffloader` inherits from [MessageOffloader](./message_offloader.md#class-openjiuwencorecontext_engineprocessoroffloadermessage_offloadermessageoffloader), generating a 2–4 sentence summary via LLM for large messages before offloading, then replacing the original text with summary + placeholder. Interface is consistent with the base class, see [MessageOffloader](./message_offloader.md#class-openjiuwencorecontext_engineprocessoroffloadermessage_offloadermessageoffloader).

**Parameters**:

* **config** (MessageSummaryOffloaderConfig): Processor configuration, see above.

**Example**:

```python
>>> import os
>>> import asyncio
>>> from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
>>> from openjiuwen.core.context_engine.processor.offloader.message_summary_offloader import (
...     MessageSummaryOffloader,
...     MessageSummaryOffloaderConfig,
... )
>>> from openjiuwen.core.foundation.llm import (
...     UserMessage,
...     AssistantMessage,
...     ToolMessage,
...     ModelRequestConfig,
...     ModelClientConfig,
... )
>>>
>>> API_BASE = os.getenv("API_BASE", "your api base")
>>> API_KEY = os.getenv("API_KEY", "your api key")
>>> MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4o-mini")
>>> MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "OpenAI")
>>>
>>> async def main():
...     model_config = ModelRequestConfig(model=MODEL_NAME)
...     model_client_config = ModelClientConfig(
...         client_provider=MODEL_PROVIDER,
...         api_base=API_BASE,
...         api_key=API_KEY,
...     )
...     offloader_config = MessageSummaryOffloaderConfig(
...         messages_threshold=3,
...         large_message_threshold=50,
...         offload_message_type=["tool"],
...         model=model_config,
...         model_client=model_client_config,
...     )
...     engine_config = ContextEngineConfig(default_window_message_num=100)
...     engine = ContextEngine(engine_config)
...     ctx = await engine.create_context(
...         "demo_ctx",
...         None,
...         history_messages=[],
...         processors=[("MessageSummaryOffloader", offloader_config)],
...     )
...     await ctx.add_messages([
...         UserMessage(content="Call tool to query data"),
...         AssistantMessage(content="", tool_calls=[{"id": "1", "name": "query", "type": "function", "arguments": "{}"}]),
...         ToolMessage(
...             content="Query result: Q1 2024 revenue increased 15% year-over-year, net profit was 120 million yuan, mainly from cloud services. "
...             + "Detailed data includes: cloud service revenue accounted for 60%, enterprise customer renewal rate increased to 92%.",
...             tool_call_id="1",
...         ),
...         UserMessage(content="Continue"),
...     ])
...     msgs = ctx.get_messages()
...     tool_msg = next(m for m in msgs if m.role == "tool")
...     assert "[[OFFLOAD:" in tool_msg.content
...     return len(msgs)
>>>
>>> asyncio.run(main())
3
```
