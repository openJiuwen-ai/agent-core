# openjiuwen.core.context_engine.processor.offloader.message_summary_offloader

## class openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.MessageSummaryOffloaderConfig

`MessageSummaryOffloader` 的配置类。当消息数量或 token 数超过阈值时，对符合条件的大消息先经 LLM 生成摘要再 offload，相比 [MessageOffloader](message_offloader.md) 可保留更多语义。

* **messages_threshold**(int | None，可选)：内存中消息数超过该值时触发 offload。默认值：`None`。
* **tokens_threshold**(int，可选)：累计 token 数超过该值时触发 offload。默认值：`20000`。
* **large_message_threshold**(int，可选)：单条消息 token 数超过该值时视为「大消息」，可被 offload。默认值：`1000`。
* **offload_message_type**(list[Literal["user", "assistant", "tool"]]，可选)：可被 offload 的消息 role 列表。默认值：`["tool"]`。
* **messages_to_keep**(int | None，可选)：保证保留的最新增消息数量。默认值：`None`。
* **keep_last_round**(bool，可选)：是否始终保留最近一轮 user-assistant 对话。默认值：`True`。
* **model**(ModelRequestConfig | None，可选)：用于执行摘要的模型请求配置。默认值：`None`。
* **model_client**(ModelClientConfig | None，可选)：用于执行摘要的模型服务配置。默认值：`None`。
* **customized_summary_prompt**(str | None，可选)：自定义摘要 prompt；为 `None` 时使用内置 prompt。默认值：`None`。

**约束**：若同时设置 `messages_to_keep` 与 `messages_threshold`，则 `messages_to_keep` 必须小于 `messages_threshold`。进行摘要时需配置 `model` 与 `model_client`。

## class openjiuwen.core.context_engine.processor.offloader.message_summary_offloader.MessageSummaryOffloader

```python
MessageSummaryOffloader(config: MessageSummaryOffloaderConfig)
```

`MessageSummaryOffloader` 继承自 [MessageOffloader](message_offloader.md#class-openjiuwencorecontext_engineprocessoroffloadermessage_offloadermessageoffloader)，在 offload 前先调用 LLM 对大消息生成 2–4 句摘要，再以摘要 + 占位符替换原文。接口与基类一致，详见 [ContextProcessor](message_offloader.md#class-openjiuwencorecontext_engineprocessoroffloadermessage_offloadermessageoffloader)。

**参数**：

* **config**(MessageSummaryOffloaderConfig)：处理器配置，见上文。

**样例**：

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
...         UserMessage(content="调用工具查询数据"),
...         AssistantMessage(content="", tool_calls=[{"id": "1", "name": "query", "type": "function", "arguments": "{}"}]),
...         ToolMessage(
...             content="查询结果：2024年Q1营收同比增长15%，净利润为1.2亿元，主要来自云服务业务。"
...             + "详细数据包括：云服务收入占比达60%，企业客户续费率提升至92%。",
...             tool_call_id="1",
...         ),
...         UserMessage(content="继续"),
...     ])
...     msgs = ctx.get_messages()
...     tool_msg = next(m for m in msgs if m.role == "tool")
...     assert "[[OFFLOAD:" in tool_msg.content
...     return len(msgs)
>>>
>>> asyncio.run(main())
3
```
