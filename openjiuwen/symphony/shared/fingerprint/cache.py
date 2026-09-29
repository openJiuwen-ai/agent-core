# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Private incremental cache for capability fingerprints."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.common.logging import logger
from openjiuwen.symphony.shared.fingerprint._io import atomic_write_json, read_json
from openjiuwen.symphony.shared.fingerprint.normalization import IONameVocabulary

_CACHE_FILENAME = ".fingerprint-cache.json"
_CACHE_SCHEMA_VERSION = "4"


@dataclass(frozen=True)
class FingerprintCacheSnapshot:
    """Validated private cache state for one base configuration signature."""

    entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    io_name_vocabulary: IONameVocabulary = field(default_factory=IONameVocabulary.empty)


class FingerprintCache:
    """Load and publish cache entries bound to a build settings signature."""

    def __init__(self, artifact_root: Path) -> None:
        self._path = artifact_root / _CACHE_FILENAME

    def load(self, settings_signature: str) -> FingerprintCacheSnapshot:
        if not self._path.is_file():
            return FingerprintCacheSnapshot()
        try:
            payload = read_json(self._path)
        except (OSError, json.JSONDecodeError, UnicodeError, BaseError) as exc:
            logger.warning("Ignoring unreadable Symphony fingerprint cache: %s", type(exc).__name__)
            return FingerprintCacheSnapshot()
        if payload.get("schema_version") != _CACHE_SCHEMA_VERSION:
            return FingerprintCacheSnapshot()
        if payload.get("settings_signature") != settings_signature:
            return FingerprintCacheSnapshot()
        entries = payload.get("entries")
        if not isinstance(entries, dict):
            return FingerprintCacheSnapshot()
        raw_vocabulary = payload.get("io_name_vocabulary")
        if not isinstance(raw_vocabulary, dict):
            return FingerprintCacheSnapshot()
        try:
            vocabulary = IONameVocabulary.from_dict(raw_vocabulary)
        except (TypeError, ValueError):
            logger.warning("Ignoring invalid Symphony I/O-name vocabulary cache")
            return FingerprintCacheSnapshot()
        return FingerprintCacheSnapshot(
            entries={
                str(capability_id): entry
                for capability_id, entry in entries.items()
                if isinstance(capability_id, str) and isinstance(entry, dict)
            },
            io_name_vocabulary=vocabulary,
        )

    def publish(
        self,
        settings_signature: str,
        entries: dict[str, dict[str, Any]],
        io_name_vocabulary: IONameVocabulary,
    ) -> None:
        atomic_write_json(
            self._path,
            {
                "schema_version": _CACHE_SCHEMA_VERSION,
                "settings_signature": settings_signature,
                "io_name_vocabulary": io_name_vocabulary.to_dict(),
                "entries": entries,
            },
        )


__all__ = ["FingerprintCache"]
