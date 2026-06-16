from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from tree_sitter_language_pack import get_parser

from openjiuwen.core.context_engine.processor.offloader.rules.common import meets_savings_ratio
from openjiuwen.core.context_engine.processor.offloader.rules.types import (
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
    def compress(self, content: str, ctx: RuleContext) -> RuleCompressionResult:
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
        _collect_replacements(root, content_bytes, config, ctx, replacements, stats)
        if not replacements:
            return _unchanged(content, language=language, details={**stats, "syntax_valid": True})

        candidate_bytes = content_bytes
        for start, end, replacement in sorted(replacements, reverse=True):
            candidate_bytes = candidate_bytes[:start] + replacement + candidate_bytes[end:]
        candidate = candidate_bytes.decode("utf-8")

        candidate_tree = parser.parse(candidate)
        syntax_valid = not _has_syntax_error(candidate_tree.root_node())
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


def _collect_replacements(
    node: Any,
    content: bytes,
    config: LanguageConfig,
    ctx: RuleContext,
    replacements: list[tuple[int, int, bytes]],
    stats: dict[str, int],
) -> None:
    if _kind(node) in config.function_nodes:
        body = _body_node(node, config)
        if body is None:
            return
        stats["bodies_seen"] += 1
        function_text = content[_start(node):_end(node)].decode("utf-8", errors="replace")
        if any(term in function_text.lower() for term in ctx.query_terms):
            stats["query_protected_bodies"] += 1
            return
        body_lines = _position_row(_end_position(body)) - _position_row(_start_position(body)) + 1
        if body_lines <= ctx.source_max_body_lines:
            return
        replacements.append((_start(body), _end(body), _omission_body(body, content, config)))
        stats["bodies_compressed"] += 1
        return
    for child in _named_children(node):
        _collect_replacements(child, content, config, ctx, replacements, stats)


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
        if not indentation.strip():
            indentation = re.match(rb"[ \t]*", original).group(0)
        return indentation + marker + b"\n" + indentation + b"pass"

    opening = original[:1]
    closing = original[-1:] if original else b""
    line_start = content.rfind(b"\n", 0, start) + 1
    base_indent = re.match(rb"[ \t]*", content[line_start:start]).group(0)
    inner_indent = base_indent + b"    "
    return opening + b"\n" + inner_indent + marker + b"\n" + base_indent + closing


def _detect_language(content: str) -> str | None:
    candidates = [
        language
        for language, patterns in _DETECTION_PATTERNS.items()
        if any(re.search(pattern, content) for pattern in patterns)
    ]
    best: tuple[int, int, str] | None = None
    for rank, language in enumerate(candidates):
        try:
            root = get_parser(language).parse(content).root_node()
        except Exception:
            continue
        errors = _count_syntax_errors(root)
        score = (errors, rank, language)
        if best is None or score < best:
            best = score
    return best[2] if best is not None else None


def _count_syntax_errors(node: Any) -> int:
    count = int(bool(node.is_error())) + int(bool(node.is_missing()))
    return count + sum(_count_syntax_errors(child) for child in _named_children(node))


def _has_syntax_error(node: Any) -> bool:
    return bool(node.has_error()) or _count_syntax_errors(node) > 0


def _named_children(node: Any) -> list[Any]:
    return [node.named_child(index) for index in range(node.named_child_count())]


def _kind(node: Any) -> str:
    return str(node.kind())


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
