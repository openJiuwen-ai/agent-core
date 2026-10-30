# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
import ntpath
import re
from collections.abc import Mapping

from openjiuwen.core.context_engine.processor.forked.offloader.rule_compression.types import RuleContext

# Argument keys whose string value names the single file a tool call reads.
_FILE_PATH_ARGUMENT_KEYS = ("file_path", "filepath", "file", "filename", "path")

# File types whose content the model needs verbatim: program source, markup and
# templates, configuration, and prose documents. Content sniffing misroutes
# these (JSX tags look like HTML, a line starting with ``Error:`` or ``Running``
# looks like a log), and every rule compressor is lossy on them. Data files that
# the compressors are designed for (.json, .jsonl, .log, .txt, .csv, .diff,
# .patch) are deliberately absent so they keep content-based routing.
_SOURCE_FILE_EXTENSIONS = frozenset(
    {
        # Program source.
        ".py", ".pyi", ".pyx", ".ipynb",
        ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts",
        ".go", ".rs", ".java", ".kt", ".kts", ".scala", ".groovy", ".gradle",
        ".c", ".h", ".cc", ".cpp", ".cxx", ".hh", ".hpp", ".hxx", ".m", ".mm",
        ".cs", ".fs", ".vb", ".swift", ".dart", ".rb", ".php", ".pl", ".pm",
        ".lua", ".r", ".jl", ".ex", ".exs", ".erl", ".hs", ".clj", ".elm",
        ".zig", ".nim", ".v", ".sol", ".proto", ".thrift", ".graphql", ".gql",
        ".sql", ".sh", ".bash", ".zsh", ".fish", ".ps1", ".psm1", ".bat", ".cmd",
        # Markup, styles and templates.
        ".html", ".htm", ".xhtml", ".xml", ".xsd", ".xsl", ".svg",
        ".vue", ".svelte", ".astro", ".jinja", ".j2", ".hbs", ".ejs", ".erb",
        ".css", ".scss", ".sass", ".less", ".styl",
        # Configuration and build definitions.
        ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".properties",
        ".env", ".tf", ".hcl", ".nix", ".cmake", ".mk", ".bzl",
        # Prose documents.
        ".md", ".mdx", ".markdown", ".rst", ".adoc", ".tex",
    }
)
# Extensionless file names that are source by convention.
_SOURCE_FILE_NAMES = frozenset(
    {
        "makefile", "gnumakefile", "dockerfile", "containerfile", "jenkinsfile",
        "vagrantfile", "gemfile", "rakefile", "podfile", "cmakelists.txt",
    }
)

ERROR_RE = re.compile(
    r"\b(error|failed|failure|traceback|exception|warn|warning)\b",
    re.IGNORECASE,
)
_DISPLAY_LINE_PREFIX_RE = re.compile(
    r"(?m)^(?P<prefix>\s*(?:"
    r"\d+[\t ]+|"
    r"[|>:#-]\s*\d+\s*[|:.)\]-]?\s*|"
    r"(?:line|row)\s+\d+\s*[:|.)\]-]\s*"
    r"))(?P<body>\S.*)$",
    re.IGNORECASE,
)
_DISPLAY_LINE_PREFIX_PRESERVE_WS_RE = re.compile(
    r"(?m)^(?P<prefix>\s*(?:"
    r"\d+\t|"
    r"\d+ +|"
    r"[|>:#-]\s*\d+\s*[|:.)\]-]?\s*|"
    r"(?:line|row)\s+\d+\s*[:|.)\]-]\s*"
    r"))(?P<body>.*\S.*)$",
    re.IGNORECASE,
)


def count_tokens(text: str, ctx: RuleContext) -> int:
    if ctx.count_tokens is not None:
        return max(ctx.count_tokens(text), 1)
    return max(len(text) // 3, 1)


def meets_savings_ratio(original: str, candidate: str, ctx: RuleContext) -> bool:
    original_tokens = count_tokens(original, ctx)
    candidate_tokens = count_tokens(candidate, ctx)
    if original_tokens <= 0:
        return False
    return 1 - candidate_tokens / original_tokens >= ctx.min_savings_ratio


def fits_budget_and_saves(original: str, candidate: str, ctx: RuleContext) -> bool:
    if count_tokens(candidate, ctx) > ctx.max_tokens:
        return False
    return meets_savings_ratio(original, candidate, ctx)


def extract_file_path_argument(tool_arguments: object | None) -> str | None:
    """Return the file path a tool call names in its arguments.

    Args:
        tool_arguments: Tool call arguments, either a mapping or its JSON string.

    Returns:
        The first non-empty string under a known file-path key, or None when the
        arguments name no single file.
    """
    arguments = tool_arguments
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return None
    if not isinstance(arguments, Mapping):
        return None
    for key in _FILE_PATH_ARGUMENT_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def is_source_file_path(file_path: str) -> bool:
    """Return whether a path names a source, markup, config or document file.

    Args:
        file_path: POSIX or Windows file path.

    Returns:
        True when the file type must be kept verbatim instead of rule-compressed.
    """
    # ntpath splits on both "/" and "\\", so one call covers every platform.
    file_name = ntpath.basename(file_path).lower()
    if file_name in _SOURCE_FILE_NAMES:
        return True
    extension = ntpath.splitext(file_name)[1]
    if extension:
        return extension in _SOURCE_FILE_EXTENSIONS
    # Dotfiles such as ".env" or ".bashrc" have no splitext extension.
    return file_name in _SOURCE_FILE_EXTENSIONS


def strip_display_line_prefixes(content: str) -> str:
    """Remove line-display prefixes added by tools such as read_file or grep."""
    return _strip_display_line_prefixes(content, preserve_body_whitespace=False)


def strip_display_line_prefixes_preserving_body_whitespace(content: str) -> str:
    """Remove display prefixes while preserving leading body whitespace."""
    return _strip_display_line_prefixes(content, preserve_body_whitespace=True)


def _strip_display_line_prefixes(content: str, *, preserve_body_whitespace: bool) -> str:
    if not content:
        return content
    pattern = _DISPLAY_LINE_PREFIX_PRESERVE_WS_RE if preserve_body_whitespace else _DISPLAY_LINE_PREFIX_RE
    matches = list(pattern.finditer(content))
    lines = content.splitlines()
    non_empty_lines = [line for line in lines if line.strip()]
    if not matches or not non_empty_lines:
        return content
    if len(matches) / len(non_empty_lines) < 0.3:
        return content
    return pattern.sub(r"\g<body>", content)
