# openjiuwen.core.context_engine.processor.forked.compressor.round_level_compressor

## class openjiuwen.core.context_engine.processor.forked.compressor.round_level_compressor.RoundLevelCompressorConfig

Configuration class for `RoundLevelCompressor`. When the context token count reaches a given ratio of the context capacity, only the most recent `keep_recent_messages` messages are kept, and all earlier history messages are compressed by an LLM into a single `<memory_block_round>` cross-round checkpoint summary message.

* **trigger_context_ratio** (float, optional): Triggers compression when the context token count reaches this ratio of the context capacity. Range (0, 1). Default value: `0.8`.
* **keep_recent_messages** (int, optional): Number of most recent messages kept uncompressed. Default value: `4`.
* **min_target_context_ratio** (float, optional): Skips compression when the compressible messages' token count is below this ratio of the context capacity. Range [0, 1). Default value: `0.1`.
* **model** (ModelRequestConfig | None, optional): Model request configuration used to perform compression. Default value: `None`.
* **model_client** (ModelClientConfig | None, optional): Model service configuration used to perform compression. Default value: `None`.
* **enable_compression_dump** (bool, optional): Whether to persist each real compression invocation (the request plus the post-compression context) to disk for offline analysis. Default value: `False`.
* **compression_dump_dir** (str | None, optional): Directory for compression dump files; uses the default directory when `None`. Default value: `None`.

**Constraints**: `model` and `model_client` must both be configured, otherwise the processor never triggers. The kept-message boundary is automatically extended backwards so that a tool call and its tool result are never split apart.

## class openjiuwen.core.context_engine.processor.forked.compressor.round_level_compressor.RoundLevelCompressor

```python
RoundLevelCompressor(config: RoundLevelCompressorConfig)
```

`RoundLevelCompressor` inherits from [ContextProcessor](base.md#class-openjiuwencorecontext_engineprocessorbasecontextprocessor). When the context window is being materialized, it checks whether the context token count reaches `trigger_context_ratio`; if so, it compresses all history messages except the most recent `keep_recent_messages` ones into a cross-round checkpoint summary (which may cover long-running project state, prior decisions, current goals, completed and unfinished work, etc.), embedding a meaning note and a conflict policy. Interface is consistent with the base class, see [ContextProcessor](base.md#class-openjiuwencorecontext_engineprocessorbasecontextprocessor).

**Parameters**:

* **config** (RoundLevelCompressorConfig): Processor configuration, see above.

**Example**:

```python
>>> import os
>>> import asyncio
>>> from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
>>> from openjiuwen.core.context_engine.processor import forked
>>> from openjiuwen.core.context_engine.processor.forked.compressor.round_level_compressor import (
...     RoundLevelCompressor,
...     RoundLevelCompressorConfig,
... )
>>> from openjiuwen.core.foundation.llm import (
...     UserMessage,
...     AssistantMessage,
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
...     compressor_config = RoundLevelCompressorConfig(
...         trigger_context_ratio=0.8,
...         keep_recent_messages=4,
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
...         processors=[("RoundLevelCompressor", compressor_config)],
...     )
...     rounds = []
...     for i in range(4):
...         rounds.append(UserMessage(content=f"user question of round {i}"))
...         rounds.append(AssistantMessage(content=f"assistant reply of round {i}"))
...     await ctx.add_messages(rounds)
...     # Below trigger_context_ratio, no compression happens
...     return len(ctx.get_messages())
>>>
>>> asyncio.run(main())
8
```

> The example output `8` is the original message count when compression does
> not trigger. When compression triggers, the most recent `keep_recent_messages`
> messages (4 in the example) are kept and earlier history is replaced by a
> single `<memory_block_round>` summary, so `get_messages()` becomes "1 summary
> + keep_recent_messages".
