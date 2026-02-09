# openjiuwen.core.context_engine.processor.offloader.message_offloader

## class openjiuwen.core.context_engine.processor.offloader.message_offloader.MessageOffloaderConfig

Configuration class for `MessageOffloader`. When the number of messages or tokens exceeds the threshold, trims and offloads eligible messages to control context size.

* **messages_threshold** (int | None, optional): Triggers offload when the number of messages in memory exceeds this value. Default value: `None`.
* **tokens_threshold** (int, optional): Triggers offload when the cumulative token count exceeds this value. Default value: `20000`.
* **large_message_threshold** (int, optional): Messages with token count exceeding this value are considered "large messages" and can be offloaded. Default value: `1000`.
* **offload_message_type** (list[Literal["user", "assistant", "tool"]], optional): List of message roles that can be offloaded. Default value: `["tool"]`.
* **trim_size** (int, optional): Number of tokens to retain when offloading, the rest are replaced with ellipsis markers. Default value: `100`.
* **messages_to_keep** (int | None, optional): Guaranteed minimum number of recent messages to keep. Default value: `None`.
* **keep_last_round** (bool, optional): Whether to always keep the most recent user-assistant dialogue round. Default value: `True`.

**Constraints**: `trim_size` must be less than `large_message_threshold`; if both `messages_to_keep` and `messages_threshold` are set, `messages_to_keep` must be less than `messages_threshold`.

## class openjiuwen.core.context_engine.processor.offloader.message_offloader.MessageOffloader

```python
MessageOffloader(config: MessageOffloaderConfig)
```

`MessageOffloader` inherits from [ContextProcessor](base.md#class-openjiuwencorecontext_engineprocessorbasecontextprocessor), trimming and offloading large messages that exceed the threshold during `add_messages`. Interface is consistent with the base class, see [ContextProcessor](base.md#class-openjiuwencorecontext_engineprocessorbasecontextprocessor).

**Parameters**:

* **config** (MessageOffloaderConfig): Processor configuration, see above.

**Example**:

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
...         UserMessage(content="Call tool to query data"),
...         AssistantMessage(content="", tool_calls=[{"id": "1", "name": "query", "type": "function", "arguments": "{}"}]),
...         ToolMessage(content="x" * 100, tool_call_id="1"),
...         UserMessage(content="Continue"),
...     ])
...     msgs = ctx.get_messages()
...     tool_msg = next(m for m in msgs if m.role == "tool")
...     assert "[[OFFLOAD:" in tool_msg.content
...     return len(msgs)
>>>
>>> asyncio.run(main())
3
```
