# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared path normalization for outbound file tools and their permission checks."""

import ast
import json
from pathlib import Path
from typing import Any

from openjiuwen.core.sys_operation.cwd import get_cwd

def normalize_file_path_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text[0] in "[(" and text[-1] in ")]":
            for loader in (json.loads, ast.literal_eval):
                try:
                    parsed = loader(text)
                except (TypeError, ValueError, SyntaxError):
                    continue
                if isinstance(parsed, (list, tuple)):
                    return [str(item).strip() for item in parsed if str(item).strip()]
                if isinstance(parsed, str):
                    return [parsed.strip()] if parsed.strip() else []
                break
        return [text]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value)]


def is_public_file_url(value: str) -> bool:
    return value.strip().lower().startswith(("http://", "https://"))


def resolve_outbound_path(value: str) -> Path:
    """Use exactly the same path for permission evaluation and source reading."""
    return (Path(get_cwd()) / value).resolve()
