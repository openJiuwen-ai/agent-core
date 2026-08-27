# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Dict, Optional

from pydantic import BaseModel, Field, model_validator


class CompressionRecallConfig(BaseModel):
    """Global configuration for archiving and recalling compressed context."""

    enabled: bool = Field(default=False)
    chunk_size_tokens: int = Field(default=3000, gt=0)
    chunk_overlap_tokens: int = Field(default=300, gt=0)

    @model_validator(mode="after")
    def _validate_chunk_tokens(self) -> "CompressionRecallConfig":
        if self.chunk_overlap_tokens >= self.chunk_size_tokens:
            raise ValueError("chunk_overlap_tokens must be smaller than chunk_size_tokens")
        return self


class ContextEngineConfig(BaseModel):
    """
    Configuration for the context engine.

    Attributes
    ----------
    max_context_message_num : int, optional
        Hard upper limit on the total number of messages allowed in any context.
        If None (default), no hard limit is enforced.

    default_window_message_num : int, optional
        Number of most-recent messages to retain when a sliding window is created
        without an explicit token or message count.  Must be > 0 if set; None
        (default) means "unlimited".

    default_window_round_num : int, optional
        Number of most-recent conversation rounds to retain when creating a
        sliding window. A round is defined as starting from a user message and
        ending at the next assistant message that contains no tool calls (i.e.,
        the final response in a tool-using turn). This spans across multiple
        messages if the assistant performs tool calls, but counts as one logical
        round. Useful for maintaining dialog coherence without specifying exact
        message counts. If set, takes precedence over `default_window_message_num`
        for round-based truncation. Must be > 0 if set; None (default) disables
        round-based windowing.

    enable_reload : bool, default False
        Whether to enable automatic reloading of offloaded messages when the
        model requests them via reload hints (e.g., [[HANDLE:xxx]]). When enabled,
        the context engine monitors model outputs for reload signals and
        transparently fetches the full content from storage, injecting it back
        into the active context. When disabled, hints remain as-is in the
        conversation, and offloaded content is never automatically restored.

    enable_tiktoken_counter : bool, default False
        Whether to create a default TiktokenCounter when ``create_context`` is
        called without an explicit token counter. When disabled, context token
        budgets use the built-in character-based estimation fallbacks.

    context_window_tokens : int, optional
        Total context window supported by the runtime model, including input and
        output tokens. Used for context compression telemetry only.

    model_name : str, optional
        Name of the LLM used by this context. Used to look up the default
        context window size from ``model_context_window_tokens``.

    model_context_window_tokens : dict[str, int], optional
        Best-effort fallback mapping from model name to total context window
        tokens. Explicit runtime values and context_window_tokens take priority.

    model_context_window_tokens_override : int, optional
        Context window supplied by the currently selected model configuration.
        It is lower priority than the global ``context_window_tokens`` value and
        higher priority than the model-name mapping and automatic lookups.

    enable_openrouter_model_context_window_tokens : bool, default False
        Whether to fetch model context windows from OpenRouter when creating a
        context. Results are cached process-wide and explicit
        model_context_window_tokens values take priority.

    openrouter_request_timeout : float, default 3.0
        Timeout in seconds for fetching OpenRouter model metadata.

    compression_recall_config : CompressionRecallConfig
        Context-wide policy for archiving source messages replaced by
        compression and making those archives available for recall.

    enable_context_debug : bool, default False
        Unified debug toggle for the forked context processors. When on,
        processors persist JSONL records at each pipeline stage (threshold
        checks, span splits, compression retries, before/after diffs) to
        ``context_debug_dir`` so developers can quickly locate context
        compression issues. Zero overhead when off.

    context_debug_dir : str, optional
        Directory for context-debug records. When None, falls back to the
        ``OPENJIUWEN_CONTEXT_DEBUG_DIR`` env var, then to
        ``{workspace}/context/{session_id}_context/context_debug/``.
    """

    max_context_message_num: Optional[int] = Field(default=None, gt=0)
    default_window_message_num: Optional[int] = Field(default=None, gt=0)
    default_window_round_num: Optional[int] = Field(default=None, gt=0)
    enable_reload: bool = Field(default=False)
    enable_tiktoken_counter: bool = Field(default=False)
    context_window_tokens: Optional[int] = Field(default=None, gt=0)
    model_name: Optional[str] = Field(default=None)
    model_context_window_tokens: Optional[Dict[str, int]] = Field(default=None)
    model_context_window_tokens_override: Optional[int] = Field(default=None)
    enable_openrouter_model_context_window_tokens: bool = Field(default=False)
    openrouter_request_timeout: float = Field(default=3.0, gt=0)
    compression_recall_config: CompressionRecallConfig = Field(
        default_factory=CompressionRecallConfig,
    )
    enable_context_debug: bool = Field(default=False)
    context_debug_dir: Optional[str] = Field(default=None)
