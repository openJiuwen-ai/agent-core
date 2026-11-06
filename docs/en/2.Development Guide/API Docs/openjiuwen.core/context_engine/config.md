# openjiuwen.core.context_engine.config

## `CompressionRecallConfig`

```python
class openjiuwen.core.context_engine.CompressionRecallConfig(
    enabled: bool = False,
    chunk_size_tokens: int = 3000,
    chunk_overlap_tokens: int = 300,
)
```

ContextEngine-wide configuration for archiving and recalling compressed source
messages. When enabled, compressors archive the source messages they replace,
and the Harness registers the `recall_compressed_context` tool so the model can
retrieve compressed content on demand.

**Parameters:**

- `enabled`: Whether to archive replaced source messages and enable recall.
  Default: `False`.
- `chunk_size_tokens`: Target token count of an archive retrieval chunk. Must
  be greater than `0`. Default: `3000`.
- `chunk_overlap_tokens`: Token overlap between adjacent chunks. Must be
  greater than `0` and smaller than `chunk_size_tokens`. Default: `300`.

## `PtlFallbackConfig`

```python
class openjiuwen.core.context_engine.PtlFallbackConfig(
    enabled: bool = False,
    phase_a_compress: bool = True,
    phase_a_timeout_s: float = 0.0,
    enable_pure_code_truncate: bool = True,
    max_retries: int = 3,
    truncate_target_ratio: float = 0.8,
    keep_last_rounds: int = 1,
    archive_dropped_rounds: bool = True,
    marker_template: str = "...",
)
```

Recovery policy for provider prompt-too-long (PTL / context-overflow) errors.

Recovery runs in two phases. Phase A reuses the configured LLM compressors.
Phase B is a pure-code truncation that never calls a model: it drops complete
dialogue turns (user message through the final assistant reply) from oldest to
newest, always keeping the newest `keep_last_rounds` turns. When a single turn
still does not fit, the oldest complete tool exchange inside it (an assistant
`tool_calls` message plus its matching `tool` results) is removed instead, so
tool-call pairing stays valid. A `[CONTEXT_TRUNCATED]` marker is inserted where
the history was cut off.

`enabled` defaults to `False`: when off, the historical behaviour is preserved
(one compression attempt, otherwise the original model exception is raised).

> `ptl_fallback_config: null` (not configured) and `enabled: false` both take
> the legacy path: the former means the capability is absent or uninstalled,
> the latter means it is installed but explicitly disabled. Use
> `ContextEngine.ptl_fallback_state()` to distinguish `absent`, `disabled`, and
> `enabled`.

When the provider's reported limit is not above the fixed system/tool overhead,
Phase B refuses to truncate and preserves the original error instead of dropping
history with no chance of fitting.

**Parameters:**

- `enabled`: Master switch. Default: `False`.
- `phase_a_compress`: Whether to try LLM compression first. Default: `True`.
- `phase_a_timeout_s`: Timeout in seconds for one Phase A compression; `0`
  disables it. On timeout the fallback proceeds to Phase B.
- `enable_pure_code_truncate`: Enable Phase B. Default: `True`.
- `max_retries`: Recovery retries per agent round, `1..10`. Default: `3`.
- `truncate_target_ratio`: Phase B targets `limit * ratio ** attempt`, so
  repeated failures keep making progress. Default: `0.8`, range `(0, 1]`.
- `keep_last_rounds`: Number of newest dialogue turns never dropped. Default: `1`.
- `archive_dropped_rounds`: Best-effort archive of dropped messages before they
  are discarded; archiving never blocks truncation. Default: `True`.
- `marker_template`: Marker text. Only `{rounds}` and `{tokens}` placeholders
  are supported.

## `ContextEngineConfig`

```python
class openjiuwen.core.context_engine.ContextEngineConfig()
```

Complete context-engine configuration.

**Parameters:**

- `max_context_message_num` (`int | None`): Hard message-count limit for one
  context. Default: `None`, meaning unlimited.
- `default_window_message_num` (`int | None`): Number of recent messages kept
  when no explicit window size is supplied. Default: `None`, meaning no
  message-count truncation; when set, it must be greater than `0`.
- `default_window_round_num` (`int | None`): Number of recent complete dialogue
  rounds kept in the window. When set, round-based truncation is applied before
  message-count truncation. Default: `None`, meaning no round-based truncation.

When both are `None`, `get_context_window()` performs no window truncation and
returns the full conversation history (subject only to the
`max_context_message_num` buffer limit and to offload/compression by context
processors).
- `enable_reload` (`bool`): Whether to enable the reload protocol for offloaded
  content. Default: `False`.
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
- `compression_recall_config` (`CompressionRecallConfig`): Compressed-source
  archive and recall policy. Defaults to a disabled `CompressionRecallConfig`.
- `enable_context_debug` (`bool`): Unified debug toggle for context processors.
  When on, processors persist JSONL records at each pipeline stage (threshold
  checks, span splits, compression retries, before/after diffs). Default:
  `False`; zero overhead when off.
- `context_debug_dir` (`str | None`): Directory for context-debug records. When
  `None`, falls back to the `OPENJIUWEN_CONTEXT_DEBUG_DIR` env var, then to
  `{workspace}/context/{session_id}_context/context_debug/`. Default: `None`.
- `ptl_fallback_config` (`PtlFallbackConfig`): Recovery policy for provider
  context-overflow errors. Defaults to a disabled `PtlFallbackConfig`.

Explicit `context_window_tokens` and `model_context_window_tokens` values take
precedence over remote OpenRouter metadata.

**Example:**

```python
from openjiuwen.core.context_engine import (
    CompressionRecallConfig,
    ContextEngineConfig,
)

config = ContextEngineConfig(
    default_window_round_num=10,
    context_window_tokens=128_000,
    compression_recall_config=CompressionRecallConfig(
        enabled=True,
        chunk_size_tokens=3000,
        chunk_overlap_tokens=300,
    ),
)
```

> **Note**: KV-cache affinity is not part of `ContextEngineConfig`. Enable it
> through the agent-side configuration:
> `ReActAgentConfig.configure_kv_cache_affinity(enable_kv_cache_affinity=True)`.
