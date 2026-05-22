# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import base64
import json
import os
import re
import urllib.parse
from copy import deepcopy
from pathlib import Path
from typing import Any, List, Literal, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage, UserMessage
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentRail,
    ModelCallInputs,
)
from openjiuwen.core.sys_operation.cwd import get_cwd


# -----------------------------------------------------------------------------
# Skill bundle / multimodal reference (mobile-only; not in react_agent / ReadFileTool)
# -----------------------------------------------------------------------------

MULTIMODAL_SKILL_USER_MESSAGE_NAME = "multimodal_skill"

USER_MESSAGES_PROTECTED_FROM_SCREENSHOT_ARCHIVE: frozenset[str] = frozenset(
    {
        MULTIMODAL_SKILL_USER_MESSAGE_NAME,
    },
)

# Short note on ``skill_tool`` text results (only when markdown embeds ``![]()``); idempotent per turn.
SKILL_TOOL_MARKDOWN_IMAGES_HINT = (
    "Embedded figures in this skill are markdown links (paths/URLs) only; pixel data is not "
    "attached. Call read_file on the image path under skills/<skill-name>/… when you need "
    "to inspect a reference screenshot."
)

REFERENCE_IMAGE_NOTE = (
    "[Skill reference image: {caption}]\n"
    "This is an example screenshot from the skill documentation, not the "
    "current device screen. Do not infer current coordinates, current app "
    "state, or visible text from it."
)

_IMAGE_LOADED_FROM_READ_FILE = re.compile(
    r"^Image loaded from read_file:\s*(.+?)\s*\Z",
    re.DOTALL,
)


def _parse_tool_call_arguments(arguments: Any) -> Optional[dict[str, Any]]:
    try:
        if isinstance(arguments, str):
            parsed = json.loads(arguments)
        else:
            parsed = arguments
        if isinstance(parsed, dict):
            return parsed
    except (TypeError, ValueError) as exc:
        logger.debug("[MultimodalSkillReadRail] invalid tool arguments: %s", exc)
    return None


def _strip_skill_tool_injected_hints(body: str) -> str:
    """Remove hints this rail prepends so ``before_model_call`` re-runs stay idempotent."""

    s = body
    while True:
        stripped = False
        for prefix in (SKILL_TOOL_MARKDOWN_IMAGES_HINT + "\n\n",):
            if s.startswith(prefix):
                s = s[len(prefix):]
                stripped = True
        if not stripped:
            break
    return s


def apply_skill_tool_markdown_images_hint(body: str) -> str:
    """Normalize body and prepend ``SKILL_TOOL_MARKDOWN_IMAGES_HINT`` at most once."""

    normalized = _strip_skill_tool_injected_hints(body)
    return SKILL_TOOL_MARKDOWN_IMAGES_HINT + "\n\n" + normalized


def is_path_under_workspace_skills(abs_file_path: str) -> bool:
    """Return True when ``abs_file_path`` resolves under ``{get_cwd()}/skills``."""
    work = Path(get_cwd()).expanduser().resolve()
    skills_root = (work / "skills").resolve()
    candidate = Path(abs_file_path).resolve()
    try:
        candidate.relative_to(skills_root)
    except ValueError:
        return False
    return True


def build_skill_bundle_image_lead_text(
    source_path: str,
    reference_caption: Optional[str] = None,
) -> str:
    """Lead text placed before skill-bundle reference images from ``read_file``."""
    cap_stripped = ""
    if isinstance(reference_caption, str):
        cap_stripped = reference_caption.strip()
    caption = cap_stripped or Path(source_path).stem or "reference image"
    return REFERENCE_IMAGE_NOTE.format(caption=caption)


def _resolve_read_file_tool_path_like_filesystem(raw: str) -> str:
    """Match ``filesystem._resolve_tool_file_path`` (relative paths vs ``get_cwd()``)."""
    expanded = os.path.expanduser(raw)
    if expanded.startswith("\\\\") or expanded.startswith("//") or os.path.isabs(expanded):
        return expanded
    work_dir = get_cwd()
    return str((Path(work_dir).expanduser().resolve() / expanded).resolve())


def _matching_read_file_caption(
    assistant: AssistantMessage,
    resolved_target: Path,
) -> Optional[str]:
    """Return optional ``caption`` from a ``read_file`` tool call whose path matches."""
    if not assistant.tool_calls:
        return None
    for tc in assistant.tool_calls:
        if getattr(tc, "name", None) != "read_file":
            continue
        args = _parse_tool_call_arguments(tc.arguments)
        if args is None:
            continue
        raw_path = args.get("path") or args.get("file_path")
        if not raw_path:
            continue
        try:
            cand = Path(_resolve_read_file_tool_path_like_filesystem(str(raw_path))).resolve()
        except OSError:
            continue
        if cand == resolved_target:
            alt = args.get("caption")
            if alt is None:
                alt = args.get("reference_caption")
            if isinstance(alt, str):
                stripped = alt.strip()
                return stripped or None
            return None
    return None


def _nearest_assistant_before_idx(messages: List[Any], idx: int) -> Optional[AssistantMessage]:
    """Skip contiguous ``ToolMessage`` tail immediately preceding ``messages[idx]``."""
    j = idx - 1
    while j >= 0 and isinstance(messages[j], ToolMessage):
        j -= 1
    if j >= 0 and isinstance(messages[j], AssistantMessage):
        return messages[j]
    return None


def _is_skill_read_file_image_user_message(msg: Any) -> bool:
    """Return True for decorated skill-bundle ``read_file`` image user turns."""
    if not isinstance(msg, UserMessage):
        return False
    if getattr(msg, "role", "") != "user":
        return False
    if getattr(msg, "name", None) != MULTIMODAL_SKILL_USER_MESSAGE_NAME:
        return False
    blocks = msg.content
    if not isinstance(blocks, list):
        return False
    return any(isinstance(b, dict) and b.get("type") == "image_url" for b in blocks)


def _merge_skill_read_file_user_messages(run: List[UserMessage]) -> UserMessage:
    """Combine parallel skill ``read_file`` image turns into one multimodal user message."""
    merged_blocks: List[dict[str, Any]] = []

    for msg in run:
        blocks = msg.content
        if not isinstance(blocks, list):
            continue
        image_blocks = [
            b for b in blocks if isinstance(b, dict) and b.get("type") == "image_url"
        ]
        for block in blocks:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            text_body = block.get("text")
            if not isinstance(text_body, str):
                continue
            note = text_body.strip()
            if note:
                merged_blocks.append({"type": "text", "text": note})
        merged_blocks.extend(image_blocks)

    return UserMessage(
        content=merged_blocks,
        name=MULTIMODAL_SKILL_USER_MESSAGE_NAME,
    )


def merge_consecutive_read_file_skill_user_messages(messages: List[Any]) -> List[Any]:
    """Merge back-to-back skill ``read_file`` image user turns into a single message."""
    merged: List[Any] = []
    idx = 0
    while idx < len(messages):
        msg = messages[idx]
        if not _is_skill_read_file_image_user_message(msg):
            merged.append(msg)
            idx += 1
            continue

        run: List[UserMessage] = [msg]
        next_idx = idx + 1
        while next_idx < len(messages) and _is_skill_read_file_image_user_message(
            messages[next_idx]
        ):
            run.append(messages[next_idx])
            next_idx += 1

        if len(run) == 1:
            merged.append(run[0])
        else:
            merged.append(_merge_skill_read_file_user_messages(run))
        idx = next_idx
    return merged


def decorate_read_file_skill_bundle_user_messages(messages: List[Any]) -> None:
    """Rewrite generic ``read_file`` image user turns under ``skills/`` (mobile multimodal cue)."""
    for idx, msg in enumerate(messages):
        if not isinstance(msg, UserMessage):
            continue
        if getattr(msg, "role", "") != "user":
            continue
        if getattr(msg, "name", None) == MULTIMODAL_SKILL_USER_MESSAGE_NAME:
            continue
        blocks = msg.content
        if not isinstance(blocks, list) or len(blocks) < 2:
            continue
        first = blocks[0]
        if not isinstance(first, dict) or first.get("type") != "text":
            continue
        text_body = first.get("text")
        if not isinstance(text_body, str):
            continue
        m = _IMAGE_LOADED_FROM_READ_FILE.match(text_body.strip())
        if not m:
            continue

        declared_path = m.group(1).strip()
        try:
            resolved = Path(declared_path).resolve()
        except OSError:
            continue
        if not is_path_under_workspace_skills(str(resolved)):
            continue

        assistant = _nearest_assistant_before_idx(messages, idx)
        caption = _matching_read_file_caption(assistant, resolved) if assistant else None

        msg.name = MULTIMODAL_SKILL_USER_MESSAGE_NAME
        blocks[0] = {
            "type": "text",
            "text": build_skill_bundle_image_lead_text(str(resolved), caption),
        }


# -----------------------------------------------------------------------------
# Markdown helpers (legacy / tests / optional paths)
# -----------------------------------------------------------------------------


def get_mime_type(file_path: Path) -> str:
    ext = file_path.suffix.lower()
    if ext in (".jpeg", ".jpg"):
        return "image/jpeg"
    if ext == ".png":
        return "image/png"
    if ext == ".gif":
        return "image/gif"
    if ext == ".webp":
        return "image/webp"
    return "image/jpeg"


def parse_markdown_to_blocks(
    markdown_text: str,
    base_dir: Optional[Path] = None,
) -> List[dict[str, Any]]:
    blocks: List[dict[str, Any]] = []
    pattern = re.compile(r"!\[([^\]]*)\]\((.*?)\)")
    last_idx = 0
    for match in pattern.finditer(markdown_text):
        text_part = markdown_text[last_idx:match.start()].strip()
        if text_part:
            blocks.append({"type": "text", "text": text_part})

        alt_text = match.group(1).strip()
        image_url = match.group(2).strip()
        caption = alt_text or Path(urllib.parse.unquote(image_url)).name or "reference image"

        if base_dir and not image_url.startswith(("http://", "https://", "data:")):
            try:
                decoded_path = urllib.parse.unquote(image_url)
                img_path = base_dir / decoded_path
                if img_path.exists() and img_path.is_file():
                    with open(img_path, "rb") as f:
                        b64_data = base64.b64encode(f.read()).decode("utf-8")
                    mime_type = get_mime_type(img_path)
                    image_url = f"data:{mime_type};base64,{b64_data}"
                else:
                    logger.warning(
                        "[MultimodalSkillReadRail] image not found: %s",
                        img_path,
                    )
            except Exception as e:
                logger.warning(
                    "MultimodalSkillReadRail: load image %s failed: %s",
                    image_url,
                    e,
                )

        blocks.append(
            {
                "type": "text",
                "text": REFERENCE_IMAGE_NOTE.format(caption=caption),
            }
        )
        blocks.append(
            {
                "type": "image_url",
                "image_url": {"url": image_url, "detail": "low"},
            }
        )

        last_idx = match.end()

    text_part = markdown_text[last_idx:].strip()
    if text_part:
        blocks.append({"type": "text", "text": text_part})
    return blocks


def _decode_skill_body(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def _extract_skill_content_from_tool_message_str(tool_body: str) -> Optional[str]:
    marker = re.search(r"""["']skill_content["']\s*:\s*""", tool_body)
    if not marker:
        return None
    i = marker.end()
    if i >= len(tool_body):
        return None
    quote = tool_body[i]
    if quote not in "'\"":
        return None
    i += 1
    chunks: List[str] = []
    esc_map = {"n": "\n", "r": "\r", "t": "\t"}
    while i < len(tool_body):
        ch = tool_body[i]
        if ch == "\\":
            i += 1
            if i >= len(tool_body):
                return None
            nxt = tool_body[i]
            chunks.append(esc_map.get(nxt, nxt))
            i += 1
            continue
        if ch == quote:
            return "".join(chunks)
        chunks.append(ch)
        i += 1
    return None


ToolSourceKind = Literal["read_file", "skill_tool"]


class MultimodalSkillReadRail(AgentRail):
    """Inject a short skill-tool hint + rewrite ``read_file`` skill images for mobile multimodal cues."""

    priority: int = 40

    def __init__(
        self,
        skill_root: str,
        *,
        skill_consult_mode: str = "inline",
    ) -> None:
        super().__init__()
        self.skill_root = Path(skill_root)
        self._skill_consult_mode = skill_consult_mode

    def _inline_mode_active(self) -> bool:
        return self._skill_consult_mode != "branch"

    def _collect_tool_paths(self, messages: List[Any]) -> tuple[dict[str, str], dict[str, ToolSourceKind]]:
        tool_paths: dict[str, str] = {}
        tool_sources: dict[str, ToolSourceKind] = {}
        for msg in messages:
            if isinstance(msg, AssistantMessage) and msg.tool_calls:
                for tc in msg.tool_calls:
                    if not tc.id:
                        continue
                    args = _parse_tool_call_arguments(tc.arguments)
                    if args is None:
                        continue
                    if tc.name == "read_file":
                        path = args.get("path") or args.get("file_path")
                        if path:
                            tool_paths[tc.id] = str(path).replace("\\", "/")
                            tool_sources[tc.id] = "read_file"
                    elif tc.name == "skill_tool":
                        skill_name = str(args.get("skill_name", "") or "").strip()
                        rel = str(args.get("relative_file_path") or "SKILL.md").strip()
                        if not rel:
                            rel = "SKILL.md"
                        if skill_name:
                            tool_paths[tc.id] = f"{skill_name}/{rel}".replace("\\", "/")
                            tool_sources[tc.id] = "skill_tool"
        return tool_paths, tool_sources

    def _expand_messages(self, messages: List[Any]) -> List[Any]:
        _, tool_sources = self._collect_tool_paths(messages)

        new_messages: List[Any] = []
        for msg in messages:
            if isinstance(msg, ToolMessage) and isinstance(msg.content, str):
                body = msg.content
                tc_id = getattr(msg, "tool_call_id", None) or ""
                src = tool_sources.get(tc_id)
                markdown: Optional[str] = None
                if src == "read_file" and "![" in body and "](" in body:
                    markdown = body
                elif src == "skill_tool":
                    raw_skill = _extract_skill_content_from_tool_message_str(body)
                    markdown_candidate = _decode_skill_body(raw_skill)
                    if markdown_candidate and "![" in markdown_candidate and "](" in markdown_candidate:
                        markdown = markdown_candidate
                elif not src and "![" in body and "](" in body:
                    markdown = body

                if markdown is not None and src == "skill_tool":
                    new_messages.append(
                        ToolMessage(
                            tool_call_id=msg.tool_call_id,
                            name=msg.name,
                            content=apply_skill_tool_markdown_images_hint(body),
                        )
                    )
                    continue
                if markdown is not None:
                    new_messages.append(msg)
                    continue
            new_messages.append(msg)
        return new_messages

    def _transform_messages(self, messages: List[Any]) -> List[Any]:
        staged = self._expand_messages(messages)
        decorate_read_file_skill_bundle_user_messages(staged)
        return merge_consecutive_read_file_skill_user_messages(staged)

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        if not self._inline_mode_active():
            return
        if not isinstance(ctx.inputs, ModelCallInputs):
            return
        if not ctx.inputs.messages:
            return

        mc = getattr(ctx.inputs, "model_context", None) or getattr(ctx, "context", None)
        preview = ctx.inputs.messages

        if mc is None:
            ctx.inputs.messages = self._transform_messages(list(preview))
            return

        expanded_tail = self._transform_messages(list(mc.get_messages()))
        mc.set_messages(expanded_tail)

        head: List[Any] = []
        if preview and getattr(preview[0], "role", None) == "system":
            head = [preview[0]]
        ctx.inputs.messages = head + deepcopy(mc.get_messages())


__all__ = [
    "MULTIMODAL_SKILL_USER_MESSAGE_NAME",
    "REFERENCE_IMAGE_NOTE",
    "SKILL_TOOL_MARKDOWN_IMAGES_HINT",
    "USER_MESSAGES_PROTECTED_FROM_SCREENSHOT_ARCHIVE",
    "MultimodalSkillReadRail",
    "apply_skill_tool_markdown_images_hint",
    "build_skill_bundle_image_lead_text",
    "decorate_read_file_skill_bundle_user_messages",
    "is_path_under_workspace_skills",
    "merge_consecutive_read_file_skill_user_messages",
    "parse_markdown_to_blocks",
]
