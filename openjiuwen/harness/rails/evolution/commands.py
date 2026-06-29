# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Host-facing command prompt builders for Skill evolution rails."""

from __future__ import annotations

import json
from typing import Any

from openjiuwen.agent_evolving.experience.draft_schema import normalize_subject


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


def _format_rebuild_target_path(*, skills_base: str | None, skill_name: str) -> str:
    if skills_base and skills_base.strip():
        return f"{skills_base.strip().rstrip('/')}/{skill_name}/SKILL.md"
    return f"{{skills_base}}/{skill_name}/SKILL.md"


def _build_rebuild_workflow_instructions(
    *,
    creator: str,
    target_skill_md: str,
) -> str:
    creator_skill_md = f"{creator}/SKILL.md"
    return (
        "Required rebuild workflow (follow in order; chat-only drafts do NOT count as completion):\n"
        f"1. read_file `{creator_skill_md}` first to load {creator} guidance and output format.\n"
        f"2. read_file `{target_skill_md}` to capture the current SKILL.md YAML frontmatter "
        "(preserve name, description, and other frontmatter keys).\n"
        "3. Compose the rebuilt SKILL.md from the deterministic context below, preserved frontmatter, "
        f"and {creator} guidance.\n"
        f"4. write_file `{target_skill_md}` with the complete rebuilt body (overwrite the file).\n"
        "5. Confirm write_file succeeded before finishing. "
        "Do not paste the full draft in chat instead of writing to disk."
    )


def build_rebuild_command_prompt(
    *,
    subject: dict[str, Any],
    user_intent: str | None = None,
    rebuild_context: dict[str, Any] | None = None,
    skills_base: str | None = None,
) -> str:
    """Build a one-shot host prompt for Skill rebuild."""
    normalized_subject = normalize_subject(subject).to_payload()
    skill_name = normalized_subject["name"]
    intent = user_intent.strip() if user_intent else "Rebuild the skill using the deterministic context."
    creator = "swarmskill-creator" if normalized_subject["kind"] == "swarm-skill" else "skill-creator"
    min_score = rebuild_context.get("min_score") if rebuild_context else None
    min_score_line = f"Min score threshold: {min_score}\n" if min_score is not None else ""
    resolved_skills_base = skills_base
    if not resolved_skills_base and rebuild_context:
        raw_base = rebuild_context.get("skills_base")
        if isinstance(raw_base, str) and raw_base.strip():
            resolved_skills_base = raw_base.strip()
    target_skill_md = _format_rebuild_target_path(
        skills_base=resolved_skills_base,
        skill_name=skill_name,
    )
    skills_base_hint = ""
    if not resolved_skills_base:
        skills_base_hint = (
            "Resolve `{skills_base}` to the workspace skills root directory before read_file/write_file.\n"
        )
    return (
        f"Please perform skill evolution operation `evolve_rebuild` for subject "
        f"{json.dumps(normalized_subject, ensure_ascii=False)}.\n"
        f"Intent: {intent}\n"
        f"{min_score_line}"
        "Old version has been archived. Create the rebuilt skill body from the deterministic context below.\n"
        f"{format_rebuild_context(rebuild_context)}\n"
        f"Use {creator} for the rebuild.\n"
        f"{_build_rebuild_workflow_instructions(creator=creator, target_skill_md=target_skill_md)}\n"
        f"{skills_base_hint}"
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
    "build_rebuild_command_prompt",
    "format_rebuild_context",
]
