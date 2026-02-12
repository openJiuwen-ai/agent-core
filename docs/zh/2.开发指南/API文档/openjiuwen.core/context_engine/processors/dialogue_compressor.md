# openjiuwen.core.context_engine.processor.compressor.dialogue_compressor

## class openjiuwen.core.context_engine.processor.compressor.dialogue_compressor.DialogueCompressorConfig

`DialogueCompressor` 的配置类。当消息数量或 token 数超过阈值时，将完整的「用户提问 → 模型 tool call → 工具返回 → 模型最终回复」对话轮压缩为一条摘要消息。

* **messages_threshold**(int | None，可选)：内存中消息数超过该值时触发压缩。默认值：`None`。
* **tokens_threshold**(int，可选)：累计 token 数超过该值时触发压缩。默认值：`10000`。
* **messages_to_keep**(int | None，可选)：保证保留的最新增消息数量。默认值：`None`。
* **keep_last_round**(bool，可选)：是否始终保留最近一轮完整对话不被压缩。默认值：`True`。
* **customized_compression_prompt**(str | None，可选)：自定义压缩 prompt；为 `None` 时使用内置 prompt。默认值：`None`。
* **compression_token_limit**(int，可选)：压缩后摘要允许的最大 token 数。默认值：`2000`。
* **model**(ModelRequestConfig | None，可选)：用于执行压缩的模型请求配置。默认值：`None`。
* **model_client**(ModelClientConfig | None，可选)：用于执行压缩的模型服务配置。默认值：`None`。

**约束**：若同时设置 `messages_to_keep` 与 `messages_threshold`，则 `messages_to_keep` 必须小于 `messages_threshold`。进行压缩时需配置 `model` 与 `model_client`。

## class openjiuwen.core.context_engine.processor.compressor.dialogue_compressor.DialogueCompressor

```python
DialogueCompressor(config: DialogueCompressorConfig)
```

`DialogueCompressor` 继承自 [ContextProcessor](base.md#class-openjiuwencorecontext_engineprocessorbasecontextprocessor)，在 `add_messages` 时识别完整的 tool-call 对话轮，经 LLM 压缩为一段摘要后替换原有多条消息。接口与基类一致，详见 [ContextProcessor](base.md#class-openjiuwencorecontext_engineprocessorbasecontextprocessor)。

**参数**：

* **config**(DialogueCompressorConfig)：处理器配置，见上文。

**样例**：

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
...         UserMessage(content="调用工具查询数据"),
...         AssistantMessage(content="", tool_calls=[{"id": "1", "name": "query", "type": "function", "arguments": "{}"}]),
...         ToolMessage(content="查询结果：营收增长15%", tool_call_id="1"),
...         AssistantMessage(content="根据工具返回，2024年Q1营收同比增长15%。"),
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
