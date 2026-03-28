# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Memory configuration for JiuWenClaw.

Configuration is passed through DeepAgentConfig.embedding_config or MemoryRail constructor.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List
from dataclasses import dataclass, field


@dataclass
class MemorySettings:
    """Memory configuration settings.

    Attributes:
        provider: Embedding provider name.
        model: Embedding model name.
        fallback: Fallback provider when embedding fails.
        sources: Memory source directories.
        extra_paths: Extra memory file paths.
        chunking: Chunking configuration.
        query: Query configuration.
        store: Storage configuration.
        sync: Synchronization configuration.
        cache: Cache configuration.
    """
    provider: str = "openai_compatible"
    model: str = "text-embedding-v3"
    fallback: str = "mock"
    sources: List[str] = field(default_factory=lambda: ["memory", "sessions"])
    extra_paths: List[str] = field(default_factory=list)

    chunking: Dict[str, int] = field(default_factory=lambda: {"tokens": 256, "overlap": 32})

    query: Dict[str, Any] = field(default_factory=lambda: {
        "max_results": 10,
        "min_score": 0.3,
        "hybrid": {
            "enabled": True,
            "vectorWeight": 0.7,
            "textWeight": 0.3,
            "candidateMultiplier": 2.0
        }
    })

    store: Dict[str, Any] = field(default_factory=lambda: {
        "path": "memory.db",
        "vector": {"enabled": True},
        "fts": {"enabled": True}
    })

    sync: Dict[str, Any] = field(default_factory=lambda: {
        "watch": True,
        "watchDebounceMs": 2000,
        "onSearch": True,
        "onSessionStart": True,
        "intervalMinutes": 0
    })

    cache: Dict[str, Any] = field(default_factory=lambda: {
        "enabled": True,
        "maxEntries": 10000
    })


def create_memory_settings(
    workspace_dir: str = ".",
    **overrides
) -> MemorySettings:
    """Create MemorySettings instance.

    Args:
        workspace_dir: Workspace directory (not used for config reading anymore).
        **overrides: Override default settings.

    Returns:
        MemorySettings instance.
    """
    settings = MemorySettings()

    for key, value in overrides.items():
        if hasattr(settings, key):
            setattr(settings, key, value)

    return settings


def is_memory_enabled() -> bool:
    """Check if memory is enabled.

    Returns:
        True if memory is enabled (default), False otherwise.
    """
    env_enabled = os.getenv("MEMORY_ENABLED", "true").lower()
    return env_enabled in ("true", "1", "yes")


__all__ = [
    "MemorySettings",
    "create_memory_settings",
    "is_memory_enabled",
]
