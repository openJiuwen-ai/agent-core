# openjiuwen.core.context_engine.processor.compressor.round_level_compressor

## class openjiuwen.core.context_engine.processor.compressor.round_level_compressor.RoundLevelCompressorConfig

`RoundLevelCompressor` 的配置类。当 token 数超过阈值且存在足够多的连续「简单对话轮」（User + Assistant，无 tool call）时，将同层级的多轮压缩为两条摘要消息（一条 user 摘要、一条 assistant 摘要）。

* **rounds_threshold**(int，可选)：触发压缩所需的连续同层级对话轮数量。默认值：`10`。
* **tokens_threshold**(int，可选)：累计 token 数超过该值时才会触发压缩（需同时满足 rounds 条件）。默认值：`10000`。
* **keep_last_round**(bool，可选)：是否始终保留最近一轮简单对话不被压缩。默认值：`True`。
* **customized_compression_prompt**(str | None，可选)：自定义压缩 prompt；为 `None` 时使用内置 prompt。默认值：`None`。
* **model**(ModelRequestConfig | None，可选)：用于执行压缩的模型请求配置。默认值：`None`。
* **model_client**(ModelClientConfig | None，可选)：用于执行压缩的模型服务配置。默认值：`None`。

**约束**：进行压缩时需配置 `model` 与 `model_client`。触发条件为 token 数超阈值 **且** 存在不少于 `rounds_threshold` 的连续同层级简单对话轮。

## class openjiuwen.core.context_engine.processor.compressor.round_level_compressor.RoundLevelCompressor

```python
RoundLevelCompressor(config: RoundLevelCompressorConfig)
```

`RoundLevelCompressor` 继承自 [ContextProcessor](base.md#class-openjiuwencorecontext_engineprocessorbasecontextprocessor)，在 `add_messages` 时识别连续的同层级简单对话轮（User + Assistant，无 tool call），经 LLM 分别压缩 user 与 assistant 消息后合并为两条摘要。接口与基类一致，详见 [ContextProcessor](base.md#class-openjiuwencorecontext_engineprocessorbasecontextprocessor)。

**参数**：

* **config**(RoundLevelCompressorConfig)：处理器配置，见上文。

**样例**：

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
...         rounds.append(UserMessage(content=f"用户第{i}轮提问"))
...         rounds.append(AssistantMessage(content=f"助手第{i}轮回复"))
...     await ctx.add_messages(rounds)
...     msgs = ctx.get_messages()
...     assert len(msgs) < 8
...     return len(msgs)
>>>
>>> asyncio.run(main())
4
```
