# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Lazy tree-sitter parsing for Code Graph.

``tree-sitter-language-pack`` is optional and is not a ``uv sync`` extra.
Install the pack yourself, then call ``preload_language_pack()`` immediately.
Indexing never downloads grammars: if they are not already cached, the query
falls back to grep.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import Any

from openjiuwen.core.common.logging import retrieval_logger as logger
from openjiuwen.core.retrieval.code_graph.indexing.language_registry import (
    TREE_SITTER_LANGUAGE_IDS,
    SourceLanguage,
    language_from_path,
)

_PARSER_IMPORT_ERROR: str | None = None
_BACKEND_UNAVAILABLE: str | None = None
_DOWNLOAD_ERROR_NAMES = frozenset({"DownloadError", "CacheLockError"})
_DOWNLOAD_DENIED = frozenset({"0", "false", "no"})
_DOWNLOAD_ALLOWED = frozenset({"1", "true", "yes"})
_CACHE_WALK_DEPTH = 3
_LANGUAGE_PACK_REQUIRED = (
    "Download tree-sitter-language-pack to enable Code Graph. Falling back to grep."
)


def parser_available() -> bool:
    """True when the pack is importable and a local Python grammar is ready."""
    if _BACKEND_UNAVAILABLE:
        return False
    if _load_get_parser() is None:
        return False
    if _should_avoid_uncached_fetch("python"):
        return False
    return True


def parser_unavailable_reason() -> str:
    """Human-readable reason the parser cannot run."""
    if _BACKEND_UNAVAILABLE:
        return _BACKEND_UNAVAILABLE
    if _load_get_parser() is None:
        return _PARSER_IMPORT_ERROR or _LANGUAGE_PACK_REQUIRED
    if _should_avoid_uncached_fetch("python"):
        return _LANGUAGE_PACK_REQUIRED
    return _PARSER_IMPORT_ERROR or "tree-sitter parser is not available"


def python_grammar_is_cached() -> bool | None:
    """Whether the pack already has a local Python grammar.

    Looks at the on-disk cache only. Calling ``get_parser`` / ``downloaded_languages``
    can download from GitHub or take a cache lock, which times out in CI.

    Returns ``None`` for older wheels that expose no cache directory API.
    A missing or unknown cache means indexing will not call ``get_parser``.
    """
    return _grammar_is_cached("python")


def _grammar_is_cached(lang_id: str) -> bool | None:
    if _load_get_parser() is None:
        return False
    try:
        from tree_sitter_language_pack import cache_dir
    except ImportError:
        return None
    try:
        root = Path(str(cache_dir()))
    except Exception:  # noqa: BLE001 — cache probe must not raise
        return False
    if not root.is_dir():
        return False
    return _dir_has_language_parser(root, lang_id)


def _dir_has_language_parser(root: Path, lang_id: str) -> bool:
    for path in _iter_cache_files(root, _CACHE_WALK_DEPTH):
        if _is_language_parser_lib(path, lang_id):
            return True
    return False


def _iter_cache_files(root: Path, max_depth: int) -> Iterator[Path]:
    stack = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        try:
            children = current.iterdir()
        except OSError:
            continue
        for path in children:
            if path.is_file():
                yield path
                continue
            if not path.is_dir():
                continue
            if depth < max_depth:
                stack.append((path, depth + 1))


def _is_language_parser_lib(path: Path, lang_id: str) -> bool:
    if not _looks_like_parser_lib(path):
        return False
    prefix = f"libtree_sitter_{lang_id.lower()}"
    name = path.name.lower()
    return name.startswith(f"{prefix}.")


def _looks_like_parser_lib(path: Path) -> bool:
    name = path.name.lower()
    if path.suffix in {".so", ".dylib", ".dll"}:
        return True
    if name.endswith(".so"):
        return True
    if ".so." in name:
        return True
    return False


def preload_language_pack() -> bool:
    """Download grammars during setup. Do not call this from a query path."""
    get_parser = _load_get_parser()
    if get_parser is None:
        return False
    try:
        get_parser("python")
    except Exception as exc:  # noqa: BLE001 — rust downloader raises pack-specific types
        _mark_backend_failure("python", exc)
        logger.warning("%s", _LANGUAGE_PACK_REQUIRED)
        return False
    _parser_for.cache_clear()
    return True


def parse_source(path: Path, source: bytes) -> Any | None:
    """Parse ``source`` with the language inferred from ``path``.

    Returns a tree-sitter ``Tree``, or ``None`` when the language is unsupported
    or the parser backend is missing.
    """
    language = language_from_path(path)
    if language is None:
        return None
    return parse_source_as(language, source)


def parse_source_as(language: SourceLanguage, source: bytes) -> Any | None:
    """Parse ``source`` as ``language``."""
    lang_id = TREE_SITTER_LANGUAGE_IDS.get(language)
    if not lang_id:
        return None
    parser = _parser_for(lang_id)
    if parser is None:
        return None
    try:
        return parser.parse(source)
    except Exception as exc:  # noqa: BLE001 — backend is optional and may raise anything
        logger.warning("code_graph parse failed for %s: %s", language, exc)
        return None


def _reset_parser_state() -> None:
    """Clear import and per-language caches. Tests use this after mocking imports."""
    global _PARSER_IMPORT_ERROR, _BACKEND_UNAVAILABLE
    _PARSER_IMPORT_ERROR = None
    _BACKEND_UNAVAILABLE = None
    _load_get_parser.cache_clear()
    _parser_for.cache_clear()


@lru_cache(maxsize=None)
def _parser_for(lang_id: str) -> Any | None:
    """Return a cached tree-sitter parser, or ``None`` after a failed load.

    Two layers, on purpose:

    * Admission (``parser_available``) is Python-only. The product graph is
      Python-first: no pack / no cached Python grammar means the whole
      backend is off and grep comes back.
    * Per-file parse treats every language the same. An uncached or failed
      grammar skips that language's files. YAML/HTML/TS/Go missing cache
      must not UNAVAILABLE a repo that still has Python. Python missing is
      already the admission check, so this path never disables the backend.
    """
    if _BACKEND_UNAVAILABLE:
        return None
    get_parser = _load_get_parser()
    if get_parser is None:
        return None
    if _should_avoid_uncached_fetch(lang_id):
        logger.warning(
            "code_graph skipping %s: grammar is not cached locally",
            lang_id,
        )
        return None
    try:
        return get_parser(lang_id)
    except Exception as exc:  # noqa: BLE001 — rust downloader raises pack-specific types
        _mark_backend_failure(lang_id, exc)
        return None


def _should_avoid_uncached_fetch(lang_id: str) -> bool:
    if _network_fetch_allowed():
        return False
    return _grammar_is_cached(lang_id) is not True


def _network_fetch_allowed() -> bool:
    """Indexing never fetches unless setup explicitly opts in."""
    flag = os.environ.get("OPENJIUWEN_CODE_GRAPH_ALLOW_PARSER_DOWNLOAD", "").strip().lower()
    if flag in _DOWNLOAD_DENIED:
        return False
    return flag in _DOWNLOAD_ALLOWED


def _mark_backend_failure(lang_id: str, exc: BaseException) -> None:
    global _BACKEND_UNAVAILABLE, _PARSER_IMPORT_ERROR
    if lang_id == "python" and _is_download_backend_error(exc):
        _BACKEND_UNAVAILABLE = f"{_LANGUAGE_PACK_REQUIRED} ({exc})"
        _PARSER_IMPORT_ERROR = _BACKEND_UNAVAILABLE
        logger.warning("%s", _BACKEND_UNAVAILABLE)
        return
    logger.warning("code_graph parser unavailable for %s: %s", lang_id, exc)


def _is_download_backend_error(exc: BaseException) -> bool:
    if type(exc).__name__ in _DOWNLOAD_ERROR_NAMES:
        return True
    text = str(exc).lower()
    if "failed to download" in text:
        return True
    if "download cache lock" in text:
        return True
    return False


@lru_cache(maxsize=1)
def _load_get_parser() -> Any | None:
    global _PARSER_IMPORT_ERROR
    try:
        from tree_sitter_language_pack import get_parser
    except ImportError:
        _PARSER_IMPORT_ERROR = _LANGUAGE_PACK_REQUIRED
        logger.warning("%s", _LANGUAGE_PACK_REQUIRED)
        return None
    if not _BACKEND_UNAVAILABLE:
        _PARSER_IMPORT_ERROR = None
    return get_parser
