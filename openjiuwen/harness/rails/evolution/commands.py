# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Host-facing command prompt builders for Skill evolution rails."""

from __future__ import annotations

import json
from typing import Any

from openjiuwen.agent_evolving.experience.draft_schema import normalize_subject


def format_relevant_index_preview(index_preview: dict[str, Any] | None) -> str:
    """Format a focused, non-complete index preview for evolve prompts."""
    if not index_preview or not index_preview.get("items"):
        return "Relevant Experience Index: empty."
    lines = ["Relevant Experience Index (not the full list):"]
    lines.extend(_format_index_lines(list(index_preview.get("items", []))))
    if index_preview.get("has_more"):
        lines.append("More records may be relevant; use list_skill_experiences for focused lookup.")
    return "\n".join(lines)


def format_complete_summary_index(index: dict[str, Any] | None, *, index_complete: bool) -> str:
    """Format a complete or explicitly partial summary index for simplify prompts."""
    if not index or not index.get("items"):
        return "Complete Experience Summary Index: empty."
    header = (
        "Complete Experience Summary Index:"
        if index_complete
        else "Partial Experience Summary Index (overflow exists):"
    )
    lines = [header]
    lines.extend(_format_index_lines(list(index.get("items", []))))
    if not index_complete or index.get("has_more"):
        lines.append(
            "This index is incomplete. Before submitting simplify actions, call list_skill_experiences "
            "until overflow is covered, then read selected details with read_skill_experiences(record_ids=[...])."
        )
    return "\n".join(lines)


def format_rebuild_context(rebuild_context: dict[str, Any] | None) -> str:
    """Format deterministic rebuild context for rebuild prompts."""
    if not rebuild_context:
        return "Deterministic Rebuild Context: empty."
    lines = ["Deterministic Rebuild Context:"]
    for item in rebuild_context.get("records", []):
        lines.append(
            "\n[{record_id}] {summary}\ntarget={target} section={section} score={score}\n{content}".format(
                record_id=item.get("record_id", ""),
                summary=item.get("summary", ""),
                target=item.get("target", ""),
                section=item.get("section", ""),
                score=item.get("score", ""),
                content=item.get("content", ""),
            )
        )
    overflow = rebuild_context.get("overflow_index") or {}
    if overflow.get("items"):
        lines.append("\nOverflow Summary Index:")
        lines.extend(_format_index_lines(list(overflow.get("items", []))))
        lines.append("Read overflow details only if they are needed for the rebuild.")
    return "\n".join(lines)


def build_simplify_command_prompt(
    *,
    subject: dict[str, Any],
    user_intent: str | None = None,
    full_index: dict[str, Any] | None = None,
    index_complete: bool = True,
    language: str = "cn",
) -> str:
    """Build a one-shot host prompt for active experience simplification."""
    del language
    normalized_subject = normalize_subject(subject).to_payload()
    intent = user_intent.strip() if user_intent else "Clean up duplicated, stale, or conflicting experiences."
    return (
        f"Please perform skill evolution operation `simplify` for subject "
        f"{json.dumps(normalized_subject, ensure_ascii=False)}.\n"
        f"Intent: {intent}\n"
        f"{format_complete_summary_index(full_index, index_complete=index_complete)}\n"
        "Read full content only for records you intend to delete, merge, refine, or use as merge evidence.\n"
        "Submit cleanup actions with simplify_skill_experiences. Do not edit skill files directly."
    )


def build_evolve_review_command_prompt(
    *,
    subject: dict[str, Any],
    user_intent: str | None = None,
    review_agent_name: str = "evolution_reviewer",
    language: str = "cn",
) -> str:
    """Build a host prompt that starts active evolve through restricted review."""
    del language
    normalized_subject = normalize_subject(subject).to_payload()
    del review_agent_name
    intent = user_intent.strip() if user_intent else ""
    subject_label = "Swarm Skill" if normalized_subject["kind"] == "swarm-skill" else "Skill"
    return (
        f"The user explicitly requested {subject_label} evolution and has confirmed review.\n"
        "Call prepare_skill_evolution with "
        f"subject={json.dumps(normalized_subject, ensure_ascii=False)}, "
        "user_confirmed=true, "
        f"user_intent={json.dumps(intent, ensure_ascii=False)}.\n"
        "Then delegate restricted review with evolve_review_task(evolution_review_ref=...) using the returned ref.\n"
        "The review agent must call submit_evolution_review before you submit a proposal selection.\n"
        "Read the validated review JSON from evolve_review_task.data.output, then submit "
        "proposal_selection_for_submission with evolve_skill_experiences. "
        "Do not construct or rewrite experiences."
    )


def build_rebuild_command_prompt(
    *,
    subject: dict[str, Any],
    user_intent: str | None = None,
    rebuild_context: dict[str, Any] | None = None,
    language: str = "cn",
) -> str:
    """Build a one-shot host prompt for Skill rebuild."""
    del language
    normalized_subject = normalize_subject(subject).to_payload()
    intent = user_intent.strip() if user_intent else "Rebuild the skill using the deterministic context."
    creator = "swarmskill-creator" if normalized_subject["kind"] == "swarm-skill" else "skill-creator"
    min_score = rebuild_context.get("min_score") if rebuild_context else None
    min_score_line = f"Min score threshold: {min_score}\n" if min_score is not None else ""
    return (
        f"Please perform skill evolution operation `evolve_rebuild` for subject "
        f"{json.dumps(normalized_subject, ensure_ascii=False)}.\n"
        f"Intent: {intent}\n"
        f"{min_score_line}"
        "Old version has been archived. Create the rebuilt skill body from the deterministic context below.\n"
        f"{format_rebuild_context(rebuild_context)}\n"
        f"Use {creator} for the rebuild and reset evolutions.json to an empty list.\n"
        "Use list_skill_experiences/read_skill_experiences only for overflow records that are not already inlined.\n"
        "Do not submit evolve_skill_experiences or simplify_skill_experiences for this rebuild."
    )


def _format_index_lines(items: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in items:
        lines.append(
            (
                "- {record_id} | {summary} | target={target} | section={section} | "
                "score={score} | updated_at={updated_at}"
            ).format(
                record_id=item.get("record_id", ""),
                summary=item.get("summary", ""),
                target=item.get("target", ""),
                section=item.get("section", ""),
                score=item.get("score", ""),
                updated_at=item.get("updated_at", ""),
            )
        )
    return lines


__all__ = [
    "build_evolve_review_command_prompt",
    "build_rebuild_command_prompt",
    "build_simplify_command_prompt",
    "format_complete_summary_index",
    "format_rebuild_context",
    "format_relevant_index_preview",
]
