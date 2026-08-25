# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""BM25 index adapter for deferred tool discovery.

The scoring/tokenization implementation lives in the context engine's shared
BM25 module. This adapter only maps ``ToolInfo`` records to searchable text
and keeps the corresponding schemas beside the immutable index.
"""

from __future__ import annotations

import inspect
import json
from typing import Any, List, Sequence

from pydantic import BaseModel

from openjiuwen.core.context_engine.processor.forked.compressor.recall.bm25 import (
    BM25Index,
)
from openjiuwen.core.foundation.tool import ToolInfo


def _parameters_text(parameters: Any) -> str:
    if inspect.isclass(parameters) and issubclass(parameters, BaseModel):
        try:
            parameters = parameters.model_json_schema()
        except Exception:
            return str(parameters)
    if isinstance(parameters, dict):
        try:
            return json.dumps(parameters, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            return str(parameters)
    return str(parameters or "")


def _tool_text(tool: ToolInfo) -> str:
    return " ".join(
        [
            str(getattr(tool, "name", "") or ""),
            str(getattr(tool, "description", "") or ""),
            _parameters_text(getattr(tool, "parameters", None)),
        ]
    )


def _is_tool_name_boundary_char(value: str) -> bool:
    """Return whether a character can be part of an ASCII tool identifier."""
    return bool(value) and value.isascii() and (value.isalnum() or value in "_.-")


def _contains_exact_tool_name(query: str, tool_name: str) -> bool:
    """Check whether ``query`` contains ``tool_name`` as a complete identifier.

    Tool names are normally ASCII identifiers such as ``search_skill``. Treat
    Chinese characters and punctuation as boundaries so natural-language
    queries like ``请调用search_skill搜索技能`` still match, while avoiding
    false positives inside names such as ``search_skill_extra``.
    """
    normalized_query = str(query or "").casefold()
    normalized_name = str(tool_name or "").strip().casefold()
    if not normalized_query or not normalized_name:
        return False

    start = 0
    while start < len(normalized_query):
        match_index = normalized_query.find(normalized_name, start)
        if match_index < 0:
            return False

        before = normalized_query[match_index - 1] if match_index else ""
        end_index = match_index + len(normalized_name)
        after = normalized_query[end_index] if end_index < len(normalized_query) else ""
        if not _is_tool_name_boundary_char(before) and not _is_tool_name_boundary_char(after):
            return True

        start = match_index + 1

    return False


class BM25ToolIndex:
    """In-memory BM25 index over model-facing tool schemas.

    The index is immutable after :meth:`build`; callers replace the whole
    instance when the registered-tool revision changes. This keeps searches
    lock-free and makes it explicit when a rebuild occurs.
    """

    def __init__(self, documents: Sequence[ToolInfo], index: BM25Index) -> None:
        self._documents = tuple(documents)
        self._index = index

    @classmethod
    def build(
        cls,
        documents: Sequence[ToolInfo],
    ) -> "BM25ToolIndex":
        normalized_documents = [document for document in documents if document is not None]
        index = BM25Index.build(
            [_tool_text(document) for document in normalized_documents]
        )
        return cls(normalized_documents, index)

    @property
    def document_count(self) -> int:
        return self._index.document_count

    @property
    def documents(self) -> tuple[ToolInfo, ...]:
        return self._documents

    def search(self, query: str, *, limit: int = 5) -> List[ToolInfo]:
        if limit <= 0:
            return []

        if not query or not self._documents:
            return []

        scored = []
        for index, score in enumerate(self._index.scores(query)):
            document = self._documents[index]
            exact_name_match = _contains_exact_tool_name(
                query,
                str(getattr(document, "name", "") or ""),
            )
            if score <= 0.0 and not exact_name_match:
                continue
            scored.append((exact_name_match, score, index, document))

        scored.sort(
            key=lambda item: (
                not item[0],
                -item[1],
                str(getattr(item[3], "name", "") or ""),
                item[2],
            )
        )
        return [document for _, _, _, document in scored[:limit]]


__all__ = ["BM25ToolIndex"]
