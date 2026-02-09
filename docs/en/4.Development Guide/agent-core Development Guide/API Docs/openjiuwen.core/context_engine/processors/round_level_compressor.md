# openjiuwen.core.context_engine.processor.compressor.round_level_compressor

## class openjiuwen.core.context_engine.processor.compressor.round_level_compressor.RoundLevelCompressorConfig

Configuration class for `RoundLevelCompressor`. When the token count exceeds the threshold and there are enough consecutive "simple dialogue rounds" (User + Assistant, no tool call), compresses multiple rounds at the same level into two summary messages (one user summary, one assistant summary).

* **rounds_threshold** (int, optional): Number of consecutive dialogue rounds at the same level required to trigger compression. Default value: `10`.
* **tokens_threshold** (int, optional): Compression is only triggered when the cumulative token count exceeds this value (must also satisfy the rounds condition). Default value: `10000`.
* **keep_last_round** (bool, optional): Whether to always keep the most recent simple dialogue round uncompressed. Default value: `True`.
* **customized_compression_prompt** (str | None, optional): Custom compression prompt; uses built-in prompt when `None`. Default value: `None`.
* **model** (ModelRequestConfig | None, optional): Model request configuration for performing compression. Default value: `None`.
* **model_client** (ModelClientConfig | None, optional): Model service configuration for performing compression. Default value: `None`.

**Constraints**: `model` and `model_client` must be configured when performing compression. Trigger condition is token count exceeding threshold **and** there are at least `rounds_threshold` consecutive simple dialogue rounds at the same level.

## class openjiuwen.core.context_engine.processor.compressor.round_level_compressor.RoundLevelCompressor

```python
RoundLevelCompressor(config: RoundLevelCompressorConfig)
```

`RoundLevelCompressor` inherits from [ContextProcessor](./base.md#class-openjiuwencorecontext_engineprocessorbasecontextprocessor), identifying consecutive simple dialogue rounds at the same level (User + Assistant, no tool call) during `add_messages`, compressing user and assistant messages separately via LLM, then merging into two summaries. Interface is consistent with the base class, see [ContextProcessor](./base.md#class-openjiuwencorecontext_engineprocessorbasecontextprocessor).

**Parameters**:

* **config** (RoundLevelCompressorConfig): Processor configuration, see above.

**Example**:

```python
>>> import os
>>> import asyncio
>>> from unittest.mock import MagicMock
>>> from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
>>> from openjiuwen.core.context_engine.processor.compressor.round_level_compressor import (
...     RoundLevelCompressor,
...     RoundLevelCompressorConfig,
... )
>>> from openjiuwen.core.context_engine.token.base import TokenCounter
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
...     mock_counter = MagicMock(spec=TokenCounter)
...     mock_counter.count_messages = MagicMock(return_value=15000)
...
...     model_config = ModelRequestConfig(model=MODEL_NAME)
...     model_client_config = ModelClientConfig(
...         client_provider=MODEL_PROVIDER,
...         api_base=API_BASE,
...         api_key=API_KEY,
...     )
...     compressor_config = RoundLevelCompressorConfig(
...         rounds_threshold=3,
...         tokens_threshold=5000,
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
...         processors=[("RoundLevelCompressor", compressor_config)],
...         token_counter=mock_counter,
...     )
...     rounds = []
...     for i in range(4):
...         rounds.append(UserMessage(content=f"User question round {i}"))
...         rounds.append(AssistantMessage(content=f"Assistant reply round {i}"))
...     await ctx.add_messages(rounds)
...     msgs = ctx.get_messages()
...     assert len(msgs) < 8
...     return len(msgs)
>>>
>>> asyncio.run(main())
4
```
