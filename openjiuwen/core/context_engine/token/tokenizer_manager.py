# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Resolve, download, validate, and cache native tokenizer artifacts."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from openjiuwen.core.context_engine.token.tokenizer_spec import (
    CompatibleTokenizerSpec,
    TokenizerSpec,
)


_HUGGINGFACE_ENDPOINT = "https://hf-mirror.com"
_HUGGINGFACE_TIMEOUT = 10.0


class TokenizerArtifactManager:
    """Load tokenizer artifacts without making model calls depend on a network.

    Local files are always preferred. Remote resolution is restricted to the
    source values represented by :class:`TokenizerSpec`; arbitrary URLs are not
    accepted. Downloads are disabled by default and can be enabled explicitly
    by the application when a model profile is saved or a session is warmed.
    """

    def __init__(
        self,
        *,
        cache_dir: str | Path | None = None,
        enable_download: bool = False,
        offline: bool = False,
        endpoint: str | None = None,
        request_timeout: float = _HUGGINGFACE_TIMEOUT,
    ) -> None:
        configured_cache_dir = cache_dir or os.getenv(
            "OPENJIUWEN_TOKENIZER_CACHE_DIR",
            "~/.cache/openjiuwen/tokenizers",
        )
        self.cache_dir = Path(configured_cache_dir).expanduser()
        self.enable_download = enable_download
        self.offline = offline
        configured_endpoint = endpoint if endpoint is not None else os.getenv("HF_ENDPOINT")
        self.endpoint = str(configured_endpoint or _HUGGINGFACE_ENDPOINT).strip().rstrip("/")
        if request_timeout <= 0:
            raise ValueError("request_timeout must be greater than zero")
        self.request_timeout = request_timeout
        self.last_error: str | None = None

    def resolve(self, spec: TokenizerSpec | CompatibleTokenizerSpec) -> Path | None:
        """Return a validated local tokenizer artifact path for ``spec``.

        A missing or invalid artifact returns ``None`` and records a compact
        reason. The artifact is ``tokenizer.json`` for the tokenizers engine
        and ``tiktoken.model`` for the tiktoken engine. Callers can then
        continue with the configured one-hop fallback.
        """
        self.last_error = None
        try:
            local_path = self._local_path(spec)
            if local_path is not None:
                return self._validate(local_path, spec)

            source = str(getattr(spec, "source", "") or "").strip().casefold()
            tokenizer_id = str(getattr(spec, "tokenizer_id", "") or "").strip()
            if not tokenizer_id:
                self.last_error = "tokenizer_id_missing"
                return None
            if "://" in tokenizer_id:
                self.last_error = "remote_url_not_allowed"
                return None

            if source == "huggingface":
                return self._resolve_huggingface(spec)
            if source == "modelscope":
                return self._resolve_modelscope(spec)

            if source == "provider_official":
                # Provider official endpoints need a provider-specific adapter and
                # must not be treated as arbitrary URLs submitted by a client.
                self.last_error = "provider_official_adapter_not_configured"
            else:
                self.last_error = "local_tokenizer_artifact_missing"
            return None
        except Exception as exc:
            # The selector owns the fallback policy.  Never let filesystem,
            # checksum, or optional backend errors escape this resolver.
            self.last_error = self._error_reason(exc)
            return None

    def _local_path(self, spec: TokenizerSpec | CompatibleTokenizerSpec) -> Path | None:
        raw_path = getattr(spec, "artifact_path", None)
        if raw_path:
            path = Path(raw_path).expanduser()
            return self._artifact_file(path, spec)

        tokenizer_id = getattr(spec, "tokenizer_id", None)
        if tokenizer_id:
            candidate = Path(tokenizer_id).expanduser()
            return self._artifact_file(candidate, spec) if candidate.exists() else None
        return None

    @classmethod
    def _artifact_file(
        cls,
        path: Path,
        spec: TokenizerSpec | CompatibleTokenizerSpec,
    ) -> Path | None:
        if path.is_file():
            return path
        if path.is_dir():
            candidate = path / cls._artifact_name(spec)
            return candidate if candidate.is_file() else None
        return None

    @staticmethod
    def _artifact_name(spec: TokenizerSpec | CompatibleTokenizerSpec) -> str:
        engine = str(getattr(spec, "engine", "auto") or "auto").strip().casefold()
        return "tiktoken.model" if engine == "tiktoken" else "tokenizer.json"

    @classmethod
    def _allow_patterns(cls, spec: TokenizerSpec | CompatibleTokenizerSpec) -> list[str]:
        if cls._artifact_name(spec) == "tiktoken.model":
            # Kimi's tiktoken vocabulary needs the repository's special-token
            # IDs. Keep this metadata beside the vocabulary when available;
            # the counter also has a reserved-token fallback for local files
            # that contain only tiktoken.model.
            return ["tiktoken.model", "tokenizer_config.json"]
        return ["tokenizer.json"]

    def _resolve_huggingface(self, spec: TokenizerSpec | CompatibleTokenizerSpec) -> Path | None:
        try:
            from filelock import FileLock
            from huggingface_hub import snapshot_download
        except ImportError:
            self.last_error = "huggingface_dependencies_unavailable"
            return None

        lock_name = self._cache_key(spec) + ".lock"
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with FileLock(str(self.cache_dir / lock_name)):
                snapshot_path = self._snapshot_download_huggingface(
                    snapshot_download,
                    spec,
                )
                self.last_error = None
        except Exception as exc:  # remote libraries expose several exception types
            self.last_error = self._error_reason(exc)
            return None

        artifact = self._find_artifact_file(Path(snapshot_path), spec)
        if artifact is None:
            self.last_error = self._missing_artifact_reason(spec)
            return None
        return self._validate(artifact, spec)

    def _snapshot_download_huggingface(
        self,
        snapshot_download: Any,
        spec: TokenizerSpec | CompatibleTokenizerSpec,
    ) -> str:
        """Download from the configured Hugging Face endpoint."""
        return snapshot_download(
            repo_id=str(spec.tokenizer_id),
            revision=getattr(spec, "revision", None),
            cache_dir=str(self.cache_dir),
            allow_patterns=self._allow_patterns(spec),
            local_files_only=self.offline or not self.enable_download,
            endpoint=self.endpoint,
            etag_timeout=self.request_timeout,
        )

    def _resolve_modelscope(self, spec: TokenizerSpec | CompatibleTokenizerSpec) -> Path | None:
        cached = self._find_modelscope_cached_artifact(spec)
        if cached is not None:
            return self._validate(cached, spec)
        if self.offline or not self.enable_download:
            self.last_error = "remote_download_disabled"
            return None
        try:
            from filelock import FileLock
            from modelscope import snapshot_download
        except ImportError:
            self.last_error = "modelscope_dependency_unavailable"
            return None

        lock_name = self._cache_key(spec) + ".lock"
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with FileLock(str(self.cache_dir / lock_name)):
                try:
                    snapshot_path = snapshot_download(
                        model_id=str(spec.tokenizer_id),
                        revision=getattr(spec, "revision", None),
                        cache_dir=str(self.cache_dir),
                    )
                except TypeError:
                    # Older ModelScope releases do not expose all keyword
                    # arguments. The model ID is still the only remote input.
                    snapshot_path = snapshot_download(str(spec.tokenizer_id))
        except Exception as exc:  # optional dependency/provider failures
            self.last_error = self._error_reason(exc)
            return None

        artifact = self._find_artifact_file(Path(snapshot_path), spec)
        if artifact is None:
            self.last_error = self._missing_artifact_reason(spec)
            return None
        return self._validate(artifact, spec)

    def _find_modelscope_cached_artifact(
        self,
        spec: TokenizerSpec | CompatibleTokenizerSpec,
    ) -> Path | None:
        """Find a cached ModelScope snapshot without contacting the network.

        ModelScope has used both ``<cache>/<model>`` and
        ``<cache>/hub/models/<model>`` layouts across releases.  Search only
        directories derived from the configured model ID so an unrelated
        tokenizer in the cache cannot be selected accidentally.
        """
        tokenizer_id = str(getattr(spec, "tokenizer_id", "") or "").strip()
        if not tokenizer_id:
            return None
        model_path = Path(tokenizer_id)
        if model_path.is_absolute() or ".." in model_path.parts:
            return None
        candidates = (
            self.cache_dir / model_path,
            self.cache_dir / "models" / model_path,
            self.cache_dir / "hub" / "models" / model_path,
        )
        for candidate in candidates:
            artifact = self._find_artifact_file(candidate, spec)
            if artifact is not None:
                return artifact
        return None

    def _validate(self, path: Path, spec: TokenizerSpec | CompatibleTokenizerSpec) -> Path | None:
        if not path.is_file():
            self.last_error = self._missing_artifact_reason(spec)
            return None
        expected = getattr(spec, "sha256", None)
        if expected and self._sha256(path) != expected.lower():
            self.last_error = "tokenizer_sha256_mismatch"
            return None
        return path

    @classmethod
    def _find_artifact_file(
        cls,
        root: Path,
        spec: TokenizerSpec | CompatibleTokenizerSpec,
    ) -> Path | None:
        direct = cls._artifact_file(root, spec)
        if direct is not None:
            return direct
        if not root.exists():
            return None
        artifact_name = cls._artifact_name(spec)
        try:
            return next((path for path in root.rglob(artifact_name) if path.is_file()), None)
        except OSError:
            return None

    @staticmethod
    def _missing_artifact_reason(spec: TokenizerSpec | CompatibleTokenizerSpec) -> str:
        engine = str(getattr(spec, "engine", "auto") or "auto").strip().casefold()
        return "tiktoken_model_missing" if engine == "tiktoken" else "tokenizer_json_missing"

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _cache_key(spec: TokenizerSpec | CompatibleTokenizerSpec) -> str:
        fields = (
            str(getattr(spec, "source", "") or ""),
            str(getattr(spec, "tokenizer_id", "") or ""),
            str(getattr(spec, "revision", "") or ""),
            str(getattr(spec, "sha256", "") or ""),
            str(getattr(spec, "engine", "tokenizers") or "tokenizers"),
        )
        return hashlib.sha256("\x00".join(fields).encode("utf-8")).hexdigest()

    @staticmethod
    def _error_reason(exc: Any) -> str:
        message = str(exc).lower()
        network_markers = (
            "connecterror",
            "connection reset",
            "connection refused",
            "connection timed out",
            "connecttimeout",
            "network is unreachable",
            "temporary failure in name resolution",
            "name or service not known",
            "timed out",
        )
        if any(marker in message for marker in network_markers):
            return "tokenizer_network_unavailable"
        if "offline" in message or "local_files_only" in message or "not found in cache" in message:
            return "tokenizer_not_in_local_cache"
        if "revision" in message:
            return "tokenizer_revision_unavailable"
        return "tokenizer_download_failed"


__all__ = ["TokenizerArtifactManager"]
