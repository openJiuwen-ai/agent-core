from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from tree_sitter_language_pack import get_parser

from openjiuwen.core.context_engine.processor.offloader.rule_compression.common import meets_savings_ratio
from openjiuwen.core.context_engine.processor.offloader.rule_compression.types import (
    ContentType,
    RuleCompressionResult,
    RuleContext,
)


@dataclass(frozen=True)
class LanguageConfig:
    function_nodes: frozenset[str]
    body_nodes: frozenset[str]
    comment_prefix: str
    python_style: bool = False


@dataclass(frozen=True)
class NumberedSourceLine:
    prefix: str
    code: str
    line_number: int


@dataclass
class ReplacementCollector:
    content: bytes
    config: LanguageConfig
    ctx: RuleContext
    replacements: list[tuple[int, int, bytes]]
    stats: dict[str, int]
    row_replacements: list[tuple[int, int]] | None = None


_LANGUAGES: dict[str, LanguageConfig] = {
    "python": LanguageConfig(frozenset({"function_definition"}), frozenset({"block"}), "#", True),
    "javascript": LanguageConfig(
        frozenset({"function_declaration", "method_definition", "arrow_function"}),
        frozenset({"statement_block"}),
        "//",
    ),
    "typescript": LanguageConfig(
        frozenset({"function_declaration", "method_definition", "arrow_function"}),
        frozenset({"statement_block"}),
        "//",
    ),
    "go": LanguageConfig(
        frozenset({"function_declaration", "method_declaration"}),
        frozenset({"block"}),
        "//",
    ),
    "rust": LanguageConfig(frozenset({"function_item"}), frozenset({"block"}), "//"),
    "java": LanguageConfig(
        frozenset({"method_declaration", "constructor_declaration"}),
        frozenset({"block"}),
        "//",
    ),
    "c": LanguageConfig(frozenset({"function_definition"}), frozenset({"compound_statement"}), "//"),
    "cpp": LanguageConfig(frozenset({"function_definition"}), frozenset({"compound_statement"}), "//"),
}
_DETECTION_PATTERNS = {
    "typescript": (r"\binterface\s+\w+", r":\s*(?:string|number|boolean|Promise)\b"),
    "python": (r"(?m)^\s*(?:async\s+)?def\s+\w+", r"(?m)^\s*from\s+\w+\s+import\s+"),
    "go": (r"(?m)^\s*package\s+\w+", r"(?m)^\s*func\s+"),
    "rust": (r"(?m)^\s*(?:pub\s+)?fn\s+\w+", r"(?m)^\s*(?:use|impl|struct)\s+"),
    "java": (r"(?m)^\s*(?:public|private|protected)\s+(?:class|interface)\s+", r"(?m)^\s*package\s+[\w.]+;"),
    "cpp": (r"\bnamespace\s+\w+|std::|::\w+", r"(?m)^\s*class\s+\w+"),
    "c": (r"#include\s*[<\"]", r"(?m)^\s*(?:int|void|char|float|double)\s+\w+\s*\("),
    "javascript": (r"(?m)^\s*(?:export\s+)?function\s+\w+", r"(?m)^\s*(?:const|let|var)\s+\w+"),
}


class SourceCodeCompressor:
    @staticmethod
    def compress(content: str, ctx: RuleContext) -> RuleCompressionResult:
        numbered_lines = _numbered_source_lines(content)
        if numbered_lines is not None:
            return _compress_numbered_source(content, numbered_lines, ctx)

        if len(content.splitlines()) < ctx.source_min_lines:
            return _unchanged(content)
        language = _detect_language(content)
        if language is None:
            return _unchanged(content)

        parser = get_parser(language)
        tree = parser.parse(content)
        root = tree.root_node()
        if _has_syntax_error(root):
            return _unchanged(content)

        config = _LANGUAGES[language]
        content_bytes = content.encode("utf-8")
        replacements: list[tuple[int, int, bytes]] = []
        stats = {"bodies_seen": 0, "bodies_compressed": 0, "query_protected_bodies": 0}
        _collect_replacements(root, ReplacementCollector(content_bytes, config, ctx, replacements, stats))
        if not replacements:
            return _unchanged(content, language=language, details={**stats, "syntax_valid": True})

        candidate_bytes = content_bytes
        for start, end, replacement in sorted(replacements, reverse=True):
            candidate_bytes = candidate_bytes[:start] + replacement + candidate_bytes[end:]
        candidate = candidate_bytes.decode("utf-8")

        candidate_tree = parser.parse(candidate)
        syntax_valid = _candidate_syntax_valid(language, candidate_tree.root_node(), candidate)
        details: dict[str, Any] = {
            "language": language,
            **stats,
            "syntax_valid": syntax_valid,
        }
        if syntax_valid and candidate != content and meets_savings_ratio(content, candidate, ctx):
            return RuleCompressionResult(
                content=candidate,
                content_type=ContentType.SOURCE_CODE,
                modified=True,
                lossy=True,
                details=details,
            )
        return RuleCompressionResult(
            content=content,
            content_type=ContentType.SOURCE_CODE,
            modified=False,
            lossy=False,
            details=details,
        )


def _compress_numbered_source(
    original: str,
    numbered_lines: list[NumberedSourceLine],
    ctx: RuleContext,
) -> RuleCompressionResult:
    clean_content = "\n".join(line.code for line in numbered_lines)
    if len(numbered_lines) < ctx.source_min_lines:
        return _unchanged_numbered(original)
    language = _detect_language(clean_content)
    if language is None:
        return _unchanged_numbered(original)

    parser = get_parser(language)
    tree = parser.parse(clean_content)
    root = tree.root_node()
    if _has_syntax_error(root):
        return _unchanged_numbered(original)

    config = _LANGUAGES[language]
    clean_bytes = clean_content.encode("utf-8")
    replacements: list[tuple[int, int, bytes]] = []
    row_replacements: list[tuple[int, int]] = []
    stats = {"bodies_seen": 0, "bodies_compressed": 0, "query_protected_bodies": 0}
    _collect_replacements(
        root,
        ReplacementCollector(
            clean_bytes,
            config,
            ctx,
            replacements,
            stats,
            row_replacements,
        ),
    )
    details: dict[str, Any] = {
        "language": language,
        **stats,
        "syntax_valid": True,
        "line_numbers_preserved": True,
    }
    if not replacements:
        return _unchanged_numbered(original, language=language, details=details)

    clean_candidate_bytes = clean_bytes
    for start, end, replacement in sorted(replacements, reverse=True):
        clean_candidate_bytes = clean_candidate_bytes[:start] + replacement + clean_candidate_bytes[end:]
    clean_candidate = clean_candidate_bytes.decode("utf-8")
    syntax_valid = _candidate_syntax_valid(language, parser.parse(clean_candidate).root_node(), clean_candidate)
    details["syntax_valid"] = syntax_valid
    if not syntax_valid:
        return RuleCompressionResult(
            content=original,
            content_type=ContentType.SOURCE_CODE,
            modified=False,
            lossy=False,
            details=details,
        )

    candidate = _apply_numbered_row_replacements(numbered_lines, row_replacements, config)
    if candidate != original and meets_savings_ratio(original, candidate, ctx):
        return RuleCompressionResult(
            content=candidate,
            content_type=ContentType.SOURCE_CODE,
            modified=True,
            lossy=True,
            details=details,
        )
    return RuleCompressionResult(
        content=original,
        content_type=ContentType.SOURCE_CODE,
        modified=False,
        lossy=False,
        details=details,
    )


def _term_matches_function(term: str, function_text: str) -> bool:
    """Return True when term appears as a whole word in the function body.

    Substring matching (``term in text``) over-protects: ``type`` matched
    ``# type: ignore`` and ``isinstance(x, type)`` everywhere. Whole-word
    matching via ``\\b`` boundaries keeps relevance signals while avoiding
    incidental hits.
    """
    pattern = r"\b{}\b".format(re.escape(term))
    return bool(re.search(pattern, function_text.lower()))


def _collect_replacements(node: Any, collector: ReplacementCollector) -> None:
    if _kind(node) in collector.config.function_nodes:
        body = _body_node(node, collector.config)
        if body is None:
            return
        collector.stats["bodies_seen"] += 1
        function_text = collector.content[_start(node):_end(node)].decode("utf-8", errors="replace")
        query_terms = {term.lower() for term in collector.ctx.query_terms if term}
        if any(_term_matches_function(term, function_text) for term in query_terms):
            collector.stats["query_protected_bodies"] += 1
            return
        body_lines = _position_row(_end_position(body)) - _position_row(_start_position(body)) + 1
        if body_lines <= collector.ctx.source_max_body_lines:
            return
        collector.replacements.append(
            (_start(body), _end(body), _omission_body(body, collector.content, collector.config))
        )
        if collector.row_replacements is not None:
            collector.row_replacements.append(
                (
                    _position_row(_start_position(body)),
                    _position_row(_end_position(body)),
                )
            )
        collector.stats["bodies_compressed"] += 1
        return
    for child in _named_children(node):
        _collect_replacements(child, collector)


def _apply_numbered_row_replacements(
    numbered_lines: list[NumberedSourceLine],
    row_replacements: list[tuple[int, int]],
    config: LanguageConfig,
) -> str:
    replacements = sorted(row_replacements, reverse=True)
    output = list(numbered_lines)
    for start_row, end_row in replacements:
        if start_row < 0 or end_row >= len(output) or start_row > end_row:
            continue
        first = output[start_row]
        last = output[end_row]
        indent = re.match(r"[ \t]*", first.code).group(0)
        marker = (
            f"{indent}{config.comment_prefix} [function body omitted; original lines "
            f"{first.line_number}-{last.line_number}, reload original source for details]"
        )
        output[start_row:end_row + 1] = [
            NumberedSourceLine(prefix=first.prefix, code=marker, line_number=first.line_number)
        ]
    return "\n".join(f"{line.prefix}{line.code}" for line in output)


def _body_node(node: Any, config: LanguageConfig) -> Any | None:
    body = node.child_by_field_name("body")
    if body is not None and _kind(body) in config.body_nodes:
        return body
    return next((child for child in _named_children(node) if _kind(child) in config.body_nodes), None)


def _omission_body(body: Any, content: bytes, config: LanguageConfig) -> bytes:
    marker = f"{config.comment_prefix} [function body omitted; reload original source for details]".encode()
    start = _start(body)
    end = _end(body)
    original = content[start:end]
    if config.python_style:
        line_start = content.rfind(b"\n", 0, start) + 1
        indentation = content[line_start:start]
        if indentation == b"":
            indentation = re.match(rb"[ \t]*", original).group(0)
        return indentation + marker + b"\n" + indentation + b"pass"

    opening = original[:1]
    closing = original[-1:] if original else b""
    line_start = content.rfind(b"\n", 0, start) + 1
    base_indent = re.match(rb"[ \t]*", content[line_start:start]).group(0)
    inner_indent = base_indent + b"    "
    return opening + b"\n" + inner_indent + marker + b"\n" + base_indent + closing


def _detect_language(content: str) -> str | None:
    candidates: list[str] = []
    for language, patterns in _DETECTION_PATTERNS.items():
        if any(re.search(pattern, content) for pattern in patterns):
            candidates.append(language)
    best: tuple[int, int, str] | None = None
    for rank, language in enumerate(candidates):
        try:
            root = get_parser(language).parse(content).root_node()
        except (TypeError, ValueError):
            continue
        errors = _count_syntax_errors(root)
        score = (errors, rank, language)
        if best is None or score < best:
            best = score
    return best[2] if best is not None else None


def _count_syntax_errors(node: Any) -> int:
    count = 0
    stack = [node]
    while stack:
        current = stack.pop()
        count += int(bool(current.is_error())) + int(bool(current.is_missing()))
        stack.extend(_named_children(current))
    return count


def _has_syntax_error(node: Any) -> bool:
    return bool(node.has_error()) or _count_syntax_errors(node) > 0


def _candidate_syntax_valid(language: str, root: Any, candidate: str) -> bool:
    if _has_syntax_error(root):
        return False
    if language == "python":
        try:
            compile(candidate, "<source-code-compressor>", "exec")
        except SyntaxError:
            return False
    return True


def _named_children(node: Any) -> list[Any]:
    named_child_count = getattr(node, "named_child_count")
    count = named_child_count() if callable(named_child_count) else named_child_count
    named_child = getattr(node, "named_child")
    return [named_child(index) for index in range(count)]


def _kind(node: Any) -> str:
    kind = getattr(node, "kind")
    return str(kind() if callable(kind) else kind)


def _start(node: Any) -> int:
    return int(node.start_byte())


def _end(node: Any) -> int:
    return int(node.end_byte())


def _start_position(node: Any) -> Any:
    return node.start_position()


def _end_position(node: Any) -> Any:
    return node.end_position()


def _position_row(point: Any) -> int:
    row = getattr(point, "row", None)
    return int(row() if callable(row) else row)


def _numbered_source_lines(content: str) -> list[NumberedSourceLine] | None:
    lines = content.splitlines()
    if not lines:
        return None
    numbered_lines: list[NumberedSourceLine] = []
    numbered_count = 0
    for line in lines:
        match = re.match(r"^(?P<prefix>\s*(?P<number>\d+)\t)(?P<code>.*)$", line)
        if match is None:
            numbered_lines.append(NumberedSourceLine(prefix="", code=line, line_number=0))
            continue
        numbered_count += 1
        numbered_lines.append(
            NumberedSourceLine(
                prefix=match.group("prefix"),
                code=match.group("code"),
                line_number=int(match.group("number")),
            )
        )
    if numbered_count < 2 or numbered_count / len(lines) < 0.8:
        return None
    return numbered_lines


def _unchanged_numbered(
    content: str,
    *,
    language: str | None = None,
    details: dict[str, Any] | None = None,
) -> RuleCompressionResult:
    numbered_details = {
        "bodies_seen": 0,
        "bodies_compressed": 0,
        "query_protected_bodies": 0,
        "line_numbers_preserved": True,
    }
    numbered_details.update(details or {})
    return _unchanged(content, language=language, details=numbered_details)


def _unchanged(
    content: str,
    *,
    language: str | None = None,
    details: dict[str, Any] | None = None,
) -> RuleCompressionResult:
    result_details = details
    if language is not None:
        result_details = {"language": language, **(details or {})}
    return RuleCompressionResult(
        content=content,
        content_type=ContentType.SOURCE_CODE,
        modified=False,
        details=result_details,
    )
