# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Lazy tree-sitter parsing for Code Graph.

``tree-sitter`` / ``tree-sitter-language-pack`` are optional. When they are
missing, or when the pack cannot load grammars (GitHub download timeout,
cache lock), the service reports ``UNAVAILABLE`` and callers fall back to grep.

``tree-sitter-language-pack`` 1.x downloads parser binaries on first
``get_parser()``. That must happen at most once: a failed download is cached
so ``build_index`` does not retry GitHub for every file.
"""

from __future__ import annotations

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


def parser_available() -> bool:
    """True when the language pack can be imported and has not failed to load."""
    if _BACKEND_UNAVAILABLE:
        return False
    return _load_get_parser() is not None


def parser_unavailable_reason() -> str:
    """Human-readable reason the parser cannot run."""
    if _BACKEND_UNAVAILABLE:
        return _BACKEND_UNAVAILABLE
    _load_get_parser()
    return _PARSER_IMPORT_ERROR or "tree-sitter parser is not available"


def python_grammar_is_cached() -> bool | None:
    """Whether the pack already has a local Python grammar.

    Looks at the on-disk cache only. Calling ``get_parser`` / ``downloaded_languages``
    can download from GitHub or take a cache lock, which times out in CI.

    Returns ``None`` for older wheels that bundle grammars and expose no cache
    directory API — callers may then call ``get_parser`` without a download.
    """
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
    return _dir_has_python_parser(root)


def _dir_has_python_parser(root: Path) -> bool:
    for path in root.iterdir():
        if "python" not in path.name.lower():
            continue
        if _looks_like_parser_lib(path):
            return True
    return False


def _looks_like_parser_lib(path: Path) -> bool:
    name = path.name.lower()
    if path.suffix in {".so", ".dylib", ".dll"}:
        return True
    if name.endswith(".so"):
        return True
    if ".so." in name:
        return True
    return False


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
    """Return a cached tree-sitter parser, or ``None`` after a failed load."""
    if _BACKEND_UNAVAILABLE:
        return None
    get_parser = _load_get_parser()
    if get_parser is None:
        return None
    try:
        return get_parser(lang_id)
    except Exception as exc:  # noqa: BLE001 — rust downloader raises pack-specific types
        _mark_backend_failure(lang_id, exc)
        return None


def _mark_backend_failure(lang_id: str, exc: BaseException) -> None:
    global _BACKEND_UNAVAILABLE, _PARSER_IMPORT_ERROR
    logger.warning("code_graph parser unavailable for %s: %s", lang_id, exc)
    if not _is_download_backend_error(exc):
        return
    _BACKEND_UNAVAILABLE = (
        "tree-sitter-language-pack could not load parsers "
        f"({exc}). Pre-download grammars or use a host that can reach "
        "GitHub releases; install openjiuwen[code-graph] is not enough."
    )
    _PARSER_IMPORT_ERROR = _BACKEND_UNAVAILABLE


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
    except ImportError as exc:
        _PARSER_IMPORT_ERROR = (
            "tree-sitter-language-pack is not installed; "
            "install openjiuwen[code-graph] to enable Code Graph"
        )
        logger.info("code_graph parser unavailable: %s", exc)
        return None
    if not _BACKEND_UNAVAILABLE:
        _PARSER_IMPORT_ERROR = None
    return get_parser
