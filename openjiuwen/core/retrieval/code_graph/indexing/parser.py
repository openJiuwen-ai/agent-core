# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Lazy tree-sitter parsing for Code Graph.

``tree-sitter`` / ``tree-sitter-language-pack`` are optional. When they are
missing, the service reports ``UNAVAILABLE`` and callers fall back to grep.
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


def parser_available() -> bool:
    """True when tree-sitter language pack can be imported."""
    return _load_get_parser() is not None


def parser_unavailable_reason() -> str:
    """Human-readable reason the parser cannot run."""
    _load_get_parser()
    return _PARSER_IMPORT_ERROR or "tree-sitter parser is not available"


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
    get_parser = _load_get_parser()
    if get_parser is None:
        return None
    lang_id = TREE_SITTER_LANGUAGE_IDS.get(language)
    if not lang_id:
        return None
    try:
        parser = get_parser(lang_id)
        return parser.parse(source)
    except Exception as exc:  # noqa: BLE001 — backend is optional and may raise anything
        logger.warning("code_graph parse failed for %s: %s", language, exc)
        return None


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
    _PARSER_IMPORT_ERROR = None
    return get_parser
