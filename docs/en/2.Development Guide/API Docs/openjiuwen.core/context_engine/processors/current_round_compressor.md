# openjiuwen.core.context_engine.processor.forked.compressor.current_round_compressor

## class openjiuwen.core.context_engine.processor.forked.compressor.current_round_compressor.CurrentRoundCompressorConfig

Configuration class for `CurrentRoundCompressor`. When the context token count reaches a given ratio of the context capacity, the work already performed within the current round (reasoning, tool calls, tool results, etc. produced after the last real user message) is compressed by an LLM into a single `<memory_block_current>` summary message, while the user request itself is left untouched.

* **trigger_context_ratio** (float, optional): Triggers compression when the context token count reaches this ratio of the context capacity. Range (0, 1). Default value: `0.8`.
* **min_target_context_ratio** (float, optional): Skips compression when the compressible messages' token count is below this ratio of the context capacity. Range [0, 1). Default value: `0.1`.
* **keep_recent_messages** (int, optional): Number of most recent messages kept uncompressed at the tail of the current round. Default value: `0`.
* **model** (ModelRequestConfig | None, optional): Model request configuration used to perform compression. Default value: `None`.
* **model_client** (ModelClientConfig | None, optional): Model service configuration used to perform compression. Default value: `None`.
* **enable_compression_dump** (bool, optional): Whether to persist each real compression invocation (the request plus the post-compression context) to disk for offline analysis. Default value: `False`.
* **compression_dump_dir** (str | None, optional): Directory for compression dump files; uses the default directory when `None`. Default value: `None`.

**Constraints**: `model` and `model_client` must both be configured, otherwise the processor never triggers. The kept-message boundary is automatically extended backwards so that a tool call and its tool result are never split apart.

## class openjiuwen.core.context_engine.processor.forked.compressor.current_round_compressor.CurrentRoundCompressor

```python
CurrentRoundCompressor(config: CurrentRoundCompressorConfig)
```

`CurrentRoundCompressor` inherits from [ContextProcessor](base.md#class-openjiuwencorecontext_engineprocessorbasecontextprocessor). When the context window is being materialized, it checks whether the context token count reaches `trigger_context_ratio`; if so, it compresses the work already done in the current round into a summary message used to recover completed analysis, tool calls, code changes, test results, and next steps. The last real user message and everything before it form a preserved prefix that is never compressed. Interface is consistent with the base class, see [ContextProcessor](base.md#class-openjiuwencorecontext_engineprocessorbasecontextprocessor).

**Parameters**:

* **config** (CurrentRoundCompressorConfig): Processor configuration, see above.

**Example**:

```python
>>> import os
>>> import asyncio
>>> from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
>>> from openjiuwen.core.context_engine.processor import forked
>>> from openjiuwen.core.context_engine.processor.forked.compressor.current_round_compressor import (
...     CurrentRoundCompressor,
...     CurrentRoundCompressorConfig,
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
...     compressor_config = CurrentRoundCompressorConfig(
...         trigger_context_ratio=0.8,
...         keep_recent_messages=2,
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
...         processors=[("CurrentRoundCompressor", compressor_config)],
...     )
...     await ctx.add_messages([
...         UserMessage(content="Help me fix this error"),
...         AssistantMessage(content="", tool_calls=[{"id": "1", "name": "read_file", "type": "function", "arguments": "{}"}]),
...         ToolMessage(content="file content ...", tool_call_id="1"),
...         AssistantMessage(content="Located the problem, fixing it now."),
...     ])
...     # Below trigger_context_ratio, no compression happens
...     return len(ctx.get_messages())
>>>
>>> asyncio.run(main())
4
```

> The example output `4` is the original message count when compression does
> not trigger. When compression triggers, the user request is kept intact and
> all current-round messages except the trailing `keep_recent_messages` (2 in
> the example) are replaced by a single `<memory_block_current>` summary, so
> `get_messages()` becomes "1 user message + 1 summary + keep_recent_messages".
