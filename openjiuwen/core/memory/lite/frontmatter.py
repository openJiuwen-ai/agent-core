# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""frontmatter for Codeing Memory."""

import datetime
from typing import Optional, Dict


VALID_TYPES = ("user", "feedback", "project", "reference")


def parse_frontmatter(content: str) -> Optional[Dict[str, str]]:
    content = content.strip()
    if not content.startswith("---"):
        return None
    end = content.find("---", 3)
    if end == -1:
        return None
    result = {}
    for line in content[3:end].strip().split("\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()
    return result if result else None


def validate_frontmatter(fm: Dict[str, str]) -> tuple[bool, str]:
    for field in ("name", "description", "type"):
        if not fm.get(field):
            return (False, f"Missing required field: {field}")
    if fm["type"] not in VALID_TYPES:
        return (False, f"type must be one of: {VALID_TYPES}")
    return (True, "")


def enrich_frontmatter(fm: Dict[str, str], is_edit: bool = False) -> Dict[str, str]:
    """Auto-fill timestamps. Sets created_at on creation, updates updated_at on every write/edit."""
    today = datetime.date.today().isoformat()
    if not is_edit:
        fm.setdefault("created_at", today)
    fm["updated_at"] = today
    return fm


def rebuild_content_with_frontmatter(content: str, fm: Dict[str, str]) -> str:
    """Rebuild file content with updated frontmatter, preserving the body."""
    body = _extract_body(content)
    fm_lines = ["---"]
    for key, value in fm.items():
        fm_lines.append(f"{key}: {value}")
    fm_lines.append("---")
    parts = ["\n".join(fm_lines)]
    if body:
        parts.append(body)
    return "\n\n".join(parts)


def _extract_body(content: str) -> str:
    """Extract the body content after the frontmatter."""
    content = content.strip()
    if not content.startswith("---"):
        return content
    end = content.find("---", 3)
    if end == -1:
        return ""
    body_start = end + 3
    return content[body_start:].strip()
