from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bank import ExperienceBank
    from .distiller import TraceDistiller
    from .cluster import cluster_traces, ClusteredQuery
    from .collector import ExperienceBaseBuilder
    from .embed import EmbeddingClient
    from .retriever import ExperienceRetriever

from .evaluator import TraceEvaluator
from .models import TraceRecord, DistilledPattern, ExperienceBankBuildConfig, ExperienceItem
from .trace import (
    list_session_ids,
    parse_all_sessions,
    parse_session,
    parse_and_store,
    load_all_records,
    clear_store,
)

__all__ = [
    "TraceRecord",
    "DistilledPattern",
    "ExperienceBankBuildConfig",
    "ExperienceItem",
    "list_session_ids",
    "parse_session",
    "parse_all_sessions",
    "TraceEvaluator",
    "parse_and_store",
    "load_all_records",
    "clear_store",
    "ExperienceBank",
    "TraceDistiller",
    "cluster_traces",
    "ClusteredQuery",
    "ExperienceBaseBuilder",
    'EmbeddingClient',
    'ExperienceRetriever'
]


def __getattr__(name):
    """Lazy import for modules that depend on optional packages (faiss, etc.).

    Raises ``AttributeError`` (not ``ImportError``) when the underlying
    optional dependency is missing, so callers' ``hasattr`` / ``except
    AttributeError`` checks behave correctly and the error names the missing
    package rather than confusing the user with a raw ModuleNotFoundError.
    """
    lazy = {
        "ExperienceBank": ".bank",
        "TraceDistiller": ".distiller",
        "cluster_traces": ".cluster",
        "ClusteredQuery": ".cluster",
        "ExperienceBaseBuilder": ".collector",
        "EmbeddingClient": ".embed",
        "ExperienceRetriever": ".retriever",
    }
    if name in lazy:
        import importlib
        try:
            mod = importlib.import_module(lazy[name], __package__)
        except ImportError as exc:
            missing = getattr(exc, "name", None) or "an optional dependency"
            raise AttributeError(
                f"cannot import {name!r} from {__name__!r}: "
                f"optional dependency {missing!r} is not installed "
                f"({type(exc).__name__}: {exc}). "
                f"Install the missing package and retry."
            ) from exc
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
