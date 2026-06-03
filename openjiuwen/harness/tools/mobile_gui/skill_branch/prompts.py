# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import List

from openjiuwen.harness.tools.mobile_gui.skill_branch.manifest import (
    SkillImageEntry,
    format_manifest_for_prompt,
)


def build_stage1_system_message(*, max_images: int) -> str:
    return f"""
You are Stage 1 of a temporary skill consultation branch for one Android GUI step.
Decide whether reference images from the skill documentation are needed before planner reasoning.

Rules:
- Do NOT return tool calls, coordinate actions, or read_file.
- The CURRENT device screenshot (attached) is authoritative for live UI state.
- Skill text and the image manifest are supplemental references only.
- Load reference images only when text plus the live screenshot are likely insufficient.
- Request at most {max_images} images total.

Output format:
- Return ONLY one code block containing exactly one LOAD_SKILL_IMAGES({{...}}) call.
- JSON fields:
  - visual_reference_needed: true or false
  - why_not_text_only: explain the decision
  - requests: list of {{image_id, reason}} using exact image_id values from the manifest
- When visual_reference_needed is false, requests must be [].

Example without images:
```python
LOAD_SKILL_IMAGES({{
  "visual_reference_needed": false,
  "why_not_text_only": "The skill describes a stable menu path; the live screenshot is enough.",
  "requests": []
}})
```

Example with images:
```python
LOAD_SKILL_IMAGES({{
  "visual_reference_needed": true,
  "why_not_text_only": "The task depends on recognizing a specific UI layout shown in the skill figures.",
  "requests": [
    {{"image_id": "github_landing_page", "reason": "Need the expected repository landing layout."}}
  ]
}})
```
""".strip()


def build_stage1_user_message(
    *,
    instruction: str,
    skill_name: str,
    skill_text: str,
    manifest: List[SkillImageEntry],
    previous_steps: str = "",
) -> str:
    manifest_text = format_manifest_for_prompt(manifest)
    sections = [
        "Decide whether to load skill reference images for this step.",
        f"User instruction: {instruction}",
        "Previous steps (last N assistant steps with tool calls/results only; task is User instruction above):",
        previous_steps or "(no previous steps)",
        f"Skill name: {skill_name}",
        "Available reference images (image_id, alt, path):",
        manifest_text,
        "Skill markdown (text only):",
        skill_text,
    ]
    return "\n\n".join(sections)


def build_stage2_system_message() -> str:
    return """
You are Stage 2 of a temporary skill consultation branch for one Android GUI step.
Return a structured planner summary for the CURRENT device state. Do NOT return GUI actions.

Rules:
- The CURRENT device screenshot is authoritative.
- Skill text and any loaded reference images are documentation only, never coordinate templates.
- If Stage 1 chose no visual references, do not invent image-based assumptions.
- Reference images show example states, not the live device screen.

Output format:
- Return ONLY one code block with a single JSON object containing:
  - skill_applicability: effective | ineffective | uncertain
  - subgoal: short local milestone
  - plan: 2-4 key actions/checks grounded in the current screen
  - do_not_do: likely wrong path to avoid
  - fallback_if_no_progress: concrete alternate route
  - expected_state: visible cues the main agent should aim for next
  - completion_scope: local_only | needs_verification | maybe_complete
""".strip()


def build_stage2_user_message(
    *,
    instruction: str,
    skill_name: str,
    skill_text: str,
    stage1_decision: str,
    selected_image_summary: str,
    previous_steps: str = "",
) -> str:
    return "\n\n".join(
        [
            "Return planner JSON only for the CURRENT screenshot.",
            f"User instruction: {instruction}",
            "Previous steps (last N assistant steps with tool calls/results only; task is User instruction above):",
            previous_steps or "(no previous steps)",
            f"Skill name: {skill_name}",
            f"Stage-1 decision: {stage1_decision}",
            f"Selected reference images: {selected_image_summary}",
            "Skill markdown:",
            skill_text,
        ]
    )
