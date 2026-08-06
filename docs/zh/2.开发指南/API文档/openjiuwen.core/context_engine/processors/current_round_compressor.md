# openjiuwen.core.context_engine.processor.forked.compressor.current_round_compressor

## class openjiuwen.core.context_engine.processor.forked.compressor.current_round_compressor.CurrentRoundCompressorConfig

`CurrentRoundCompressor` 的配置类。当上下文 token 数达到上下文容量的一定比例时，将「当前轮」内已经完成的工作（最后一条真实用户消息之后产生的推理、工具调用与工具结果等消息）经 LLM 压缩为一条 `<memory_block_current>` 摘要消息，用户请求本身保留不动。

* **trigger_context_ratio**(float，可选)：上下文 token 数占上下文容量的比例达到该值时触发压缩。取值范围 (0, 1)。默认值：`0.8`。
* **min_target_context_ratio**(float，可选)：可压缩消息的 token 数低于上下文容量的该比例时跳过本次压缩。取值范围 [0, 1)。默认值：`0.1`。
* **keep_recent_messages**(int，可选)：压缩时在当前轮尾部保留的最新消息条数。默认值：`0`。
* **model**(ModelRequestConfig | None，可选)：用于执行压缩的模型请求配置。默认值：`None`。
* **model_client**(ModelClientConfig | None，可选)：用于执行压缩的模型服务配置。默认值：`None`。
* **enable_compression_dump**(bool，可选)：是否将每次真实压缩的请求与压缩后上下文落盘，用于离线效果分析。默认值：`False`。
* **compression_dump_dir**(str | None，可选)：压缩落盘文件的存放目录；为 `None` 时使用默认目录。默认值：`None`。

**约束**：`model` 与 `model_client` 必须同时配置，否则该处理器永远不会触发。保留消息边界会自动向前扩展，保证 tool call 与其 tool 结果不被拆散。

## class openjiuwen.core.context_engine.processor.forked.compressor.current_round_compressor.CurrentRoundCompressor

```python
CurrentRoundCompressor(config: CurrentRoundCompressorConfig)
```

`CurrentRoundCompressor` 继承自 [ContextProcessor](base.md#class-openjiuwencorecontext_engineprocessorbasecontextprocessor)，在获取上下文窗口时判断上下文 token 数是否达到 `trigger_context_ratio`，若是则将当前轮内已完成的工作压缩为一条摘要消息，用于恢复已完成的分析、工具调用、代码改动、测试结果与后续步骤；最后一条真实用户消息及其之前的内容作为保留前缀不受压缩影响。接口与基类一致，详见 [ContextProcessor](base.md#class-openjiuwencorecontext_engineprocessorbasecontextprocessor)。

**参数**：

* **config**(CurrentRoundCompressorConfig)：处理器配置，见上文。

**样例**：

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
...     forked.activate()  # 注册处理器，之后可按名称引用
...     engine_config = ContextEngineConfig(default_window_message_num=100)
...     engine = ContextEngine(engine_config)
...     ctx = await engine.create_context(
...         "demo_ctx",
...         None,
...         history_messages=[],
...         processors=[("CurrentRoundCompressor", compressor_config)],
...     )
...     await ctx.add_messages([
...         UserMessage(content="帮我修复这个报错"),
...         AssistantMessage(content="", tool_calls=[{"id": "1", "name": "read_file", "type": "function", "arguments": "{}"}]),
...         ToolMessage(content="文件内容……", tool_call_id="1"),
...         AssistantMessage(content="已定位问题，正在修复。"),
...     ])
...     # 消息量未达到 trigger_context_ratio，不发生压缩
...     return len(ctx.get_messages())
>>>
>>> asyncio.run(main())
4
```

> 示例输出 `4` 为未触发压缩时的原始消息数。触发压缩时，用户请求保留不动，
> 当前轮内除尾部 `keep_recent_messages` 条（上例为 2 条）之外的消息被一条
> `<memory_block_current>` 摘要替换，`get_messages()` 的长度变为
> 「1 条用户消息 + 1 条摘要 + keep_recent_messages」。
