# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Disk cache for ``CodeGraphIndex`` with atomic replace."""

from __future__ import annotations

import os
import pickle
import tempfile
from pathlib import Path

from openjiuwen.core.common.logging import retrieval_logger as logger
from openjiuwen.core.retrieval.code_graph.models import INDEX_SCHEMA_VERSION, CodeGraphIndex

# Envelope version. Bump together with ``INDEX_SCHEMA_VERSION`` whenever the
# pickled ``CodeGraphIndex`` gains or changes fields.
CACHE_FORMAT_VERSION = 3


class DiskIndexStore:
    """Pickle-based index cache under ``cache_dir``."""

    def __init__(self, cache_dir: str | Path, *, max_size_mb: int = 1024) -> None:
        self.cache_dir = Path(cache_dir)
        self.max_size_bytes = max(1, max_size_mb) * 1024 * 1024

    def load(self, cache_key: str) -> CodeGraphIndex | None:
        path = self._path(cache_key)
        if not path.is_file():
            return None
        try:
            with path.open("rb") as handle:
                payload = pickle.load(handle)
        except (OSError, pickle.PickleError, EOFError) as exc:
            logger.warning("code_graph cache load failed for %s: %s", cache_key, exc)
            return None
        if not isinstance(payload, dict) or payload.get("version") != CACHE_FORMAT_VERSION:
            return None
        if payload.get("schema") != INDEX_SCHEMA_VERSION:
            return None
        index = payload.get("index")
        if not isinstance(index, CodeGraphIndex):
            return None
        if getattr(index, "schema_version", None) != INDEX_SCHEMA_VERSION:
            return None
        return index

    def save(self, cache_key: str, index: CodeGraphIndex) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self._path(cache_key)
        fd, tmp_name = tempfile.mkstemp(prefix=".codegraph-", suffix=".tmp", dir=self.cache_dir)
        try:
            with os.fdopen(fd, "wb") as handle:
                pickle.dump(
                    {
                        "version": CACHE_FORMAT_VERSION,
                        "schema": INDEX_SCHEMA_VERSION,
                        "index": index,
                    },
                    handle,
                    protocol=4,
                )
            os.replace(tmp_name, path)
        except OSError as exc:
            logger.warning("code_graph cache save failed for %s: %s", cache_key, exc)
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            return
        self._enforce_quota()

    def delete(self, cache_key: str) -> None:
        path = self._path(cache_key)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("code_graph cache delete failed for %s: %s", cache_key, exc)

    def _path(self, cache_key: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in cache_key)
        return self.cache_dir / f"{safe}.pkl"

    def _enforce_quota(self) -> None:
        files = sorted(self.cache_dir.glob("*.pkl"), key=lambda p: p.stat().st_mtime if p.exists() else 0)
        total = 0
        sizes: list[tuple[Path, int]] = []
        for path in files:
            try:
                size = path.stat().st_size
            except OSError:
                continue
            sizes.append((path, size))
            total += size
        overflow = total - self.max_size_bytes
        if overflow <= 0:
            return
        for path, size in sizes:
            try:
                path.unlink()
            except OSError:
                continue
            overflow -= size
            if overflow <= 0:
                break
