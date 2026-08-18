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

        scored = [
            (score, index, self._documents[index])
            for index, score in enumerate(self._index.scores(query))
            if score > 0.0
        ]

        scored.sort(
            key=lambda item: (
                -item[0],
                str(getattr(item[2], "name", "") or ""),
                item[1],
            )
        )
        return [document for _, _, document in scored[:limit]]


__all__ = ["BM25ToolIndex"]
