# openjiuwen.core.context_engine.processor.offloader.message_offloader

## class openjiuwen.core.context_engine.processor.offloader.message_offloader.MessageOffloaderConfig

`MessageOffloader` 的配置类。当消息数量或 token 数超过阈值时，对符合条件的消息进行裁剪并 offload，以控制上下文规模。

* **messages_threshold**(int | None，可选)：内存中消息数超过该值时触发 offload。默认值：`None`。
* **tokens_threshold**(int，可选)：累计 token 数超过该值时触发 offload。默认值：`20000`。
* **large_message_threshold**(int，可选)：单条消息 token 数超过该值时视为「大消息」，可被 offload。默认值：`1000`。
* **offload_message_type**(list[Literal["user", "assistant", "tool"]]，可选)：可被 offload 的消息 role 列表。默认值：`["tool"]`。
* **trim_size**(int，可选)：offload 时保留的 token 数，其余用省略标记替换。默认值：`100`。
* **messages_to_keep**(int | None，可选)：保证保留的最新增消息数量。默认值：`None`。
* **keep_last_round**(bool，可选)：是否始终保留最近一轮 user-assistant 对话。默认值：`True`。

**约束**：`trim_size` 必须小于 `large_message_threshold`；若同时设置 `messages_to_keep` 与 `messages_threshold`，则 `messages_to_keep` 必须小于 `messages_threshold`。

## class openjiuwen.core.context_engine.processor.offloader.message_offloader.MessageOffloader

```python
MessageOffloader(config: MessageOffloaderConfig)
```

`MessageOffloader` 继承自 [ContextProcessor](base.md#class-openjiuwencorecontext_engineprocessorbasecontextprocessor)，在 `add_messages` 时对超出阈值的大消息进行裁剪和 offload。接口与基类一致，详见 [ContextProcessor](base.md#class-openjiuwencorecontext_engineprocessorbasecontextprocessor)。

**参数**：

* **config**(MessageOffloaderConfig)：处理器配置，见上文。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
>>> from openjiuwen.core.context_engine.processor.offloader.message_offloader import (
...     MessageOffloader,
...     MessageOffloaderConfig,
... )
>>> from openjiuwen.core.foundation.llm import UserMessage, AssistantMessage, ToolMessage
>>>
>>> async def main():
...     config = ContextEngineConfig(default_window_message_num=100)
...     engine = ContextEngine(config)
...     offloader_config = MessageOffloaderConfig(
...         messages_threshold=3,
...         large_message_threshold=50,
...         trim_size=20,
...         offload_message_type=["tool"],
...     )
...     ctx = await engine.create_context(
...         "demo_ctx",
...         None,
...         history_messages=[],
...         processors=[("MessageOffloader", offloader_config)],
...     )
...     await ctx.add_messages([
...         UserMessage(content="调用工具查询数据"),
...         AssistantMessage(content="", tool_calls=[{"id": "1", "name": "query", "type": "function", "arguments": "{}"}]),
...         ToolMessage(content="x" * 100, tool_call_id="1"),
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
