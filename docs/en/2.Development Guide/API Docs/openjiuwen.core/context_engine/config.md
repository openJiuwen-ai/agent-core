# openjiuwen.core.context_engine.config

## `ContextEngineConfig`

```python
class openjiuwen.core.context_engine.ContextEngineConfig()
```

Complete context-engine configuration.

**Parameters:**

- `max_context_message_num` (`int | None`): Hard message-count limit for one
  context. Default: `None`, meaning unlimited.
- `default_window_message_num` (`int | None`): Number of recent messages kept
  when no explicit window size is supplied. Default: `None`; when set, it must
  be greater than `0`.
- `default_window_round_num` (`int | None`): Number of recent complete dialogue
  rounds kept in the window. When set, round-based truncation takes precedence
  over message-count truncation. Default: `None`.
- `enable_reload` (`bool`): Whether to enable the reload protocol for offloaded
  content. Default: `False`.
- `enable_tiktoken_counter` (`bool`): Whether to use tiktoken for token
  accounting. Default: `False`.
- `context_window_tokens` (`int | None`): Total context-window capacity of the
  runtime model, used by threshold calculations and compression telemetry.
  Default: `None`.
- `model_name` (`str | None`): Model name used by this context. Default: `None`.
- `model_context_window_tokens` (`dict[str, int] | None`): Explicit mapping
  from model names to context-window token capacities. Default: `None`.
- `enable_openrouter_model_context_window_tokens` (`bool`): Whether to fetch
  model-window metadata from OpenRouter. Default: `False`.
- `openrouter_request_timeout` (`float`): OpenRouter metadata request timeout
  in seconds. Default: `3.0`; must be greater than `0`.

Explicit `context_window_tokens` and `model_context_window_tokens` values take
precedence over remote OpenRouter metadata.

**Example:**

```python
from openjiuwen.core.context_engine import ContextEngineConfig

config = ContextEngineConfig(
    default_window_round_num=10,
    context_window_tokens=128_000,
    enable_tiktoken_counter=True,
)
```
