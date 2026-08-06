# openjiuwen.core.context_engine.processor.forked.compressor.dialogue_compressor

## class openjiuwen.core.context_engine.processor.forked.compressor.dialogue_compressor.DialogueCompressorConfig

Configuration class for `DialogueCompressor`. When the context token count reaches a given ratio of the context capacity, all history messages before the current dialogue round (the last real user message) are compressed by an LLM into a single `<memory_block_dialogue>` summary message, while the current round is kept intact.

* **trigger_context_ratio** (float, optional): Triggers compression when the context token count reaches this ratio of the context capacity. Range (0, 1). Default value: `0.8`.
* **min_target_context_ratio** (float, optional): Skips compression when the compressible messages' token count is below this ratio of the context capacity, avoiding compressions with too little benefit. Range [0, 1). Default value: `0.1`.
* **model** (ModelRequestConfig | None, optional): Model request configuration used to perform compression. Default value: `None`.
* **model_client** (ModelClientConfig | None, optional): Model service configuration used to perform compression. Default value: `None`.
* **enable_compression_dump** (bool, optional): Whether to persist each real compression invocation (the request plus the post-compression context) to disk for offline analysis; no overhead when disabled. Default value: `False`.
* **compression_dump_dir** (str | None, optional): Directory for compression dump files; uses the default directory when `None`. Default value: `None`.

**Constraints**: `model` and `model_client` must both be configured, otherwise the processor never triggers. The kept-message boundary is automatically extended backwards so that a tool call and its tool result are never split apart.

**Migration note**: The legacy `DialogueCompressor` (`processor.compressor.dialogue_compressor`) compressed complete tool-call dialogue rounds one by one, configured via `messages_threshold`, `tokens_threshold`, `messages_to_keep`, `keep_last_round`, etc. The current implementation instead compresses all history before the current round into a single summary, and its trigger changed from message/token count thresholds to a context-capacity ratio; the configuration fields are not compatible. Reconfigure with the fields above when migrating from the legacy version.

## class openjiuwen.core.context_engine.processor.forked.compressor.dialogue_compressor.DialogueCompressor

```python
DialogueCompressor(config: DialogueCompressorConfig)
```

`DialogueCompressor` inherits from [ContextProcessor](base.md#class-openjiuwencorecontext_engineprocessorbasecontextprocessor). When the context window is being materialized, it checks whether the context token count reaches `trigger_context_ratio`; if so, it compresses the history messages before the current round into a single summary message that replaces the originals, embedding a meaning note and a conflict policy (newer raw messages and the latest explicit user intent override the summary). Interface is consistent with the base class, see [ContextProcessor](base.md#class-openjiuwencorecontext_engineprocessorbasecontextprocessor).

**Parameters**:

* **config** (DialogueCompressorConfig): Processor configuration, see above.

**Example**:

```python
>>> import os
>>> import asyncio
>>> from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
>>> from openjiuwen.core.context_engine.processor import forked
>>> from openjiuwen.core.context_engine.processor.forked.compressor.dialogue_compressor import (
...     DialogueCompressor,
...     DialogueCompressorConfig,
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
...     compressor_config = DialogueCompressorConfig(
...         trigger_context_ratio=0.8,
...         model=model_config,
...         model_client=model_client_config,
...     )
...     forked.activate()  # register the processors so they can be referenced by name
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
...     # Below trigger_context_ratio, no compression happens
...     return len(ctx.get_messages())
>>>
>>> asyncio.run(main())
4
```

> The example output `4` is the original message count when compression does
> not trigger. When compression triggers, all history before the current round
> is replaced by a single `<memory_block_dialogue>` summary, so
> `get_messages()` becomes "1 summary + current-round messages". Note the
> example contains only one dialogue round, whose messages all belong to the
> current round, so there is nothing to compress even at threshold; the effect
> shows up with multiple completed rounds in history.
