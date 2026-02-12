# openjiuwen.core.context_engine.processor.compressor.dialogue_compressor

## class openjiuwen.core.context_engine.processor.compressor.dialogue_compressor.DialogueCompressorConfig

Configuration class for `DialogueCompressor`. When the number of messages or tokens exceeds the threshold, compresses a complete dialogue round of "user question → model tool call → tool return → model final reply" into a single summary message.

* **messages_threshold** (int | None, optional): Triggers compression when the number of messages in memory exceeds this value. Default value: `None`.
* **tokens_threshold** (int, optional): Triggers compression when the cumulative token count exceeds this value. Default value: `10000`.
* **messages_to_keep** (int | None, optional): Guaranteed minimum number of recent messages to keep. Default value: `None`.
* **keep_last_round** (bool, optional): Whether to always keep the most recent complete dialogue round uncompressed. Default value: `True`.
* **customized_compression_prompt** (str | None, optional): Custom compression prompt; uses built-in prompt when `None`. Default value: `None`.
* **compression_token_limit** (int, optional): Maximum token count allowed for the summary after compression. Default value: `2000`.
* **model** (ModelRequestConfig | None, optional): Model request configuration for performing compression. Default value: `None`.
* **model_client** (ModelClientConfig | None, optional): Model service configuration for performing compression. Default value: `None`.

**Constraints**: If both `messages_to_keep` and `messages_threshold` are set, `messages_to_keep` must be less than `messages_threshold`. `model` and `model_client` must be configured when performing compression.

## class openjiuwen.core.context_engine.processor.compressor.dialogue_compressor.DialogueCompressor

```python
DialogueCompressor(config: DialogueCompressorConfig)
```

`DialogueCompressor` inherits from [ContextProcessor](base.md#class-openjiuwencorecontext_engineprocessorbasecontextprocessor), identifying complete tool-call dialogue rounds during `add_messages` and compressing them into a summary via LLM, replacing the original multiple messages. Interface is consistent with the base class, see [ContextProcessor](base.md#class-openjiuwencorecontext_engineprocessorbasecontextprocessor).

**Parameters**:

* **config** (DialogueCompressorConfig): Processor configuration, see above.

**Example**:

```python
>>> import os
>>> import asyncio
>>> from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
>>> from openjiuwen.core.context_engine.processor.compressor.dialogue_compressor import (
...     DialogueCompressor,
...     DialogueCompressorConfig,
... )
>>> from openjiuwen.core.context_engine.schema.messages import OffloadMixin
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
...     compressor_config = DialogueCompressorConfig(
...         messages_threshold=2,
...         tokens_threshold=100000,
...         keep_last_round=False,
...         model=model_config,
...         model_client=model_client_config,
...     )
...     engine_config = ContextEngineConfig(default_window_message_num=100)
...     engine = ContextEngine(engine_config)
...     ctx = await engine.create_context(
...         "demo_ctx",
...         None,
...         history_messages=[],
...         processors=[("DialogueCompressor", compressor_config)],
...     )
...     await ctx.add_messages([
...         UserMessage(content="Call tool to query data"),
...         AssistantMessage(content="", tool_calls=[{"id": "1", "name": "query", "type": "function", "arguments": "{}"}]),
...         ToolMessage(content="Query result: revenue increased 15%", tool_call_id="1"),
...         AssistantMessage(content="According to the tool return, Q1 2024 revenue increased 15% year-over-year."),
...     ])
...     msgs = ctx.get_messages()
...     assert len(msgs) == 2
...     assert isinstance(msgs[1], OffloadMixin)
...     assert "[[OFFLOAD:" in msgs[1].content
...     return len(msgs)
>>>
>>> asyncio.run(main())
2
```
