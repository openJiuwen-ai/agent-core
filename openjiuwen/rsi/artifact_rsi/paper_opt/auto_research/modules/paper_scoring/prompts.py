"""Load paper-scoring system prompts from markdown files."""

from __future__ import annotations

from pathlib import Path

_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


def load_prompt(name: str) -> str:
    path = _PROMPT_DIR / name
    return path.read_text(encoding="utf-8").strip()


GENERAL_RUBRIC_PROMPT = load_prompt("rubric.md")
EXPERIMENT_RIGOR_PROMPT = load_prompt("experiment_rigor.md")
