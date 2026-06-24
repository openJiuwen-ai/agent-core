from __future__ import annotations

import json
import re
from typing import Protocol

from json_repair import loads as repair_json_loads

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

    _HTML_STRUCTURAL_TAG_RE = re.compile(
        r"</?(?:html|head|body|article|main|section|div|script|style|nav|footer|aside|"
        r"table|thead|tbody|tr|th|td|ul|ol|li|p|pre|code|strong|span|h[1-6])\b",
        re.IGNORECASE,
    )
    _SEARCH_LINE_RE = re.compile(r"^.+?:\d+[:\-].+$")
    _TIMESTAMP_LOG_LINE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}\b")
    _BUILD_OUTPUT_RE = re.compile(
        r"(?im)("
        r"^=+ (?:test session starts|failures|errors|short test summary info) =+$|"
        r"^collected \d+ items\b|"
        r"^(?:FAILED|ERROR) .+::.+(?: - |$)|"
        r"^\d+\s+(?:failed|passed|skipped|errors?|warnings?)\b|"
        r"\b\d+\s+failed,\s+\d+\s+passed\b|"
        r"^npm (?:ERR!|WARN|info)\b|"
        r"^(?:PASS|FAIL) \S+|"
        r"^Test Suites:|"
        r"^(?:Compiling|Finished|Running) \S+|"
        r"^(?:error|warning)(?:\[[A-Z]\d+\])?:|"
        r"^(?:\[[A-Z]+\]\s*)?(?:ERROR|WARN|WARNING|INFO|DEBUG|TRACE|FATAL|CRITICAL)\b|"
        r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}.*\b(?:ERROR|WARN|WARNING|INFO|DEBUG|TRACE|FATAL|CRITICAL)\b|"
        r"^.+?:\d+(?::\d+)?:\s*(?:error|warning):|"
        r"^make(?:\[\d+\])?:|"
        r"^Traceback \(most recent call last\)|"
        r"^\s*File \".+\", line \d+|"
        r"^\s*at .+\(.+:\d+:\d+\)|"
        r"^-->\s+.+:\d+:\d+"
        r")"
    )
    _CODE_RE = re.compile(
        r"^\s*(?:def|class|import|from|async\s+def|export|function|const|let|var|"
        r"pub|fn|impl|use|struct|enum|interface|type|package|func|public|private|"
        r"protected|#include)\b",
        re.MULTILINE,
    )
    _NUMBERED_LINE_PREFIX_RE = re.compile(
        r"(?m)^\s*\d+(?:\t|\s+(?=(?:[\[{\]}],?|\"|</?|[A-Za-z_.#-][^\s]*\s*[:{])))"
    )
    _NUMBERED_SOURCE_LINE_RE = re.compile(
        r"(?m)^\s*\d+\t\s*(?:@|async\s+def|def|class|import|from|try:|if |elif |"
        r"else:|for |while |with |return\b|[A-Za-z_]\w*\s*=)"
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
        json_text = self._json_detection_text(text)
        try:
            parsed = json.loads(json_text)
        except (TypeError, ValueError):
            parsed = self._repair_json_array(json_text)
        if isinstance(parsed, list):
            return ContentType.JSON_ARRAY
        html_detection_text = self._html_detection_text(text)
        lowered = html_detection_text[:20000].lower()
        if (
            "<!doctype html" in lowered
            or "<html" in lowered
            or "<head" in lowered
            or "<body" in lowered
            or len(self._HTML_STRUCTURAL_TAG_RE.findall(lowered)) >= 2
        ):
            return ContentType.HTML
        source_text = self._source_detection_text(text)
        if source_text != text and self._CODE_RE.search(source_text):
            return ContentType.SOURCE_CODE
        if self._BUILD_OUTPUT_RE.search(text):
            return ContentType.BUILD_OUTPUT
        if "diff --git " in text or re.search(r"(?m)^@@ .+ @@", text):
            return ContentType.GIT_DIFF
        lines = [line for line in text.splitlines() if line.strip()]
        if lines:
            matches = sum(
                1
                for line in lines
                if self._SEARCH_LINE_RE.match(line) and not self._TIMESTAMP_LOG_LINE_RE.match(line)
            )
            if matches / len(lines) >= 0.3:
                return ContentType.SEARCH_RESULTS
        if self._CODE_RE.search(source_text):
            return ContentType.SOURCE_CODE
        return ContentType.PLAIN_TEXT

    def compress(self, content: str, ctx: RuleContext) -> RuleCompressionResult:
        content_type = self.detect(content)
        routed_content = (
            self._strip_numbered_line_prefixes(content)
            if content_type in {ContentType.HTML, ContentType.JSON_ARRAY}
            else content
        )
        return self._compressors[content_type].compress(routed_content, ctx)

    def _json_detection_text(self, content: str) -> str:
        stripped = self._strip_numbered_line_prefixes(content)
        return stripped if stripped != content else content

    def _repair_json_array(self, content: str) -> object | None:
        if not content.lstrip().startswith("["):
            return None
        try:
            return repair_json_loads(content)
        except Exception:
            return None

    def _html_detection_text(self, content: str) -> str:
        stripped = self._strip_numbered_line_prefixes(content)
        return stripped if stripped != content else content

    def _source_detection_text(self, content: str) -> str:
        stripped = self._strip_numbered_source_lines(content)
        return stripped if stripped != content else content

    def _strip_numbered_source_lines(self, content: str) -> str:
        if len(self._NUMBERED_SOURCE_LINE_RE.findall(content)) < 2:
            return content
        return self._strip_numbered_line_prefixes(content)

    def _strip_numbered_line_prefixes(self, content: str) -> str:
        return self._NUMBERED_LINE_PREFIX_RE.sub("", content)


ContentRouter = RuleContentRouter
