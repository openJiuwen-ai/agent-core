from __future__ import annotations

import json
import re
from typing import Protocol

from openjiuwen.core.context_engine.processor.offloader.rules.diff_compressor import DiffCompressor
from openjiuwen.core.context_engine.processor.offloader.rules.html_compressor import HtmlCompressor
from openjiuwen.core.context_engine.processor.offloader.rules.json_array_compressor import (
    JsonArrayCompressor,
)
from openjiuwen.core.context_engine.processor.offloader.rules.log_compressor import LogCompressor
from openjiuwen.core.context_engine.processor.offloader.rules.plain_text_compressor import (
    PlainTextCompressor,
)
from openjiuwen.core.context_engine.processor.offloader.rules.search_results_compressor import (
    SearchResultsCompressor,
)
from openjiuwen.core.context_engine.processor.offloader.rules.source_code_compressor import (
    SourceCodeCompressor,
)
from openjiuwen.core.context_engine.processor.offloader.rules.types import (
    ContentType,
    RuleCompressionResult,
    RuleContext,
)


class RuleCompressor(Protocol):
    def compress(self, content: str, ctx: RuleContext) -> RuleCompressionResult: ...


class RuleContentRouter:
    """Detect content type and dispatch to one focused deterministic compressor."""

    _SEARCH_LINE_RE = re.compile(r"^.+?:\d+[:\-].+$")
    _ERROR_RE = re.compile(
        r"\b(error|failed|failure|traceback|exception|warn|warning)\b",
        re.IGNORECASE,
    )
    _CODE_RE = re.compile(
        r"^\s*(?:def|class|import|from|async\s+def|export|function|const|let|var|"
        r"pub|fn|impl|use|struct|enum|interface|type|package|func|public|private|"
        r"protected|#include)\b",
        re.MULTILINE,
    )

    def __init__(self) -> None:
        self._compressors: dict[ContentType, RuleCompressor] = {
            ContentType.JSON_ARRAY: JsonArrayCompressor(),
            ContentType.GIT_DIFF: DiffCompressor(),
            ContentType.HTML: HtmlCompressor(),
            ContentType.SEARCH_RESULTS: SearchResultsCompressor(),
            ContentType.BUILD_OUTPUT: LogCompressor(),
            ContentType.SOURCE_CODE: SourceCodeCompressor(),
            ContentType.PLAIN_TEXT: PlainTextCompressor(),
        }

    def detect(self, content: str) -> ContentType:
        text = (content or "").strip()
        if not text:
            return ContentType.PLAIN_TEXT
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, list):
            return ContentType.JSON_ARRAY
        if "diff --git " in text or re.search(r"(?m)^@@ .+ @@", text):
            return ContentType.GIT_DIFF
        lowered = text[:5000].lower()
        if "<!doctype html" in lowered or "<html" in lowered or "<body" in lowered:
            return ContentType.HTML
        lines = [line for line in text.splitlines() if line.strip()]
        if lines:
            matches = sum(1 for line in lines if self._SEARCH_LINE_RE.match(line))
            if matches / len(lines) >= 0.3:
                return ContentType.SEARCH_RESULTS
        if self._CODE_RE.search(text):
            return ContentType.SOURCE_CODE
        if self._ERROR_RE.search(text) or re.search(
            r"\b(pytest|npm|cargo|make|jest)\b",
            text,
            re.IGNORECASE,
        ):
            return ContentType.BUILD_OUTPUT
        return ContentType.PLAIN_TEXT

    def compress(self, content: str, ctx: RuleContext) -> RuleCompressionResult:
        content_type = self.detect(content)
        return self._compressors[content_type].compress(content, ctx)


ContentRouter = RuleContentRouter
