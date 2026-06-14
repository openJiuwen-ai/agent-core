from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from enum import Enum
from html.parser import HTMLParser
from typing import Any


class ContentType(str, Enum):
    JSON_ARRAY = "JSON_ARRAY"
    GIT_DIFF = "GIT_DIFF"
    HTML = "HTML"
    SEARCH_RESULTS = "SEARCH_RESULTS"
    BUILD_OUTPUT = "BUILD_OUTPUT"
    SOURCE_CODE = "SOURCE_CODE"
    PLAIN_TEXT = "PLAIN_TEXT"


@dataclass(frozen=True)
class RuleContext:
    max_tokens: int
    head_tokens: int = 2000
    tail_tokens: int = 2000


@dataclass(frozen=True)
class RuleCompressionResult:
    content: str
    content_type: ContentType
    modified: bool


class ContentRouter:
    """Deterministic, dependency-light compression for oversized tool results."""

    _SEARCH_LINE_RE = re.compile(r"^.+?:\d+[:\-].+$")
    _ERROR_RE = re.compile(r"\b(error|failed|failure|traceback|exception|warn|warning)\b", re.IGNORECASE)
    _CODE_RE = re.compile(
        r"^\s*(def|class|import|from|async\s+def|function|const|let|var|pub|fn|impl|package)\b",
        re.MULTILINE,
    )

    def detect(self, content: str) -> ContentType:
        text = (content or "").strip()
        if not text:
            return ContentType.PLAIN_TEXT
        try:
            parsed = json.loads(text)
        except Exception:
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
            matched = sum(1 for line in lines if self._SEARCH_LINE_RE.match(line))
            if matched / len(lines) >= 0.3:
                return ContentType.SEARCH_RESULTS
        if self._ERROR_RE.search(text) or re.search(r"\b(pytest|npm|cargo|make|jest)\b", text, re.IGNORECASE):
            return ContentType.BUILD_OUTPUT
        if self._CODE_RE.search(text):
            return ContentType.SOURCE_CODE
        return ContentType.PLAIN_TEXT

    def compress(self, content: str, ctx: RuleContext) -> RuleCompressionResult:
        content_type = self.detect(content)
        if content_type == ContentType.JSON_ARRAY:
            compressed = self._compress_json_array(content, ctx)
        elif content_type == ContentType.SEARCH_RESULTS:
            compressed = self._compress_search_results(content)
        elif content_type == ContentType.BUILD_OUTPUT:
            compressed = self._compress_log_output(content)
        elif content_type == ContentType.GIT_DIFF:
            compressed = self._compress_diff(content)
        elif content_type == ContentType.HTML:
            compressed = self._extract_html_text(content)
        elif content_type == ContentType.SOURCE_CODE:
            compressed = content
        else:
            compressed = self._compress_plain_text(content)
        return RuleCompressionResult(
            content=compressed,
            content_type=content_type,
            modified=compressed != content,
        )

    def _compress_json_array(self, content: str, ctx: RuleContext) -> str:
        try:
            rows = json.loads(content)
        except Exception:
            return content
        if not isinstance(rows, list) or not rows:
            return content
        if not all(isinstance(row, dict) for row in rows):
            return self._select_head_tail_json(rows)
        keys = sorted({key for row in rows for key in row.keys()})
        if not keys:
            return content
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=keys, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        table = out.getvalue().strip()
        if self._estimate_tokens(table) <= ctx.max_tokens:
            return f"[JSON_ARRAY compressed to CSV]\n{table}"
        selected = self._select_salient_rows(rows)
        return json.dumps(selected, ensure_ascii=False, indent=2)

    @staticmethod
    def _select_head_tail_json(rows: list[Any]) -> str:
        if len(rows) <= 10:
            return json.dumps(rows, ensure_ascii=False, indent=2)
        selected = rows[:5] + [{"_omitted": len(rows) - 10}] + rows[-5:]
        return json.dumps(selected, ensure_ascii=False, indent=2)

    def _select_salient_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(rows) <= 12:
            return rows
        scored = []
        for index, row in enumerate(rows):
            text = json.dumps(row, ensure_ascii=False).lower()
            score = 0
            if index < 5 or index >= len(rows) - 5:
                score += 10
            if self._ERROR_RE.search(text):
                score += 20
            scored.append((score, index, row))
        selected = sorted(sorted(scored, reverse=True)[:12], key=lambda item: item[1])
        result = [row for _, _, row in selected]
        omitted = len(rows) - len(result)
        if omitted > 0:
            result.insert(min(5, len(result)), {"_omitted": omitted})
        return result

    def _compress_search_results(self, content: str) -> str:
        grouped: dict[str, list[str]] = {}
        order: list[str] = []
        for line in content.splitlines():
            match = re.match(r"^(?P<file>.+?):\d+[:\-].+$", line)
            if not match:
                continue
            file_path = match.group("file")
            if file_path not in grouped:
                grouped[file_path] = []
                order.append(file_path)
            grouped[file_path].append(line)
        if not grouped:
            return content
        lines: list[str] = ["[SEARCH_RESULTS compressed]"]
        for file_path in order[:15]:
            matches = grouped[file_path]
            kept = self._keep_first_last_and_errors(matches, max_lines=5)
            lines.extend(kept)
            omitted = len(matches) - len(kept)
            if omitted > 0:
                lines.append(f"[... and {omitted} more matches in {file_path}]")
        if len(order) > 15:
            lines.append(f"[... and {len(order) - 15} more files omitted]")
        return "\n".join(lines)

    def _compress_log_output(self, content: str) -> str:
        lines = content.splitlines()
        if len(lines) <= 50:
            return content
        selected: list[tuple[int, str]] = []
        for index, line in enumerate(lines):
            if index < 10 or index >= len(lines) - 10 or self._ERROR_RE.search(line):
                selected.append((index, line))
        selected = self._dedupe_indexed_lines(selected)[:100]
        omitted = max(len(lines) - len(selected), 0)
        counts = {
            "ERROR": sum(1 for line in lines if re.search(r"\b(error|failed|failure|exception)\b", line, re.I)),
            "WARN": sum(1 for line in lines if re.search(r"\b(warn|warning)\b", line, re.I)),
        }
        body = [line for _, line in selected]
        if omitted:
            body.append(f"[{omitted} lines omitted: {counts['ERROR']} ERROR, {counts['WARN']} WARN]")
        return "\n".join(body)

    @staticmethod
    def _compress_diff(content: str) -> str:
        lines = content.splitlines()
        if len(lines) <= 50:
            return content
        kept: list[str] = []
        for line in lines:
            if line.startswith(("diff --git", "index ", "--- ", "+++ ", "@@", "+", "-")):
                kept.append(line)
        omitted = len(lines) - len(kept)
        if omitted > 0:
            kept.append(f"[{omitted} unchanged/context diff lines omitted]")
        return "\n".join(kept)

    @staticmethod
    def _extract_html_text(content: str) -> str:
        parser = _HTMLTextExtractor()
        parser.feed(content)
        text = "\n".join(line.strip() for line in parser.text.splitlines() if line.strip())
        return text or content

    @staticmethod
    def _compress_plain_text(content: str) -> str:
        seen: set[str] = set()
        lines: list[str] = []
        for line in content.splitlines():
            normalized = " ".join(line.split())
            if normalized and normalized not in seen:
                seen.add(normalized)
                lines.append(normalized)
            elif not normalized and (not lines or lines[-1] != ""):
                lines.append("")
        return "\n".join(lines).strip()

    def _keep_first_last_and_errors(self, lines: list[str], max_lines: int) -> list[str]:
        if len(lines) <= max_lines:
            return lines
        selected: list[str] = []
        for line in lines:
            if len(selected) >= max_lines:
                break
            if self._ERROR_RE.search(line):
                selected.append(line)
        selected.extend(lines[:2])
        selected.extend(lines[-2:])
        deduped = []
        for line in selected:
            if line not in deduped:
                deduped.append(line)
        return deduped[:max_lines]

    @staticmethod
    def _dedupe_indexed_lines(lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
        seen: set[int] = set()
        deduped: list[tuple[int, str]] = []
        for index, line in sorted(lines, key=lambda item: item[0]):
            if index in seen:
                continue
            seen.add(index)
            deduped.append((index, line))
        return deduped

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(len(text) // 3, 1)


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._parts: list[str] = []

    @property
    def text(self) -> str:
        return " ".join(self._parts)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "nav", "footer"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "nav", "footer"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and data.strip():
            self._parts.append(data.strip())
