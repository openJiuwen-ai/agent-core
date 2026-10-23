"""Finite repair diagnostics shared by runtime and standalone generation."""

from __future__ import annotations

from types import MappingProxyType


REPAIR_DIAGNOSTIC_ID = "bounded_schema_context_repair_2"
REPAIR_DIAGNOSTICS = MappingProxyType(
    {
        "json": "The response was not a JSON object. Return a fresh complete JSON object satisfying the same task and output constraints.",
        "schema": "The response did not satisfy the required fields or field types. Return a fresh complete JSON object with only answer and explanation strings; answer must be nonempty and explanation may be empty. Do not copy input fields.",
        "role-schema": 'The role response did not satisfy the required fields or field types. Return a fresh JSON object with only status ("continue" or "complete") and nonempty feedback. Do not copy answer, explanation, query, metadata, or the incoming object; do not revise the answer.',
        "admissible-answer": "The answer was not an admissible selection. Return a fresh complete JSON object using an answer permitted by the same task and output constraints.",
        "truncation": "The response was truncated. Return a shorter fresh complete JSON object satisfying the same task and output constraints; do not continue the truncated text.",
    }
)


def select_repair_diagnostic(error: str | None, *, context_invalid: bool = False, kind: str | None = None) -> str:
    """Classify a recorded error without copying any variable error content."""

    text = (error or "").casefold()
    if "truncat" in text or "output-token-limit" in text:
        key = "truncation"
    elif context_invalid and kind != "role":
        key = "admissible-answer"
    elif "not a json object" in text or "invalid json" in text or "json decode" in text:
        key = "json"
    else:
        key = "role-schema" if kind == "role" else "schema"
    return REPAIR_DIAGNOSTICS[key]


def render_repair(original_prompt: str, diagnostic: str) -> str:
    """Append only a known bounded diagnostic to the unchanged original body."""

    if diagnostic not in REPAIR_DIAGNOSTICS.values():
        raise ValueError("repair diagnostic must come from the fixed catalogue")
    return original_prompt + f"\n\nRepair diagnostic ({REPAIR_DIAGNOSTIC_ID}):\n" + diagnostic


def repair_variants(original_prompt: str, *, kind: str | None = None) -> tuple[str, ...]:
    """The component's finite catalogue, including the empty diagnostic, for admission."""

    keys = (
        ("json", "role-schema", "truncation")
        if kind == "role"
        else ("json", "schema", "admissible-answer", "truncation")
    )
    return (original_prompt, *(render_repair(original_prompt, REPAIR_DIAGNOSTICS[key]) for key in keys))
