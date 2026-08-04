"""Small, embedded data-type compatibility vocabulary for graph candidates."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DataTypeInference:
    data_type: str


class DataTypeVocabulary:
    CONTENT_CARRIER_TYPES = frozenset({"text", "markdown", "json", "csv", "table", "yaml", "xml", "html", "code"})
    MARKUP_TEXT_TYPES = frozenset({"markdown", "html"})
    STRUCTURED_TEXT_TYPES = frozenset({"json", "yaml", "xml"})
    TYPE_HIERARCHY = {
        "markdown": frozenset({"text"}),
        "json": frozenset({"file", "text"}),
        "csv": frozenset({"file", "table", "text"}),
        "table": frozenset({"text"}),
        "yaml": frozenset({"file", "text"}),
        "xml": frozenset({"file", "text"}),
        "html": frozenset({"file", "text"}),
        "pdf": frozenset({"file"}),
        "docx": frozenset({"file"}),
        "pptx": frozenset({"file"}),
        "xlsx": frozenset({"file", "table"}),
        "png": frozenset({"image", "file"}),
        "jpg": frozenset({"image", "file"}),
        "svg": frozenset({"image", "file"}),
        "webp": frozenset({"image", "file"}),
        "gif": frozenset({"image", "file"}),
        "image": frozenset({"file"}),
        "audio": frozenset({"file"}),
        "video": frozenset({"file"}),
        "path": frozenset({"file"}),
        "code": frozenset({"file", "text"}),
        "archive": frozenset({"file"}),
    }

    @classmethod
    def default(cls) -> "DataTypeVocabulary":
        return cls()

    def is_subtype(self, child: str, parent: str) -> bool:
        child = str(child).lower()
        parent = str(parent).lower()
        if child == parent:
            return True
        frontier = list(self.TYPE_HIERARCHY.get(child, ()))
        seen: set[str] = set()
        while frontier:
            current = frontier.pop()
            if current == parent:
                return True
            if current not in seen:
                seen.add(current)
                frontier.extend(self.TYPE_HIERARCHY.get(current, ()))
        return False

    def can_feed_by_type(self, output_type: str, input_type: str) -> bool:
        if output_type == "unknown" or input_type == "unknown":
            return False
        if output_type == input_type or self.is_subtype(output_type, input_type):
            return True
        if input_type == "text" and output_type in self.CONTENT_CARRIER_TYPES:
            return True
        if output_type == "text" and input_type in self.MARKUP_TEXT_TYPES:
            return True
        if output_type in self.MARKUP_TEXT_TYPES and input_type in self.MARKUP_TEXT_TYPES:
            return True
        return output_type in self.STRUCTURED_TEXT_TYPES and input_type in self.STRUCTURED_TEXT_TYPES

    @staticmethod
    def infer_from_io_semantics(io_name: str, description: str) -> DataTypeInference | None:
        text = f"{io_name} {description}".lower()
        if re.search(r"\b(?:https?|url|uri|remote url|download url)\b|_(?:url|uri)\b", text):
            return DataTypeInference("url")
        return None
