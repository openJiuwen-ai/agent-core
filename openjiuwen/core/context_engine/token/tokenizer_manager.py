# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Resolve, download, validate, and cache native tokenizer artifacts."""

from __future__ import annotations

import hashlib
import os
import threading
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from openjiuwen.core.context_engine.token.tokenizer_spec import (
    CompatibleTokenizerSpec,
    TokenizerSpec,
)


_HF_NETWORK_LOCK = threading.RLock()
_HF_CLIENT_UNCONFIGURED = object()
_HF_CLIENT_ROUTE: object = _HF_CLIENT_UNCONFIGURED
_PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
_NO_PROXY_ENV_KEYS = ("NO_PROXY", "no_proxy")
_AUTO_PROXY_VALUES = frozenset({"", "auto"})
_DIRECT_PROXY_VALUES = frozenset({"direct", "none", "off", "disabled"})


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
        proxy: str | None = None,
    ) -> None:
        configured_cache_dir = cache_dir or os.getenv(
            "OPENJIUWEN_TOKENIZER_CACHE_DIR",
            "~/.cache/openjiuwen/tokenizers",
        )
        self.cache_dir = Path(configured_cache_dir).expanduser()
        self.enable_download = enable_download
        self.offline = offline
        self.proxy = str(proxy).strip() if proxy is not None else None
        self.last_error: str | None = None

    def resolve(self, spec: TokenizerSpec | CompatibleTokenizerSpec) -> Path | None:
        """Return a validated local ``tokenizer.json`` path for ``spec``.

        A missing or invalid artifact returns ``None`` and records a compact
        reason. Callers can then continue with the configured fallback chain.
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
            return self._tokenizer_file(path)

        tokenizer_id = getattr(spec, "tokenizer_id", None)
        if tokenizer_id:
            candidate = Path(tokenizer_id).expanduser()
            return self._tokenizer_file(candidate) if candidate.exists() else None
        return None

    @staticmethod
    def _tokenizer_file(path: Path) -> Path | None:
        if path.is_file():
            return path
        if path.is_dir():
            candidate = path / "tokenizer.json"
            return candidate if candidate.is_file() else None
        return None

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
        except Exception as exc:  # remote libraries expose several exception types
            self.last_error = self._error_reason(exc)
            return None

        artifact = self._find_tokenizer_file(Path(snapshot_path))
        if artifact is None:
            self.last_error = "tokenizer_json_missing"
            return None
        return self._validate(artifact, spec)

    def _snapshot_download_huggingface(
        self,
        snapshot_download: Any,
        spec: TokenizerSpec | CompatibleTokenizerSpec,
    ) -> str:
        """Download through an explicit or automatically discovered route.

        The process may carry a ``NO_PROXY`` value for local services while
        the operating system has a proxy configured for external traffic. In
        that situation Hugging Face can incorrectly go direct and fail before
        reaching the Hub. Resolve the route once for the Hub client, without
        changing the caller's environment for the rest of the process.
        """
        if self.offline or not self.enable_download:
            return snapshot_download(
                repo_id=str(spec.tokenizer_id),
                revision=getattr(spec, "revision", None),
                cache_dir=str(self.cache_dir),
                allow_patterns=["tokenizer.json"],
                local_files_only=True,
            )

        routes = _huggingface_proxy_routes(self.proxy)
        last_error: Exception | None = None
        for index, proxy in enumerate(routes):
            try:
                with _HF_NETWORK_LOCK:
                    if not _configure_huggingface_client(proxy):
                        with _temporary_huggingface_network(proxy):
                            return snapshot_download(
                                repo_id=str(spec.tokenizer_id),
                                revision=getattr(spec, "revision", None),
                                cache_dir=str(self.cache_dir),
                                allow_patterns=["tokenizer.json"],
                                local_files_only=self.offline or not self.enable_download,
                            )
                    return snapshot_download(
                        repo_id=str(spec.tokenizer_id),
                        revision=getattr(spec, "revision", None),
                        cache_dir=str(self.cache_dir),
                        allow_patterns=["tokenizer.json"],
                        local_files_only=self.offline or not self.enable_download,
                    )
            except Exception as exc:  # remote libraries expose several exception types
                last_error = exc
                self.last_error = self._error_reason(exc)
                # If the automatically discovered system route is unavailable,
                # make one direct attempt. An explicitly configured proxy is
                # authoritative and is not silently bypassed.
                if (
                    index + 1 < len(routes)
                    and self.last_error == "tokenizer_network_unavailable"
                ):
                    continue
                raise
        if last_error is not None:
            raise last_error
        raise RuntimeError("tokenizer_download_failed")

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

        artifact = self._find_tokenizer_file(Path(snapshot_path))
        if artifact is None:
            self.last_error = "tokenizer_json_missing"
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
            artifact = self._find_tokenizer_file(candidate)
            if artifact is not None:
                return artifact
        return None

    def _validate(self, path: Path, spec: TokenizerSpec | CompatibleTokenizerSpec) -> Path | None:
        if not path.is_file():
            self.last_error = "tokenizer_json_missing"
            return None
        expected = getattr(spec, "sha256", None)
        if expected and self._sha256(path) != expected.lower():
            self.last_error = "tokenizer_sha256_mismatch"
            return None
        return path

    @staticmethod
    def _find_tokenizer_file(root: Path) -> Path | None:
        direct = TokenizerArtifactManager._tokenizer_file(root)
        if direct is not None:
            return direct
        if not root.exists():
            return None
        try:
            return next((path for path in root.rglob("tokenizer.json") if path.is_file()), None)
        except OSError:
            return None

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


def _huggingface_proxy_routes(proxy: str | None) -> tuple[str | None, ...]:
    """Return the configured route and an automatic direct fallback."""
    configured = str(proxy or "").strip()
    normalized = configured.casefold()
    if normalized in _DIRECT_PROXY_VALUES:
        return (None,)
    if normalized not in _AUTO_PROXY_VALUES:
        return (configured,)

    with _HF_NETWORK_LOCK:
        system_proxy = _discover_system_proxy()
    if system_proxy:
        return (system_proxy, None)
    return (None,)


def _discover_system_proxy() -> str | None:
    """Discover environment/OS proxy settings without honoring NO_PROXY.

    On macOS, ``urllib.request.getproxies`` can expose the system proxy only
    when ``NO_PROXY`` is absent. The temporary removal is limited to this
    short discovery call and the original environment is restored immediately.
    """
    previous = {key: os.environ.get(key) for key in _NO_PROXY_ENV_KEYS}
    try:
        for key in _NO_PROXY_ENV_KEYS:
            os.environ.pop(key, None)
        proxies = urllib.request.getproxies()
    except Exception:  # noqa: BLE001 - proxy discovery is best effort
        proxies = {}
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    for key in ("https", "http", "all"):
        value = str(proxies.get(key) or "").strip()
        if value:
            return value
    return None


def _configure_huggingface_client(proxy: str | None) -> bool:
    """Configure Hugging Face's shared client for one route if supported."""
    global _HF_CLIENT_ROUTE
    with _HF_NETWORK_LOCK:
        if _HF_CLIENT_ROUTE == proxy:
            return True
        try:
            import httpx
            import huggingface_hub
        except ImportError:
            return False

        set_client_factory = getattr(huggingface_hub, "set_client_factory", None)
        if not callable(set_client_factory):
            try:
                from huggingface_hub.utils._http import set_client_factory
            except ImportError:
                return False

        def client_factory() -> httpx.Client:
            kwargs: dict[str, Any] = {
                "follow_redirects": True,
                "timeout": None,
                "trust_env": False,
            }
            if proxy:
                kwargs["proxy"] = proxy
            return httpx.Client(**kwargs)

        set_client_factory(client_factory)
        _HF_CLIENT_ROUTE = proxy
        return True


@contextmanager
def _temporary_huggingface_network(proxy: str | None):
    """Fallback environment route for older Hugging Face Hub versions."""
    previous = {key: os.environ.get(key) for key in (*_PROXY_ENV_KEYS, *_NO_PROXY_ENV_KEYS)}
    try:
        for key in _PROXY_ENV_KEYS:
            if proxy:
                os.environ[key] = proxy
            else:
                os.environ.pop(key, None)
        for key in _NO_PROXY_ENV_KEYS:
            os.environ.pop(key, None)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


__all__ = ["TokenizerArtifactManager"]
