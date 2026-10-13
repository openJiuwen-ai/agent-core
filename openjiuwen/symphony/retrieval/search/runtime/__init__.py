from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openjiuwen.symphony.retrieval.common.models import RetrieverCandidate as RetrieverCandidate
    from openjiuwen.symphony.retrieval.common.models import RetrieverChoice as RetrieverChoice
    from openjiuwen.symphony.retrieval.common.models import RetrieverItem as RetrieverItem
    from openjiuwen.symphony.retrieval.common.models import RetrieverNode as RetrieverNode
    from openjiuwen.symphony.retrieval.common.models import RetrieverTrace as RetrieverTrace
    from openjiuwen.symphony.retrieval.common.models import RetrieverTraceEvent as RetrieverTraceEvent

    from .flat import FlatRetriever as FlatRetriever
    from .progressive import ProgressiveRetriever as ProgressiveRetriever
    from .types import ProgressiveRetrieverConfig as ProgressiveRetrieverConfig
    from .types import ProgressiveRetrieverResult as ProgressiveRetrieverResult

__all__ = [
    "FlatRetriever",
    "ProgressiveRetriever",
    "ProgressiveRetrieverConfig",
    "ProgressiveRetrieverResult",
    "RetrieverCandidate",
    "RetrieverChoice",
    "RetrieverItem",
    "RetrieverNode",
    "RetrieverTrace",
    "RetrieverTraceEvent",
]


def __getattr__(name: str):
    if name in {
        "RetrieverCandidate",
        "RetrieverChoice",
        "RetrieverItem",
        "RetrieverNode",
        "RetrieverTrace",
        "RetrieverTraceEvent",
    }:
        from openjiuwen.symphony.retrieval.common.models import (
            RetrieverCandidate,
            RetrieverChoice,
            RetrieverItem,
            RetrieverNode,
            RetrieverTrace,
            RetrieverTraceEvent,
        )

        exports = {
            "RetrieverCandidate": RetrieverCandidate,
            "RetrieverChoice": RetrieverChoice,
            "RetrieverItem": RetrieverItem,
            "RetrieverNode": RetrieverNode,
            "RetrieverTrace": RetrieverTrace,
            "RetrieverTraceEvent": RetrieverTraceEvent,
        }
        return exports.get(name)
    if name == "FlatRetriever":
        from .flat import FlatRetriever

        return FlatRetriever
    if name == "ProgressiveRetriever":
        from .progressive import ProgressiveRetriever

        return ProgressiveRetriever
    if name in {"ProgressiveRetrieverConfig", "ProgressiveRetrieverResult"}:
        from .types import ProgressiveRetrieverConfig, ProgressiveRetrieverResult

        exports = {
            "ProgressiveRetrieverConfig": ProgressiveRetrieverConfig,
            "ProgressiveRetrieverResult": ProgressiveRetrieverResult,
        }
        return exports.get(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
