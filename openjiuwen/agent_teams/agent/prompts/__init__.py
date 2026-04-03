# coding: utf-8
"""Template loader for agent-teams policy prompts."""
from __future__ import annotations

from functools import cache
from pathlib import Path

from openjiuwen.core.foundation.prompt import PromptTemplate

_PROMPTS_DIR = Path(__file__).parent
_DEFAULT_LANGUAGE = "cn"


@cache
def _load(name: str, language: str | None = None) -> PromptTemplate:
    """Load a .md template, optionally from a language subdirectory."""
    if language:
        path = _PROMPTS_DIR / language / f"{name}.md"
    else:
        path = _PROMPTS_DIR / f"{name}.md"
    return PromptTemplate(name=name, content=path.read_text(encoding="utf-8"))


def load_template(name: str, language: str = _DEFAULT_LANGUAGE) -> PromptTemplate:
    """Load a language-specific template from ``<lang>/<name>.md``."""
    return _load(name, language)


def load_shared_template(name: str) -> PromptTemplate:
    """Load a language-independent template from ``<name>.md``."""
    return _load(name)
