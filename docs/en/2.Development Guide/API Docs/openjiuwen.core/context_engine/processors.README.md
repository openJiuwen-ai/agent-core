# processor

`openjiuwen.core.context_engine.processor` is the core module in openJiuwen for extending and managing context processing logic. Processors can intervene during message writing (add_messages) or window output (get_context_window) stages to control context scale and reduce token consumption.

**Classes**:

| CLASS | DESCRIPTION |
|-------|-------------|
| **ContextProcessor** | Abstract base class for context processors, defining trigger / on hooks and state persistence interfaces. |
| **MessageOffloader** | Message offloader, truncates oversized messages exceeding threshold and offloads them without calling LLM. |
| **MessageOffloaderConfig** | MessageOffloader configuration class. |
| **MessageSummaryOffloader** | Message offloader, rule-compresses oversized tool messages and offloads them without calling an LLM. |
| **MessageSummaryOffloaderConfig** | MessageSummaryOffloader configuration class. |
| **DialogueCompressor** | Dialogue compressor, compresses history messages before the current round into a single summary. |
| **DialogueCompressorConfig** | DialogueCompressor configuration class. |
| **CurrentRoundCompressor** | Current-round compressor that summarizes work completed after the latest user request. |
| **CurrentRoundCompressorConfig** | CurrentRoundCompressor configuration class. |
| **RoundLevelCompressor** | Round-level compressor, keeps only the most recent messages and compresses earlier history into a single cross-round checkpoint summary. |
| **RoundLevelCompressorConfig** | RoundLevelCompressor configuration class. |
| **SessionMemoryCompressor** | Materializes asynchronously maintained session notes as a model-context block. Disabled by default. |
| **SessionMemoryCompressorConfig** | SessionMemoryCompressor configuration class. |
| **ReasoningToolLoopCompactProcessor** | Detects and folds consecutive duplicated reasoning/tool-call rounds to break model loops. |
| **ReasoningToolLoopCompactProcessorConfig** | ReasoningToolLoopCompactProcessor configuration class. |

## Default processor chain

The Harness assembles the default context processor chain through
`ContextProcessorRail(preset=True)`. The chain runs `MessageSummaryOffloader`,
`SessionMemoryCompressor` (disabled by default),
`ReasoningToolLoopCompactProcessor`, `DialogueCompressor`,
`CurrentRoundCompressor`, and `RoundLevelCompressor` in a fixed order. See
[Context Engine](../../Advanced%20Usage/Context%20Engine.md) for the chain
order and enablement examples.
