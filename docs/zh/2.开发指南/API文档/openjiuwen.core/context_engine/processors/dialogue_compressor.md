# openjiuwen.core.context_engine.processor.forked.compressor.dialogue_compressor

## class openjiuwen.core.context_engine.processor.forked.compressor.dialogue_compressor.DialogueCompressorConfig

`DialogueCompressor` 的配置类。当上下文 token 数达到上下文容量的一定比例时，将「当前对话轮」（最后一条真实用户消息）之前的全部历史消息经 LLM 压缩为一条 `<memory_block_dialogue>` 摘要消息，当前轮消息完整保留。

* **trigger_context_ratio**(float，可选)：上下文 token 数占上下文容量的比例达到该值时触发压缩。取值范围 (0, 1)。默认值：`0.8`。
* **min_target_context_ratio**(float，可选)：可压缩消息的 token 数低于上下文容量的该比例时跳过本次压缩，避免收益过小的压缩。取值范围 [0, 1)。默认值：`0.1`。
* **model**(ModelRequestConfig | None，可选)：用于执行压缩的模型请求配置。默认值：`None`。
* **model_client**(ModelClientConfig | None，可选)：用于执行压缩的模型服务配置。默认值：`None`。
* **enable_compression_dump**(bool，可选)：是否将每次真实压缩的请求与压缩后上下文落盘，用于离线效果分析；关闭时无额外开销。默认值：`False`。
* **compression_dump_dir**(str | None，可选)：压缩落盘文件的存放目录；为 `None` 时使用默认目录。默认值：`None`。

**约束**：`model` 与 `model_client` 必须同时配置，否则该处理器永远不会触发。保留消息边界会自动向前扩展，保证 tool call 与其 tool 结果不被拆散。

**迁移说明**：旧版 `DialogueCompressor`（`processor.compressor.dialogue_compressor`）按完整 tool-call 对话轮逐轮压缩，配置字段为 `messages_threshold`、`tokens_threshold`、`messages_to_keep`、`keep_last_round` 等；当前实现改为将「当前轮之前的全部历史」压缩为一条摘要，触发条件由消息数/token 数阈值改为上下文容量比例，配置字段不兼容。从旧版迁移时需按上表字段重新配置。

## class openjiuwen.core.context_engine.processor.forked.compressor.dialogue_compressor.DialogueCompressor

```python
DialogueCompressor(config: DialogueCompressorConfig)
```

`DialogueCompressor` 继承自 [ContextProcessor](base.md#class-openjiuwencorecontext_engineprocessorbasecontextprocessor)，在获取上下文窗口时判断上下文 token 数是否达到 `trigger_context_ratio`，若是则将当前轮之前的历史消息压缩为一条摘要消息并替换原消息，同时在摘要中附带含义说明与冲突处理策略（更新的原始消息与最新用户意图优先于摘要）。接口与基类一致，详见 [ContextProcessor](base.md#class-openjiuwencorecontext_engineprocessorbasecontextprocessor)。

**参数**：

* **config**(DialogueCompressorConfig)：处理器配置，见上文。

**样例**：

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
...     forked.activate()  # 注册处理器，之后可按名称引用
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
...     # 消息量未达到 trigger_context_ratio，不发生压缩
...     return len(ctx.get_messages())
>>>
>>> asyncio.run(main())
4
```

> 示例输出 `4` 为未触发压缩时的原始消息数。触发压缩时，当前轮之前的全部
> 历史会被一条 `<memory_block_dialogue>` 摘要替换，`get_messages()` 的长度
> 变为「1 条摘要 + 当前轮消息数」。注意上例只有一轮对话，当前轮即全部消息，
> 因此即使达到阈值也没有可压缩的历史；真实场景中历史包含多个已完成对话轮时
> 压缩效果才会体现。
