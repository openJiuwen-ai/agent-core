# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Smart output truncation and large-output persistence."""
from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path


def truncate_output(text: str, max_chars: int, *, head_ratio: float = 0.8) -> str:
    """Truncate long output while preserving both the beginning and end."""
    if len(text) <= max_chars:
        return text

    head_budget = int(max_chars * head_ratio)
    tail_budget = max_chars - head_budget

    head = text[:head_budget]
    tail = text[-tail_budget:] if tail_budget > 0 else ""
    omitted = text[head_budget: len(text) - tail_budget] if tail_budget > 0 else text[head_budget:]
    omitted_lines = omitted.count("\n")

    return f"{head}\n\n... [{omitted_lines} lines omitted] ...\n\n{tail}"


_OUTPUT_DIR: Path = Path(tempfile.gettempdir()) / "openjiuwen_powershell_outputs"


def persist_large_output(stdout: str, stderr: str) -> tuple[str, int]:
    """Write raw command output to a temp file for later retrieval."""
    combined = stdout
    if stderr:
        combined += f"\n--- stderr ---\n{stderr}"

    content_bytes = combined.encode("utf-8", errors="replace")
    digest = hashlib.sha256(content_bytes).hexdigest()[:12]

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = _OUTPUT_DIR / f"powershell_{digest}.txt"

    if not path.exists():
        path.write_bytes(content_bytes)

    return str(path), len(content_bytes)
