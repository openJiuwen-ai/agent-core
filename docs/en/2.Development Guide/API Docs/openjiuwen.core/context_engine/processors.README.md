# processor

`openjiuwen.core.context_engine.processor` is the core module in openJiuwen for extending and managing context processing logic. Processors can intervene during message writing (add_messages) or window output (get_context_window) stages to control context scale and reduce token consumption.

**Classes**:

| CLASS | DESCRIPTION |
|-------|-------------|
| **ContextProcessor** | Abstract base class for context processors, defining trigger / on hooks and state persistence interfaces. |
| **MessageOffloader** | Message offloader, truncates oversized messages exceeding threshold and offloads them without calling LLM. |
| **MessageOffloaderConfig** | MessageOffloader configuration class. |
| **MessageSummaryOffloader** | Message summary offloader, generates summary via LLM before offloading. |
| **MessageSummaryOffloaderConfig** | MessageSummaryOffloader configuration class. |
| **DialogueCompressor** | Dialogue round compressor, compresses complete tool-call rounds into a single summary. |
| **DialogueCompressorConfig** | DialogueCompressor configuration class. |
| **RoundLevelCompressor** | Round-level compressor, compresses consecutive simple dialogue rounds into two summaries. |
| **RoundLevelCompressorConfig** | RoundLevelCompressor configuration class. |
