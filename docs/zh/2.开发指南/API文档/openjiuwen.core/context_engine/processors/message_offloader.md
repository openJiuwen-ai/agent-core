# openjiuwen.core.context_engine.processor.offloader.message_offloader

## MessageOffloaderConfig

`MessageOffloader` 使用上下文相对百分比阈值，不再配置消息数阈值或累计 token 阈值。

* **ttl_seconds**（int，可选）：两次上下文窗口请求之间的空闲时间达到该值后，TTL
  处理才具备触发资格。设置为 `0` 可关闭 TTL。默认值：`300`。
* **enable_rule_compression**（bool，可选）：是否在 offload 兜底前先执行确定性规则压缩。
  设置为 `False` 时，超阈值工具结果直接以头尾预览形式 offload。默认值：`True`。
* **add_message_threshold_ratio**（float，可选）：`add_messages` 阶段单条工具消息超过
  `context_window_tokens * 3 * add_message_threshold_ratio` 后才处理。默认值：`0.2`。
* **ttl_context_occupancy_ratio**（float，可选）：TTL 阶段完整 ModelContext 字符占用超过
  `context_window_tokens * 3 * ttl_context_occupancy_ratio` 后才具备处理资格。默认值：`0.5`。
* **ttl_message_threshold_ratio**（float，可选）：TTL 阶段单条工具消息超过
  `context_window_tokens * 3 * ttl_message_threshold_ratio` 后才处理。默认值：`0.1`。
* **offload_preview_head_tail_chars**（int，可选）：直接 offload 或复用已有 offload 时，
  inline 占位内容保留的头部和尾部字符数。默认值：`2000`。
* **protected_tool_names**（list[str]，可选）：禁止压缩和卸载的工具名，支持
  `工具名:参数通配符` 格式。默认值：`["reload_original_context_messages"]`。

## MessageOffloader

处理器按 `context_window_tokens * 3` 估算上下文字符容量。

* `add_messages` 阶段，只有单条工具消息字符数严格超过容量的
  `add_message_threshold_ratio` 才触发处理。
* 规则压缩关闭时，超阈值工具消息直接 offload，inline 内容保留头尾预览，中间插入
  “已截断并卸载”的说明。
* 规则压缩结果仍超阈值时走 offload 兜底；如果压缩结果本身已经是 offload 消息，则复用
  原有 handle/type，只进一步截断 inline 占位内容，不重复卸载原文。
* `get_context_window` 阶段，只有距离上次请求达到 TTL，且完整持久化 ModelContext
  的字符占用达到容量的 `ttl_context_occupancy_ratio`，才执行 TTL 处理。
* TTL 遍历完整 ModelContext，而不是只遍历本次返回的滑动窗口。
* 已带有 `rule_compressed_at` 的工具消息直接跳过，不再重复压缩。
* 每次 TTL 都执行相同规则：单条消息超过 `ttl_message_threshold_ratio` 后才处理；
  压缩后进入 TTL 预算则继续保留，仍超过预算则当次直接卸载。

```python
from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.context_engine.processor.offloader.message_offloader import (
    MessageOffloaderConfig,
)

engine = ContextEngine(ContextEngineConfig(context_window_tokens=128_000))
context = await engine.create_context(
    "demo",
    processors=[("MessageOffloader", MessageOffloaderConfig(ttl_seconds=300))],
)
```
