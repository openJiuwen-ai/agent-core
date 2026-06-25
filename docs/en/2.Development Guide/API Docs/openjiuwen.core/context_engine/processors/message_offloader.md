# openjiuwen.core.context_engine.processor.offloader.message_offloader

## MessageOffloaderConfig

`MessageOffloader` uses context-relative percentage thresholds. Message count and accumulated-token
thresholds are not configurable.

* **ttl_seconds** (int, optional): Idle time between context-window requests before TTL processing
  is eligible. Set to `0` to disable TTL processing. Default: `300`.
* **enable_rule_compression** (bool, optional): Whether deterministic rule compression runs before
  offload fallback. When `False`, oversized tool results are offloaded directly with a head/tail
  preview. Default: `True`.
* **add_message_threshold_ratio** (float, optional): During `add_messages`, process one tool
  message only when it exceeds `context_window_tokens * 3 * add_message_threshold_ratio`.
  Default: `0.2`.
* **ttl_context_occupancy_ratio** (float, optional): TTL processing is eligible only when the full
  ModelContext character occupancy reaches `context_window_tokens * 3 * ttl_context_occupancy_ratio`.
  Default: `0.5`.
* **ttl_message_threshold_ratio** (float, optional): During TTL, process one tool message only when
  it exceeds `context_window_tokens * 3 * ttl_message_threshold_ratio`. Default: `0.1`.
* **offload_preview_head_tail_chars** (int, optional): Number of head and tail characters retained
  in inline placeholders for direct offload and reused offload previews. Default: `2000`.
* **protected_tool_names** (list[str], optional): Tool names, or `tool:argument-pattern` entries,
  that must remain inline. Default: `["reload_original_context_messages"]`.

## MessageOffloader

The processor estimates character capacity as `context_window_tokens * 3`.

* During `add_messages`, a single tool result is processed only when its character length is
  greater than `add_message_threshold_ratio` of that capacity.
* When rule compression is disabled, oversized tool results are offloaded directly and the inline
  placeholder keeps a head/tail preview with a truncation notice in the middle.
* When rule compression still produces content above the threshold, offload is used as fallback.
  If the compression result is already an offloaded message, the existing handle/type are reused
  and only the inline placeholder is further truncated.
* At `get_context_window`, TTL processing runs only when the request has been idle for
  `ttl_seconds` and the complete persistent model context occupies at least
  `ttl_context_occupancy_ratio` of capacity.
* TTL processing traverses the complete model context, not only the returned sliding window.
* Tool messages carrying `rule_compressed_at` are skipped and are not compressed again.
* Each eligible TTL pass applies the same rule: process single messages above
  `ttl_message_threshold_ratio`; keep results that fit the TTL budget and immediately offload
  results that remain oversized.

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
