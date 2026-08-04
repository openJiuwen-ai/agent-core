"""Default capability-relation matcher backed by an injected LLM client."""

from __future__ import annotations

from typing import Any, Callable

from openjiuwen.symphony.orchestration.graph.matcher.openai import OpenAICompatibleOntologyMatcher


class LLMRelationMatcher(OpenAICompatibleOntologyMatcher):
    """Public default matcher with source-compatible batching and validation."""

    def __init__(
        self,
        llm_client: Any,
        *,
        min_confidence: float = 0.7,
        batch_size: int = 12,
        max_workers: int = 1,
        require_consensus: bool = True,
        progress: Callable[..., None] | None = None,
    ) -> None:
        super().__init__(
            llm_client,
            batch_size=batch_size,
            max_workers=max_workers,
            require_consensus=require_consensus,
            thresholds={"can_feed": min_confidence},
            progress=progress,
        )
