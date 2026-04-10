# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""frontmatter for Codeing Memory."""

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