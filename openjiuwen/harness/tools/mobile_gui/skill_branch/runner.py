# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import AssistantMessage, SystemMessage, UserMessage
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.harness.tools.mobile_gui.rails.multimodal_skill_read_rail import (
    REFERENCE_IMAGE_NOTE,
    get_mime_type,
)
from openjiuwen.harness.tools.mobile_gui.skill_branch.manifest import (
    SkillImageEntry,
    build_skill_image_manifest,
)
from openjiuwen.harness.tools.mobile_gui.skill_branch.parsers import (
    parse_load_skill_images_response,
    parse_planner_json_response,
)
from openjiuwen.harness.tools.mobile_gui.skill_branch.prompts import (
    build_stage1_system_message,
    build_stage1_user_message,
    build_stage2_system_message,
    build_stage2_user_message,
)


@dataclass
class BranchResult:
    success: bool
    planner: Optional[Dict[str, str]] = None
    error: Optional[str] = None
    stage1_decision: Optional[Dict[str, Any]] = None
    selected_image_ids: List[str] = field(default_factory=list)
    tool_message_body: str = ""


def _assistant_text(response: AssistantMessage) -> str:
    content = getattr(response, "content", "") or ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return str(content)


def _load_image_data_url(entry: SkillImageEntry) -> Optional[str]:
    path = Path(entry.abs_path)
    if not path.is_file():
        return None
    try:
        raw = path.read_bytes()
    except OSError as exc:
        logger.warning("[MultimodalSkillBranch] failed to read %s: %s", path, exc)
        return None
    mime = get_mime_type(path)
    b64 = base64.b64encode(raw).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def _build_live_screenshot_blocks(screenshot_b64: str) -> List[dict]:
    if not screenshot_b64:
        return [{"type": "text", "text": "(no live device screenshot available)"}]
    url = screenshot_b64
    if not url.startswith("data:"):
        url = f"data:image/jpeg;base64,{screenshot_b64}"
    return [
        {"type": "text", "text": "[CURRENT device screenshot — authoritative for live UI]"},
        {"type": "image_url", "image_url": {"url": url, "detail": "low"}},
    ]


def _build_reference_blocks(
    entries: List[SkillImageEntry],
    selection: Dict[str, Any],
) -> List[dict]:
    blocks: List[dict] = []
    request_by_id = {
        str(item.get("image_id", "")): item
        for item in (selection.get("requests") or [])
        if isinstance(item, dict)
    }
    for entry in entries:
        if entry.image_id not in request_by_id:
            continue
        reason = str(request_by_id[entry.image_id].get("reason", "") or "").strip()
        data_url = _load_image_data_url(entry)
        if not data_url:
            continue
        caption = entry.alt or entry.image_id
        blocks.append(
            {
                "type": "text",
                "text": "\n".join(
                    [
                        REFERENCE_IMAGE_NOTE.format(caption=caption),
                        f"Selection reason: {reason or '(none)'}",
                    ]
                ),
            }
        )
        blocks.append(
            {"type": "image_url", "image_url": {"url": data_url, "detail": "low"}}
        )
    return blocks


async def run_skill_branch(
    model: Model,
    *,
    instruction: str,
    skill_name: str,
    skill_text: str,
    skill_directory: str,
    live_screenshot_b64: str = "",
    previous_steps: str = "",
    max_images: int = 4,
    max_stage1_rounds: int = 2,
    max_stage2_rounds: int = 2,
) -> BranchResult:
    """Run two-stage skill branch (image gate + planner JSON)."""
    manifest = build_skill_image_manifest(skill_text, skill_directory)
    if not manifest:
        return BranchResult(
            success=False,
            error="Skill has no resolvable local reference images.",
        )

    valid_ids = {entry.image_id for entry in manifest}
    stage1_feedback: List[str] = []
    stage1_decision: Optional[Dict[str, Any]] = None

    for round_idx in range(max_stage1_rounds):
        user_parts = build_stage1_user_message(
            instruction=instruction,
            skill_name=skill_name,
            skill_text=skill_text,
            manifest=manifest,
            previous_steps=previous_steps,
        )
        if stage1_feedback:
            user_parts += "\n\nFeedback:\n" + "\n".join(f"- {f}" for f in stage1_feedback)

        messages: List[Any] = [
            SystemMessage(content=build_stage1_system_message(max_images=max_images)),
            UserMessage(
                content=[
                    {"type": "text", "text": user_parts},
                    *_build_live_screenshot_blocks(live_screenshot_b64),
                ]
            ),
        ]
        try:
            response = await model.invoke(messages)
        except Exception as exc:
            logger.error("[MultimodalSkillBranch] Stage 1 model error: %s", exc)
            return BranchResult(success=False, error=f"Stage 1 failed: {exc}")

        parsed, err = parse_load_skill_images_response(
            _assistant_text(response),
            valid_image_ids=valid_ids,
            max_images=max_images,
        )
        if err:
            stage1_feedback.append(err)
            continue
        stage1_decision = parsed
        break
    else:
        return BranchResult(
            success=False,
            error=stage1_feedback[-1] if stage1_feedback else "Stage 1 failed.",
            stage1_decision=stage1_decision,
        )

    if stage1_decision is None:
        return BranchResult(
            success=False,
            error=stage1_feedback[-1] if stage1_feedback else "Stage 1 failed.",
        )

    selected_entries: List[SkillImageEntry] = []
    if stage1_decision.get("visual_reference_needed"):
        selected_ids = {
            str(item.get("image_id", ""))
            for item in stage1_decision.get("requests") or []
            if isinstance(item, dict)
        }
        selected_entries = [e for e in manifest if e.image_id in selected_ids]

    stage2_feedback: List[str] = []
    planner: Optional[Dict[str, str]] = None
    stage1_json = json.dumps(stage1_decision, ensure_ascii=False)
    selected_summary = (
        ", ".join(e.image_id for e in selected_entries) if selected_entries else "None"
    )

    for _ in range(max_stage2_rounds):
        user_parts = build_stage2_user_message(
            instruction=instruction,
            skill_name=skill_name,
            skill_text=skill_text,
            stage1_decision=stage1_json,
            selected_image_summary=selected_summary,
            previous_steps=previous_steps,
        )
        if stage2_feedback:
            user_parts += "\n\nFeedback:\n" + "\n".join(f"- {f}" for f in stage2_feedback)

        content_blocks: List[dict] = [
            {"type": "text", "text": user_parts},
            *_build_live_screenshot_blocks(live_screenshot_b64),
        ]
        content_blocks.extend(_build_reference_blocks(manifest, stage1_decision))

        messages = [
            SystemMessage(content=build_stage2_system_message()),
            UserMessage(content=content_blocks),
        ]
        try:
            response = await model.invoke(messages)
        except Exception as exc:
            logger.error("[MultimodalSkillBranch] Stage 2 model error: %s", exc)
            return BranchResult(
                success=False,
                error=f"Stage 2 failed: {exc}",
                stage1_decision=stage1_decision,
            )

        planner, err = parse_planner_json_response(_assistant_text(response))
        if err:
            stage2_feedback.append(err)
            continue
        break
    else:
        return BranchResult(
            success=False,
            error=stage2_feedback[-1] if stage2_feedback else "Stage 2 failed.",
            stage1_decision=stage1_decision,
            selected_image_ids=[e.image_id for e in selected_entries],
        )

    return BranchResult(
        success=True,
        planner=planner,
        stage1_decision=stage1_decision,
        selected_image_ids=[e.image_id for e in selected_entries],
    )
