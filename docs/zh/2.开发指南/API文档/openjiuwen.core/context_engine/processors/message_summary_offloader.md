# openjiuwen.core.context_engine.processor.forked.offloader.message_offloader

> **迁移说明**：本文档描述默认处理链使用的实现。旧模块路径
> `openjiuwen.core.context_engine.processor.offloader.message_summary_offloader`
> 下的同名类仍然存在，但配置字段与行为不同（旧版经 LLM 生成摘要后 offload，
> 使用 `large_message_threshold`、`offload_message_type` 等字段），两者不能
> 混用。从旧版迁移时请按本文档的模块路径导入，并按 `MessageSummaryOffloaderConfig`
> 的新字段重新配置。

## class openjiuwen.core.context_engine.processor.forked.offloader.message_offloader.MessageSummaryOffloaderConfig

`MessageSummaryOffloader` 的配置类。该处理器不依赖 LLM：当 tool 消息过大时，先按内置规则压缩，再将原始内容 offload 到文件系统，上下文中仅保留头尾预览与 `[[OFFLOAD: ...]]` 占位符，需要时可按占位符中的句柄/路径取回原文。

* **add_message_threshold_ratio**(float，可选)：新增消息时，单条 tool 消息的 token 数超过上下文容量 × 该比例时被处理。默认值：`0.1`。
* **ttl_seconds**(int，可选)：距离上一次获取上下文窗口超过该秒数后，TTL 处理才可能触发；`0` 表示关闭 TTL 处理。默认值：`300`。
* **ttl_context_occupancy_ratio**(float，可选)：上下文 token 占用达到上下文容量 × 该比例时，TTL 处理才可能触发。默认值：`0.5`。
* **ttl_message_threshold_ratio**(float，可选)：TTL 处理时，单条历史 tool 消息的 token 数超过上下文容量 × 该比例时被处理。默认值：`0.05`。
* **protected_tool_names**(list[str]，可选)：始终保留原文、不做压缩与 offload 的工具名列表；支持 `"工具名"` 或 `"工具名:参数glob模式"` 两种写法。默认值：`["read_file"]`。
* **enable_debug_dump**(bool，可选)：是否将规则压缩与 offload 的调试记录落盘。默认值：`False`。
* **debug_dump_dir**(str | None，可选)：调试记录存放目录，支持 `{session_id}`、`{context_id}` 占位符；为 `None` 时使用默认目录。默认值：`None`。

**约束**：offload 文件默认写入 `{workspace}/context/{session_id}_context/offload/` 目录；上下文未关联 workspace 时 offload 不会生效，消息保持原样。

## class openjiuwen.core.context_engine.processor.forked.offloader.message_offloader.MessageSummaryOffloader

```python
MessageSummaryOffloader(config: MessageSummaryOffloaderConfig)
```

`MessageSummaryOffloader` 继承自 [ContextProcessor](base.md#class-openjiuwencorecontext_engineprocessorbasecontextprocessor)，在两个时机介入：

1. 新增消息（`on_add_messages`）：对超过 `add_message_threshold_ratio` 的新增 tool 消息做规则压缩与 offload；
2. 获取上下文窗口（`on_get_context_window`）：当上下文空闲超过 `ttl_seconds` 且占用达到 `ttl_context_occupancy_ratio` 时，对超过 `ttl_message_threshold_ratio` 的历史 tool 消息做同样处理。

接口与基类一致，详见 [ContextProcessor](base.md#class-openjiuwencorecontext_engineprocessorbasecontextprocessor)。

**参数**：

* **config**(MessageSummaryOffloaderConfig)：处理器配置，见上文。

**样例**：

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
...     forked.activate()  # 注册处理器，之后可按名称引用
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
...         ToolMessage(content="查询结果：营收增长15%", tool_call_id="1"),
...     ])
...     # 消息未达到 add_message_threshold_ratio，不发生 offload
...     return len(ctx.get_messages())
>>>
>>> asyncio.run(main())
3
```

> 示例输出 `3` 为未触发 offload 时的原始消息数。当 tool 消息超过
> `add_message_threshold_ratio` 时，该消息会被规则压缩并 offload 到文件系统，
> 上下文中替换为头尾预览加 `[[OFFLOAD: ...]]` 占位符（消息条数不变，内容变短）。
