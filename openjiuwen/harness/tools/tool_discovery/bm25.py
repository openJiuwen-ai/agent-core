# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""BM25 index adapter for deferred tool discovery.

The scoring/tokenization implementation lives in the context engine's shared
BM25 module. This adapter only maps ``ToolInfo`` records to searchable text
and keeps the corresponding schemas beside the immutable index.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from collections import Counter
from typing import Any, List, Sequence

from pydantic import BaseModel

from openjiuwen.core.context_engine.processor.forked.compressor.recall.bm25 import (
    BM25Index,
    tokenize,
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


def _tool_signature(tool: ToolInfo) -> dict[str, str]:
    """Return the searchable/schema fields used to identify a tool version."""
    return {
        "type": str(getattr(tool, "type", "") or ""),
        "name": str(getattr(tool, "name", "") or ""),
        "description": str(getattr(tool, "description", "") or ""),
        "parameters": _parameters_text(getattr(tool, "parameters", None)),
    }


def _tool_fingerprint(tool: ToolInfo) -> str:
    """Create a deterministic fingerprint for a model-facing tool record."""
    serialized = json.dumps(
        _tool_signature(tool),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _is_tool_name_boundary_char(value: str) -> bool:
    """Return whether a character can be part of an ASCII tool identifier."""
    return bool(value) and value.isascii() and (value.isalnum() or value in "_.-")


def _tool_name_family(name: str) -> str:
    """Return the leading ASCII identifier component that groups a tool family.

    Tool names conventionally start with a capability/domain component
    (``heartbeat_create_job``, ``cron_list_jobs``, ``session_read``). Users and
    models search for that family word ("heartbeat", "cron") rather than the
    full identifier, so it needs its own ranking signal.
    """
    normalized = str(name or "").strip().casefold()
    if not normalized:
        return ""
    family = normalized.split("_", 1)[0]
    return family if family.isascii() and family.isalnum() else ""


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
    instance when the indexed tool metadata changes. This keeps searches
    lock-free and makes it explicit when a rebuild occurs.
    """

    def __init__(
        self,
        documents: Sequence[ToolInfo],
        index: BM25Index,
        source_fingerprint: str | None = None,
    ) -> None:
        self._documents = tuple(documents)
        self._index = index
        self._source_fingerprint = source_fingerprint or self.fingerprint(documents)
        # Number of indexed tools per leading name component. A component
        # shared by several tools is a family word ("heartbeat", "cron"); one
        # used by a single tool is too generic to outrank description matches.
        families = (
            _tool_name_family(getattr(document, "name", ""))
            for document in self._documents
        )
        self._name_family_counts: Counter[str] = Counter(
            family for family in families if family
        )

    @classmethod
    def build(
        cls,
        documents: Sequence[ToolInfo],
    ) -> "BM25ToolIndex":
        normalized_documents = [document for document in documents if document is not None]
        index = BM25Index.build(
            [_tool_text(document) for document in normalized_documents]
        )
        return cls(
            normalized_documents,
            index,
            source_fingerprint=cls.fingerprint(normalized_documents),
        )

    @staticmethod
    def fingerprint(documents: Sequence[ToolInfo]) -> str:
        """Return a deterministic fingerprint for the complete document set."""
        document_fingerprints = sorted(
            _tool_fingerprint(document)
            for document in documents
            if document is not None
        )
        serialized = json.dumps(
            document_fingerprints,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @staticmethod
    def tool_fingerprint(document: ToolInfo) -> str:
        """Return the fingerprint used for one search result authorization."""
        return _tool_fingerprint(document)

    @property
    def document_count(self) -> int:
        return self._index.document_count

    @property
    def documents(self) -> tuple[ToolInfo, ...]:
        return self._documents

    @property
    def source_fingerprint(self) -> str:
        """Fingerprint of the tool metadata used to build this index."""
        return self._source_fingerprint

    def search(self, query: str, *, limit: int = 5) -> List[ToolInfo]:
        if limit <= 0:
            return []

        if not query or not self._documents:
            return []

        # Family words are only honored when several tools share the leading
        # name component, so a single tool cannot hijack an ordinary query.
        query_family_tokens = {
            token
            for token in tokenize(query)
            if self._name_family_counts.get(token, 0) >= 2
        }

        scored = []
        for index, score in enumerate(self._index.scores(query)):
            document = self._documents[index]
            name = str(getattr(document, "name", "") or "")
            exact_name_match = _contains_exact_tool_name(query, name)
            family_match = (
                bool(query_family_tokens)
                and _tool_name_family(name) in query_family_tokens
            )
            if score <= 0.0 and not exact_name_match and not family_match:
                continue
            scored.append((exact_name_match, family_match, score, index, document))

        scored.sort(
            key=lambda item: (
                not item[0],
                not item[1],
                -item[2],
                str(getattr(item[4], "name", "") or ""),
                item[3],
            )
        )
        return [document for _, _, _, _, document in scored[:limit]]


__all__ = ["BM25ToolIndex"]
