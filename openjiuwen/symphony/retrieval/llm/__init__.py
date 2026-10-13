from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import (
        CandidateEncodingError,
        CandidateScore,
        CandidateScoringError,
        CandidateScoringResult,
        GenerationConfig,
        GenerationConstraints,
        LLMClientCapabilities,
        LLMClientError,
        LLMRequestError,
        LLMStreamChunk,
        MaxNewTokensTooLarge,
        Message,
        PrefixCacheError,
        PrefixCacheRuntimeOOM,
        PrefixCacheUnavailable,
        ProgressiveLLMClient,
        PromptCacheHint,
        QueryTooLongForPrefixCache,
        TrieConstraint,
        UnsupportedCapability,
        generation_config_to_debug_dict,
    )
    from .config import LLMClientConfig, OpenAIClientConfig, TransformersClientConfig, VLLMClientConfig
    from .factory import coerce_generation_client, create_progressive_client, progressive_client_cache_key
    from .openai_api import OpenAICompatibleClient
    from .transformers_logit_selection import TransformersLogitSelectionClient
    from .transformers_prefix_cached_generation import (
        DistributedGenerationConfig,
        TransformersPrefixCachedGenerationClient,
    )
    from .vllm import LocalVLLMClient, LocalVLLMPrefixCacheHandle

__all__ = [
    "CandidateEncodingError",
    "CandidateScore",
    "CandidateScoringError",
    "CandidateScoringResult",
    "DistributedGenerationConfig",
    "GenerationConfig",
    "GenerationConstraints",
    "LLMClientCapabilities",
    "LLMClientConfig",
    "LLMClientError",
    "LLMRequestError",
    "LLMStreamChunk",
    "LocalVLLMClient",
    "LocalVLLMPrefixCacheHandle",
    "MaxNewTokensTooLarge",
    "Message",
    "OpenAIClientConfig",
    "OpenAICompatibleClient",
    "PrefixCacheError",
    "PrefixCacheRuntimeOOM",
    "PrefixCacheUnavailable",
    "PromptCacheHint",
    "ProgressiveLLMClient",
    "QueryTooLongForPrefixCache",
    "TransformersClientConfig",
    "TransformersLogitSelectionClient",
    "TransformersPrefixCachedGenerationClient",
    "TrieConstraint",
    "UnsupportedCapability",
    "VLLMClientConfig",
    "coerce_generation_client",
    "create_progressive_client",
    "generation_config_to_debug_dict",
    "progressive_client_cache_key",
]


def __getattr__(name: str):
    if name in {
        "CandidateEncodingError",
        "CandidateScore",
        "CandidateScoringError",
        "CandidateScoringResult",
        "GenerationConfig",
        "GenerationConstraints",
        "LLMClientCapabilities",
        "LLMClientError",
        "LLMRequestError",
        "LLMStreamChunk",
        "MaxNewTokensTooLarge",
        "Message",
        "PrefixCacheError",
        "PrefixCacheRuntimeOOM",
        "PrefixCacheUnavailable",
        "PromptCacheHint",
        "ProgressiveLLMClient",
        "QueryTooLongForPrefixCache",
        "TrieConstraint",
        "UnsupportedCapability",
        "generation_config_to_debug_dict",
    }:
        from . import base

        return getattr(base, name)
    if name in {"LLMClientConfig", "OpenAIClientConfig", "TransformersClientConfig", "VLLMClientConfig"}:
        from . import config

        return getattr(config, name)
    if name in {"coerce_generation_client", "create_progressive_client", "progressive_client_cache_key"}:
        from . import factory

        return getattr(factory, name)
    if name == "OpenAICompatibleClient":
        from .openai_api import OpenAICompatibleClient

        return OpenAICompatibleClient
    if name in {"DistributedGenerationConfig", "TransformersPrefixCachedGenerationClient"}:
        from .transformers_prefix_cached_generation import (
            DistributedGenerationConfig,
            TransformersPrefixCachedGenerationClient,
        )

        exports = {
            "DistributedGenerationConfig": DistributedGenerationConfig,
            "TransformersPrefixCachedGenerationClient": TransformersPrefixCachedGenerationClient,
        }
        return exports.get(name)
    if name == "TransformersLogitSelectionClient":
        from .transformers_logit_selection import TransformersLogitSelectionClient

        return TransformersLogitSelectionClient
    if name in {"LocalVLLMClient", "LocalVLLMPrefixCacheHandle"}:
        from .vllm import LocalVLLMClient, LocalVLLMPrefixCacheHandle

        exports = {
            "LocalVLLMClient": LocalVLLMClient,
            "LocalVLLMPrefixCacheHandle": LocalVLLMPrefixCacheHandle,
        }
        return exports.get(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
