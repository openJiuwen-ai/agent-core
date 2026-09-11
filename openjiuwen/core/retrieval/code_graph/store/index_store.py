# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Disk cache for ``CodeGraphIndex`` with atomic replace.

Cache keys may be nested (``<repo-id>/<token>-<config>``) so two clones that
share a basename do not collide. Quota walks the whole tree.
"""

from __future__ import annotations

import json
import os
import pickle
import shutil
import tempfile
import time
from pathlib import Path

from openjiuwen.core.common.logging import retrieval_logger as logger
from openjiuwen.core.retrieval.code_graph.models import INDEX_SCHEMA_VERSION, CodeGraphIndex

# Envelope version. Bump together with ``INDEX_SCHEMA_VERSION`` whenever the
# pickled ``CodeGraphIndex`` gains or changes fields.
CACHE_FORMAT_VERSION = 4
ACTIVE_MANIFEST = "active.json"
BUILD_LOCK_NAME = "build.lock"


class DiskIndexStore:
    """Pickle-based index cache under ``cache_dir``."""

    def __init__(self, cache_dir: str | Path, *, max_size_mb: int = 1024) -> None:
        # Always absolute so a relative yaml value does not follow process cwd
        # when the same config is reused on another machine.
        self.cache_dir = Path(cache_dir).expanduser().resolve()
        self.max_size_bytes = max(1, max_size_mb) * 1024 * 1024

    def load(self, cache_key: str) -> CodeGraphIndex | None:
        path = self._path(cache_key)
        if not path.is_file():
            return None
        return self._load_path(path)

    def used_bytes(self) -> int:
        """Measured size of every file currently under the cache directory."""
        if not self.cache_dir.is_dir():
            return 0
        total = 0
        for path in self.cache_dir.rglob("*"):
            if not path.is_file():
                continue
            try:
                total += path.stat().st_size
            except OSError:
                continue
        return total

    def save(
        self,
        cache_key: str,
        index: CodeGraphIndex,
        *,
        last_full_build_seconds: float | None = None,
    ) -> None:
        path = self._path(cache_key)
        self._atomic_pickle(path, index)
        self._write_active_manifest(
            path,
            cache_key,
            index,
            last_full_build_seconds=last_full_build_seconds,
        )
        self._drop_superseded(path)
        self._enforce_quota()
        used = self.used_bytes()
        if used > self.max_size_bytes:
            self.delete(cache_key)
            from openjiuwen.core.retrieval.code_graph.budgets import raise_limit_exceeded

            raise_limit_exceeded("max_cache_size_mb", used, self.max_size_bytes)

    def load_active(self, repo_id: str, config_hash: str) -> CodeGraphIndex | None:
        """Load the last published checkpoint for ``repo_id`` if config matches."""
        manifest = self._read_manifest(repo_id)
        if manifest is None:
            return None
        if manifest.get("config_hash") != config_hash:
            return None
        if manifest.get("schema") != INDEX_SCHEMA_VERSION:
            return None
        relative = str(manifest.get("index_key") or "")
        if not relative:
            return None
        return self.load(relative)

    def load_full_build_seconds(self, repo_id: str) -> float | None:
        """Last measured full rebuild, if the active manifest recorded one."""
        manifest = self._read_manifest(repo_id)
        if manifest is None:
            return None
        raw = manifest.get("last_full_build_seconds")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    def delete(self, cache_key: str) -> None:
        path = self._path(cache_key)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("code_graph cache delete failed for %s: %s", cache_key, exc)

    def delete_repo(self, repo_id: str) -> None:
        """Remove every checkpoint for one workspace, including active.json."""
        directory = self.cache_dir / self._safe_part(repo_id)
        if not directory.is_dir():
            return
        try:
            shutil.rmtree(directory)
        except OSError as exc:
            logger.warning("code_graph cache drop failed for %s: %s", repo_id, exc)

    def enforce_quota(self) -> None:
        """Delete non-active pickles until the cache directory is under quota."""
        self._enforce_quota()

    def cleanup_orphans(self) -> None:
        """Remove leftover temp files from interrupted writes."""
        if not self.cache_dir.is_dir():
            return
        for path in self.cache_dir.rglob(".codegraph-*.tmp"):
            try:
                path.unlink()
            except OSError:
                continue

    def purge_expired(self, ttl_days: int) -> None:
        """Drop pickle checkpoints older than ``ttl_days``. Active files stay."""
        if ttl_days <= 0 or not self.cache_dir.is_dir():
            return
        cutoff = time.time() - float(ttl_days) * 86400.0
        protected = self._active_index_paths()
        for path in self.cache_dir.rglob("*.pkl"):
            if path in protected:
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue

    def build_lock_path(self, repo_id: str) -> Path:
        directory = self.cache_dir / self._safe_part(repo_id)
        directory.mkdir(parents=True, exist_ok=True)
        return directory / BUILD_LOCK_NAME

    def _path(self, cache_key: str) -> Path:
        parts = [self._safe_part(part) for part in str(cache_key).replace("\\", "/").split("/") if part]
        if not parts:
            parts = ["index"]
        return self.cache_dir.joinpath(*parts).with_suffix(".pkl")

    @staticmethod
    def _load_path(path: Path) -> CodeGraphIndex | None:
        try:
            with path.open("rb") as handle:
                payload = pickle.load(handle)
        except (OSError, pickle.PickleError, EOFError) as exc:
            logger.warning("code_graph cache load failed for %s: %s", path, exc)
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

    @staticmethod
    def _atomic_pickle(path: Path, index: CodeGraphIndex) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".codegraph-", suffix=".tmp", dir=path.parent)
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
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        except OSError as exc:
            logger.warning("code_graph cache save failed for %s: %s", path, exc)
            try:
                os.unlink(tmp_name)
            except OSError:
                pass

    def _write_active_manifest(
        self,
        index_path: Path,
        cache_key: str,
        index: CodeGraphIndex,
        *,
        last_full_build_seconds: float | None = None,
    ) -> None:
        try:
            relative = str(index_path.relative_to(self.cache_dir).with_suffix("")).replace("\\", "/")
        except ValueError:
            relative = cache_key
        payload: dict[str, object] = {
            "index_key": relative,
            "config_hash": index.config_hash,
            "schema": INDEX_SCHEMA_VERSION,
            "snapshot": index.snapshot,
            "updated_at": time.time(),
        }
        if last_full_build_seconds is not None and last_full_build_seconds > 0:
            payload["last_full_build_seconds"] = round(float(last_full_build_seconds), 4)
        manifest_path = index_path.parent / ACTIVE_MANIFEST
        fd, tmp_name = tempfile.mkstemp(prefix=".codegraph-manifest-", suffix=".tmp", dir=index_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, manifest_path)
        except OSError as exc:
            logger.warning("code_graph manifest save failed for %s: %s", manifest_path, exc)
            try:
                os.unlink(tmp_name)
            except OSError:
                pass

    def _read_manifest(self, repo_id: str) -> dict[str, object] | None:
        path = self.cache_dir / self._safe_part(repo_id) / ACTIVE_MANIFEST
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("code_graph manifest load failed for %s: %s", path, exc)
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _drop_superseded(keep: Path) -> None:
        """Remove older pickles in the same repo folder after the pointer moved."""
        parent = keep.parent
        if not parent.is_dir():
            return
        keep_resolved = keep.resolve()
        for path in parent.glob("*.pkl"):
            try:
                if path.resolve() == keep_resolved:
                    continue
                path.unlink()
            except OSError as exc:
                logger.warning("code_graph superseded cache delete failed for %s: %s", path, exc)

    def _active_index_paths(self) -> set[Path]:
        protected: set[Path] = set()
        if not self.cache_dir.is_dir():
            return protected
        for manifest in self.cache_dir.rglob(ACTIVE_MANIFEST):
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            key = str(payload.get("index_key") or "")
            if key:
                protected.add(self._path(key))
        return protected

    def _enforce_quota(self) -> None:
        if not self.cache_dir.is_dir():
            return
        protected = self._active_index_paths()
        files = sorted(
            (path for path in self.cache_dir.rglob("*.pkl") if path.is_file()),
            key=lambda item: item.stat().st_mtime if item.exists() else 0,
        )
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
            if path in protected:
                continue
            try:
                path.unlink()
            except OSError:
                continue
            overflow -= size
            if overflow <= 0:
                break

    @staticmethod
    def safe_part(part: str) -> str:
        return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in part) or "index"

    _safe_part = safe_part
