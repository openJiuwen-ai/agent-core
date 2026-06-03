# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import List


_MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((.*?)\)")


@dataclass(frozen=True)
class SkillImageEntry:
    """One reference figure declared in skill markdown."""

    image_id: str
    alt: str
    rel_path: str
    abs_path: str


def _stable_image_id(rel_path: str, index: int) -> str:
    stem = Path(urllib.parse.unquote(rel_path)).stem
    if stem:
        return stem
    return f"image_{index}"


def build_skill_image_manifest(
    skill_markdown: str,
    skill_directory: str,
) -> List[SkillImageEntry]:
    """Parse ``![](alt)(path)`` entries and resolve files under ``skill_directory``."""
    base = Path(skill_directory).expanduser().resolve()
    entries: List[SkillImageEntry] = []
    seen_ids: set[str] = set()

    for index, match in enumerate(_MARKDOWN_IMAGE_RE.finditer(skill_markdown or "")):
        alt = (match.group(1) or "").strip()
        raw_url = (match.group(2) or "").strip()
        if not raw_url or raw_url.startswith(("http://", "https://", "data:")):
            continue

        decoded = urllib.parse.unquote(raw_url)
        rel_path = decoded.replace("\\", "/")
        candidate = (base / decoded).resolve()
        if not candidate.is_file():
            continue

        image_id = _stable_image_id(rel_path, index)
        if image_id in seen_ids:
            image_id = f"{image_id}_{index}"
        seen_ids.add(image_id)

        entries.append(
            SkillImageEntry(
                image_id=image_id,
                alt=alt or Path(rel_path).name,
                rel_path=rel_path,
                abs_path=str(candidate),
            )
        )

    return entries


def format_manifest_for_prompt(entries: List[SkillImageEntry]) -> str:
    """Compact manifest text for branch Stage 1."""
    if not entries:
        return "(no local reference images)"
    lines = []
    for entry in entries:
        lines.append(
            f"- {entry.image_id}: alt={entry.alt!r}, path={entry.rel_path}"
        )
    return "\n".join(lines)
