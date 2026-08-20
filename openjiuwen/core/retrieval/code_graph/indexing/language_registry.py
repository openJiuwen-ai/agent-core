# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""File-type → language mapping for Code Graph parsing."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path


class SourceLanguage(StrEnum):
    """Languages the Code Graph parser can attempt to index."""

    BASH = "bash"
    C = "c"
    CSHARP = "csharp"
    CPP = "cpp"
    CSS = "css"
    DOCKERFILE = "dockerfile"
    GO = "go"
    JAVA = "java"
    JAVASCRIPT = "javascript"
    KOTLIN = "kotlin"
    PHP = "php"
    PYTHON = "python"
    SQL = "sql"
    RUST = "rust"
    RUBY = "ruby"
    TYPESCRIPT = "typescript"
    HTML = "html"
    YAML = "yaml"
    XML = "xml"


_EXTENSION_TO_LANGUAGE: dict[str, SourceLanguage] = {
    ".sh": SourceLanguage.BASH,
    ".bash": SourceLanguage.BASH,
    ".c": SourceLanguage.C,
    ".h": SourceLanguage.C,
    ".cs": SourceLanguage.CSHARP,
    ".css": SourceLanguage.CSS,
    ".cpp": SourceLanguage.CPP,
    ".cc": SourceLanguage.CPP,
    ".cxx": SourceLanguage.CPP,
    ".hpp": SourceLanguage.CPP,
    ".go": SourceLanguage.GO,
    ".java": SourceLanguage.JAVA,
    ".js": SourceLanguage.JAVASCRIPT,
    ".jsx": SourceLanguage.JAVASCRIPT,
    ".mjs": SourceLanguage.JAVASCRIPT,
    ".kt": SourceLanguage.KOTLIN,
    ".kts": SourceLanguage.KOTLIN,
    ".php": SourceLanguage.PHP,
    ".py": SourceLanguage.PYTHON,
    ".pyi": SourceLanguage.PYTHON,
    ".sql": SourceLanguage.SQL,
    ".rs": SourceLanguage.RUST,
    ".rb": SourceLanguage.RUBY,
    ".ts": SourceLanguage.TYPESCRIPT,
    ".tsx": SourceLanguage.TYPESCRIPT,
    ".html": SourceLanguage.HTML,
    ".htm": SourceLanguage.HTML,
    ".yaml": SourceLanguage.YAML,
    ".yml": SourceLanguage.YAML,
    ".xml": SourceLanguage.XML,
}

TREE_SITTER_LANGUAGE_IDS: dict[SourceLanguage, str] = {
    SourceLanguage.BASH: "bash",
    SourceLanguage.C: "c",
    SourceLanguage.CSHARP: "csharp",
    SourceLanguage.CSS: "css",
    SourceLanguage.CPP: "cpp",
    SourceLanguage.DOCKERFILE: "dockerfile",
    SourceLanguage.GO: "go",
    SourceLanguage.JAVA: "java",
    SourceLanguage.JAVASCRIPT: "javascript",
    SourceLanguage.KOTLIN: "kotlin",
    SourceLanguage.PHP: "php",
    SourceLanguage.PYTHON: "python",
    SourceLanguage.SQL: "sql",
    SourceLanguage.RUST: "rust",
    SourceLanguage.RUBY: "ruby",
    SourceLanguage.TYPESCRIPT: "typescript",
    SourceLanguage.HTML: "html",
    SourceLanguage.YAML: "yaml",
    SourceLanguage.XML: "xml",
}


def language_from_path(path: Path) -> SourceLanguage | None:
    """Return the language for ``path``, or ``None`` if unsupported."""
    if path.name.lower() == "dockerfile":
        return SourceLanguage.DOCKERFILE
    return _EXTENSION_TO_LANGUAGE.get(path.suffix.lower())


def is_source_file(path: Path) -> bool:
    """True when Code Graph should attempt to parse ``path``."""
    return language_from_path(path) is not None
