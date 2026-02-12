# openjiuwen.core.context_engine.config

## class openjiuwen.core.context_engine.config.ContextEngineConfig
```python
class openjiuwen.core.context_engine.config.ContextEngineConfig()
```
Represents the complete configuration structure for the context engine, used to control various behaviors and parameters of the context engine.

**Parameters**:

- **max_context_message_num** (int, optional): Maximum total number of messages allowed in any context. Default value: `None`, meaning no hard limit is enabled.
- **default_window_message_num** (int, optional): Number of latest messages to retain when creating a sliding window without explicitly specifying token count or message count. If explicitly set, the value must be greater than `0`. Default value: `None`, meaning unlimited.
- **default_window_round_num** (int, optional): Number of latest dialogue rounds to retain when creating a sliding window. A round is defined as: starting from a user message, ending at the next assistant message without tool calls (i.e., the final reply in a tool-call round). If the assistant executes tool calls, this process spans multiple messages but counts as one round logically. This configuration can effectively maintain dialogue coherence without specifying an exact message count. If explicitly set, the value must be greater than `0`, and in round-based message truncation logic, this configuration takes priority over `default_window_message_num`; default value: `None`, meaning the round-based window mechanism is disabled.
- **enable_kv_cache_release** (bool, optional): Whether to enable releasing key-value cache (KV-cache) for offloaded messages to reduce GPU memory pressure. Note: Requires model support for KV-Cache active release interface. Default value: `False`.
- **enable_reload** (bool, optional): Whether to enable message reload interface, automatically reloading offloaded messages. When enabled, the context engine automatically adds reload prompts when getting context windows, and adding the reload tool allows the agent to reload original messages when detailed information is needed. Default value: `False`.

**Example**:

```python
>>> from openjiuwen.core.context_engine import ContextEngineConfig
>>>
>>> config = ContextEngineConfig(
...     max_context_message_num=500,
...     default_window_message_num=100,
...     default_window_round_num=10,
...     enable_kv_cache_release=False,
...     enable_reload=True,
... )
>>> config.default_window_message_num
100
```
