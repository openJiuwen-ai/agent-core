# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openjiuwen.core.context_engine.token.tokenizer_spec import TokenizerSpec

DEFAULT_OVERFLOW_TRUNCATE_MARKER = (
    "[CONTEXT_TRUNCATED] To fit the model context window, {rounds} earlier "
    "conversation segment(s) (about {tokens} tokens) were removed. If that "
    "information is still required, ask the user to provide it again."
)


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


class PtlFallbackConfig(BaseModel):
    """Recovery policy for provider prompt-too-long (PTL) rejections.

    The fallback runs in two phases. Phase A reuses the configured LLM
    compressors; Phase B is a pure-code truncation that drops the oldest
    complete dialogue turns without calling a model. Phase B is what makes
    the fallback work when no compressor is configured or when compression is
    too slow/ineffective.

    Attributes
    ----------
    enabled : bool, default False
        Master switch. When off, a PTL rejection keeps the historical
        behaviour: one compression attempt, otherwise the original model
        exception is raised unchanged.
    phase_a_compress : bool, default True
        Run the LLM compressors first. Phase B runs only when Phase A does
        not change the context.
    phase_a_timeout_s : float, default 0
        Upper bound for one Phase A compression. ``0`` disables the timeout.
        On timeout the in-flight compression is cancelled and the fallback
        proceeds to Phase B; compression state may report that call as
        interrupted.
    enable_pure_code_truncate : bool, default True
        Enable Phase B. Dropping is per complete dialogue turn, oldest
        first; the newest ``keep_last_rounds`` turns are always preserved.
        When even one turn does not fit, the oldest complete tool exchange
        inside the retained turn is removed instead.
    max_retries : int, default 3
        Maximum recovery retries per agent round (1..10). Each retry repeats
        the model call after the recovery hook changes the context.
    truncate_target_ratio : float, default 0.8
        Phase B aims at ``limit * ratio ** attempt`` so that repeated
        failures keep making progress.
    keep_last_rounds : int, default 1
        Number of newest dialogue turns Phase B never drops.
    archive_dropped_rounds : bool, default True
        Best-effort archive of dropped messages before they are discarded.
        Archiving never blocks the truncation.
    marker_template : str
        Text of the marker injected where the dropped history used to be.
        Only ``{rounds}`` and ``{tokens}`` placeholders are supported.

    Notes
    -----
    ``ContextEngineConfig.ptl_fallback_config`` has two "off" states:

    * ``None``: the capability is absent (for example, the removable
      ``recovery`` package was deleted). The legacy path is used without
      importing it.
    * a present config with ``enabled=False``: the capability is installed but
      explicitly disabled. Behaviour is identical to ``None``.

    Use :meth:`is_active` when a boolean "should this run" answer is needed.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False)
    phase_a_compress: bool = Field(default=True)
    phase_a_timeout_s: float = Field(default=0.0, ge=0)
    enable_pure_code_truncate: bool = Field(default=True)
    max_retries: int = Field(default=3, ge=1, le=10)
    truncate_target_ratio: float = Field(default=0.8, gt=0, le=1)
    keep_last_rounds: int = Field(default=1, ge=1)
    archive_dropped_rounds: bool = Field(default=True)
    marker_template: str = Field(default=DEFAULT_OVERFLOW_TRUNCATE_MARKER)

    def is_active(self) -> bool:
        """Return whether the fallback should run (installed and enabled)."""
        return bool(self.enabled)

    @model_validator(mode="after")
    def _validate_policy(self) -> "PtlFallbackConfig":
        try:
            self.marker_template.format(rounds=0, tokens=0)
        except (KeyError, IndexError, ValueError) as exc:
            raise ValueError("marker_template must only use the {rounds} and {tokens} placeholders") from exc
        if self.enabled and not self.phase_a_compress and not self.enable_pure_code_truncate:
            raise ValueError("ptl_fallback_config requires phase_a_compress or enable_pure_code_truncate")
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
        Enable application-level tokenizer warm-up. When a configured model has
        a native tokenizer spec, warm-up downloads its artifact and at most one
        configured family fallback. With the switch off, no warm-up/download
        occurs, but a tokenizer already present in the local cache can still be
        used. Context creation never downloads a tokenizer.

    context_window_tokens : int, optional
        Total context window supported by the runtime model, including input and
        output tokens. Used for context compression telemetry only.

    model_name : str, optional
        Name of the LLM used by this context. Used to look up the default
        context window size from ``model_context_window_tokens``.

    model_provider : str, optional
        Provider name used by tokenizer selection.

    tokenizer_spec : TokenizerSpec, optional
        Exact tokenizer artifact and explicitly declared same-family fallbacks.

    tokenizer_registry : list[TokenizerSpec]
        Additional exact model entries used by ``TokenizerSelector``.

    tokenizer_cache_dir : str, optional
        Backend cache directory for downloaded tokenizer artifacts.

    enable_tokenizer_download : bool, default False
        Compatibility field for application-level tokenizer warm-up. Context
        initialization is always local-only and never downloads a missing
        tokenizer artifact.

    tokenizer_offline : bool, default False
        Compatibility field for application-level tokenizer warm-up. Context
        initialization is always forced to local cache/files only.

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
        Unified debug toggle for context processing and outbound model calls.
        When on, processors persist JSONL records at each pipeline stage
        (threshold checks, span splits, compression retries, before/after
        diffs) and the ReAct agent persists the exact final messages/tools
        payload sent to the model in ``llm_request.jsonl`` under
        ``context_debug_dir``. This can contain user data and system prompts;
        keep it disabled outside debugging. Zero overhead when off.

    context_debug_dir : str, optional
        Directory for context-debug records. When None, falls back to the
        ``OPENJIUWEN_CONTEXT_DEBUG_DIR`` env var, then to
        ``{workspace}/context/{session_id}_context/context_debug/``.

    ptl_fallback_config : PtlFallbackConfig, optional
        Recovery policy for model prompt-too-long rejections. ``None`` means
        the capability is absent and the legacy single-compression path is
        used. See :class:`PtlFallbackConfig`.
    """

    max_context_message_num: Optional[int] = Field(default=None, gt=0)
    default_window_message_num: Optional[int] = Field(default=None, gt=0)
    default_window_round_num: Optional[int] = Field(default=None, gt=0)
    enable_reload: bool = Field(default=False)
    enable_tiktoken_counter: bool = Field(default=False)
    context_window_tokens: Optional[int] = Field(default=None, gt=0)
    model_name: Optional[str] = Field(default=None)
    model_provider: Optional[str] = Field(default=None)
    model_context_window_tokens: Optional[Dict[str, int]] = Field(default=None)
    tokenizer_spec: Optional[TokenizerSpec] = Field(default=None)
    tokenizer_registry: list[TokenizerSpec] = Field(default_factory=list)
    tokenizer_cache_dir: Optional[str] = Field(default=None)
    enable_tokenizer_download: bool = Field(default=False)
    tokenizer_offline: bool = Field(default=False)
    model_context_window_tokens_override: Optional[int] = Field(default=None)
    enable_openrouter_model_context_window_tokens: bool = Field(default=False)
    openrouter_request_timeout: float = Field(default=3.0, gt=0)
    compression_recall_config: CompressionRecallConfig = Field(
        default_factory=CompressionRecallConfig,
    )
    enable_context_debug: bool = Field(default=False)
    context_debug_dir: Optional[str] = Field(default=None)
    ptl_fallback_config: Optional[PtlFallbackConfig] = Field(default=None)
