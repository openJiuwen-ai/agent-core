# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PersonalContext function tools render like the file tools they wrap."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from openjiuwen.harness.personal_context.file_tools import make_personal_context_file_tools
from openjiuwen.harness.tools.base_tool import ToolOutput


def _tool(tmp_path: Path, name: str) -> Any:
    tools = make_personal_context_file_tools(cast(Any, object()), tmp_path)
    return next(tool for tool in tools if tool.card.name == name)


@pytest.mark.level1
def test_guarded_write_file_renders_like_write_file(tmp_path: Path) -> None:
    output = ToolOutput(
        success=True,
        data={
            "file_path": "context/a.md",
            "bytes_written": 4,
            "type": "create",
            "created": True,
            "original_file": None,
        },
    )

    assert _tool(tmp_path, "write_file").render_for_llm(output) == "Created context/a.md (4 bytes written)."


@pytest.mark.level1
def test_bounded_grep_and_move_path_render_text(tmp_path: Path) -> None:
    no_match = ToolOutput(
        success=True,
        data={"stdout": "", "content": "", "exit_code": 1, "appliedLimit": None},
    )
    assert _tool(tmp_path, "grep").render_for_llm(no_match) == "No matches found."

    moved = ToolOutput(
        success=True,
        data={"source_path": "a/b.md", "destination_path": "c/b.md", "kind": "file", "links_rewritten": False},
    )
    assert _tool(tmp_path, "move_path").render_for_llm(moved) == (
        "Moved file context/a/b.md to context/c/b.md; links were not rewritten."
    )
    assert _tool(tmp_path, "move_path").render_for_llm(ToolOutput(success=False, error="exists")) == "exists"
