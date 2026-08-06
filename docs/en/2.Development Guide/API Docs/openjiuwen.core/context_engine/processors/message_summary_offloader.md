# openjiuwen.core.context_engine.processor.forked.offloader.message_offloader

> **Migration note**: This page documents the implementation used by the default
> processor chain. The same-named class under the old module path
> `openjiuwen.core.context_engine.processor.offloader.message_summary_offloader`
> still exists, but its configuration fields and behavior differ (the old
> implementation summarizes via an LLM before offloading, using fields such as
> `large_message_threshold` and `offload_message_type`), and the two are not
> interchangeable. When migrating from the old version, import from the module
> path shown on this page and reconfigure with the new
> `MessageSummaryOffloaderConfig` fields.

## class openjiuwen.core.context_engine.processor.forked.offloader.message_offloader.MessageSummaryOffloaderConfig

Configuration class for `MessageSummaryOffloader`. This processor does not rely on an LLM: when a tool message is too large, it first applies built-in rule-based compression, then offloads the original content to the filesystem, keeping only a head/tail preview plus an `[[OFFLOAD: ...]]` placeholder in the context; the original content can be retrieved later via the handle/path in the placeholder.

* **add_message_threshold_ratio** (float, optional): When adding messages, a single tool message whose token count exceeds context capacity × this ratio is processed. Default value: `0.1`.
* **ttl_seconds** (int, optional): TTL processing becomes eligible only after this many seconds have passed since the last context-window request; `0` disables TTL processing. Default value: `300`.
* **ttl_context_occupancy_ratio** (float, optional): TTL processing becomes eligible only when the context token occupancy reaches context capacity × this ratio. Default value: `0.5`.
* **ttl_message_threshold_ratio** (float, optional): During TTL processing, a single historical tool message whose token count exceeds context capacity × this ratio is processed. Default value: `0.05`.
* **protected_tool_names** (list[str], optional): Tools whose results are always kept inline and never compressed or offloaded; each entry is either `"tool_name"` or `"tool_name:argument-glob-pattern"`. Default value: `["read_file"]`.
* **enable_debug_dump** (bool, optional): Whether to persist rule-compression and offload debug records to disk. Default value: `False`.
* **debug_dump_dir** (str | None, optional): Directory for debug records, supporting `{session_id}` and `{context_id}` placeholders; uses the default directory when `None`. Default value: `None`.

**Constraints**: Offloaded files are written under `{workspace}/context/{session_id}_context/offload/` by default; when the context is not associated with a workspace, offloading does not take effect and messages are kept as-is.

## class openjiuwen.core.context_engine.processor.forked.offloader.message_offloader.MessageSummaryOffloader

```python
MessageSummaryOffloader(config: MessageSummaryOffloaderConfig)
```

`MessageSummaryOffloader` inherits from [ContextProcessor](base.md#class-openjiuwencorecontext_engineprocessorbasecontextprocessor) and intervenes at two points:

1. Adding messages (`on_add_messages`): newly added tool messages exceeding `add_message_threshold_ratio` are rule-compressed and offloaded;
2. Materializing the context window (`on_get_context_window`): once the context has been idle for more than `ttl_seconds` and its occupancy reaches `ttl_context_occupancy_ratio`, historical tool messages exceeding `ttl_message_threshold_ratio` are processed the same way.

Interface is consistent with the base class, see [ContextProcessor](base.md#class-openjiuwencorecontext_engineprocessorbasecontextprocessor).

**Parameters**:

* **config** (MessageSummaryOffloaderConfig): Processor configuration, see above.

**Example**:

```python
>>> import asyncio
>>> from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
>>> from openjiuwen.core.context_engine.processor import forked
>>> from openjiuwen.core.context_engine.processor.forked.offloader.message_offloader import (
...     MessageSummaryOffloader,
...     MessageSummaryOffloaderConfig,
... )
>>> from openjiuwen.core.foundation.llm import (
...     UserMessage,
...     AssistantMessage,
...     ToolMessage,
... )
>>>
>>> async def main():
...     offloader_config = MessageSummaryOffloaderConfig(
...         add_message_threshold_ratio=0.1,
...         protected_tool_names=["read_file"],
...     )
...     forked.activate()  # register the processors so they can be referenced by name
...     engine_config = ContextEngineConfig(default_window_message_num=100)
...     engine = ContextEngine(engine_config)
...     ctx = await engine.create_context(
...         "demo_ctx",
...         None,
...         history_messages=[],
...         processors=[("MessageSummaryOffloader", offloader_config)],
...     )
...     await ctx.add_messages([
...         UserMessage(content="Call tool to query data"),
...         AssistantMessage(content="", tool_calls=[{"id": "1", "name": "query", "type": "function", "arguments": "{}"}]),
...         ToolMessage(content="Query result: revenue increased 15%", tool_call_id="1"),
...     ])
...     # Below add_message_threshold_ratio, no offload happens
...     return len(ctx.get_messages())
>>>
>>> asyncio.run(main())
3
```

> The example output `3` is the original message count when offload does not
> trigger. When a tool message exceeds `add_message_threshold_ratio`, it is
> rule-compressed and offloaded to the filesystem, replaced in the context by a
> head/tail preview plus an `[[OFFLOAD: ...]]` placeholder (message count stays
> the same, content gets shorter).
