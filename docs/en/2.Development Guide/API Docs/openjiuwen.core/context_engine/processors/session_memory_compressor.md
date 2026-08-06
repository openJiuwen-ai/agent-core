# openjiuwen.core.context_engine.processor.forked.compressor.session_memory_compressor

## class openjiuwen.core.context_engine.processor.forked.compressor.session_memory_compressor.SessionMemoryCompressorConfig

Configuration class for `SessionMemoryCompressor`. This processor materializes session-level durable notes (session memory) as a single `<memory_block_session>` summary message, replacing the prefix of history messages already covered by the notes. **The processor is disabled by default (`enabled=False`)** and must be explicitly enabled via a configuration override.

* **enabled** (bool, optional): Whether to enable this processor. Default value: `False`.
* **trigger_context_ratio** (float, optional): The notes may be materialized into the context only when the context token count reaches this ratio of the context capacity. Range (0, 1). Default value: `0.8`.
* **session_memory_path** (str | None, optional): Custom path of the session notes file; when `None`, the default path `{workspace}/context/{session_id}_context/session_memory/session_context.md` is used. Default value: `None`.
* **max_notes_chars** (int, optional): Maximum number of note characters allowed to be materialized; materialization is skipped when the notes exceed this size. Default value: `120000`.
* **memory** (SessionMemoryConfig, optional): Configuration of the background task that asynchronously maintains the session notes, see `SessionMemoryConfig` below. Default value: `SessionMemoryConfig()`.

**Constraints**: The notes are maintained in a dedicated notes file by the companion background update task; materialization only happens after notes have been generated and the context has reached `trigger_context_ratio`. Materialization replaces only the message prefix already covered by the notes; uncovered messages are kept as-is.

## class openjiuwen.core.context_engine.context.session_memory_manager.SessionMemoryConfig

Configuration class for the background session-notes update task (`SessionMemoryManager`), defined in `openjiuwen.core.context_engine.context.session_memory_manager` and passed as `SessionMemoryCompressorConfig.memory`.

* **update_trigger_context_ratio** (float, optional): Triggers a background notes update when the context token count reaches this ratio of the context capacity. Range (0, 1). Default value: `0.7`.
* **model** (ModelRequestConfig | None, optional): Model request configuration used for notes updates; inherits the agent's model configuration when `None`. Default value: `None`.
* **model_client** (ModelClientConfig | None, optional): Model service configuration used for notes updates; inherits the agent's model service configuration when `None`. Default value: `None`.
* **update_mode** (Literal["agent_edit", "direct_replace"], optional): How the notes are updated. `"agent_edit"` lets a dedicated updater agent edit the notes file via the `edit_file` tool; `"direct_replace"` has the model output the complete new notes content and replaces the file wholesale. Default value: `"agent_edit"`.
* **direct_replace_max_retries** (int, optional): Maximum number of retries on model invocation failure when `update_mode="direct_replace"`. Default value: `2`.
* **enable_debug_dump** (bool, optional): Whether to persist notes-update debug records to disk. Default value: `False`.
* **debug_dump_dir** (str | None, optional): Directory for debug records; uses the default directory when `None`. Default value: `None`.

## class openjiuwen.core.context_engine.processor.forked.compressor.session_memory_compressor.SessionMemoryCompressor

```python
SessionMemoryCompressor(config: SessionMemoryCompressorConfig)
```

`SessionMemoryCompressor` inherits from [ContextProcessor](base.md#class-openjiuwencorecontext_engineprocessorbasecontextprocessor) and intervenes when the context window is being materialized: when `enabled=True`, the context token count has reached `trigger_context_ratio`, and session notes have been generated, it reads the notes file and wraps it as a `<memory_block_session>` message (with a meaning note, a conflict policy, and a coverage marker), replacing the prefix of history messages already covered by the notes. Interface is consistent with the base class, see [ContextProcessor](base.md#class-openjiuwencorecontext_engineprocessorbasecontextprocessor).

**Parameters**:

* **config** (SessionMemoryCompressorConfig): Processor configuration, see above.

**Example**:

This processor depends on the workspace and session state; the recommended way to enable it is to override the same-named configuration in the default processor chain via `ContextProcessorRail`:

```python
from openjiuwen.core.context_engine.processor.forked.compressor.session_memory_compressor import (
    SessionMemoryCompressorConfig,
)
from openjiuwen.core.context_engine.context.session_memory_manager import SessionMemoryConfig
from openjiuwen.harness.rails.context_engineer.context_processor_rail import ContextProcessorRail

rail = ContextProcessorRail(
    preset=True,
    processors=[
        (
            "SessionMemoryCompressor",
            SessionMemoryCompressorConfig(
                enabled=True,
                memory=SessionMemoryConfig(update_trigger_context_ratio=0.7),
            ),
        )
    ],
)
```
