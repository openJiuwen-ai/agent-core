# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import json
from typing import Any, Mapping, Optional

from pydantic import BaseModel


class ToolOutput(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None


def render_fields(fields: Mapping[str, Any], *, separator: str = "\n") -> str:
    """Render a flat record as ``key: value`` pairs for model-facing tool text.

    Pairs whose value is ``None`` or an empty string are skipped. String values
    are written as-is; any other value is JSON-encoded so nested structures
    stay unambiguous.
    """
    pairs = []
    for key, value in fields.items():
        if value is None or value == "":
            continue
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        pairs.append(f"{key}: {text}")
    return separator.join(pairs)
