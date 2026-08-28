# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Guards that block submit_code_context until spans are real and in-scope."""

from __future__ import annotations

from openjiuwen.core.retrieval.code_graph.models import CodeGraphIndex
from openjiuwen.core.retrieval.code_graph.query.test_paths import issue_about_tests, is_test_path
from openjiuwen.harness.schema.code_graph import CodeGraphRunState


def finish_guard_messages(
    state: CodeGraphRunState,
    index: CodeGraphIndex | None = None,
    *,
    profile: str | None = None,
    tool_name: str = "submit_code_context",
) -> list[str]:
    """Return actionable blockers. Empty means the caller may proceed."""
    _ = index
    _ = profile
    messages: list[str] = []
    if not state.selected:
        messages.append(
            f"{tool_name} blocked: select at least one location with "
            "select_code_context first, or pass locations from read_symbol."
        )
        return messages
    files = {item.file.replace("\\", "/") for item in state.selected if item.file}
    if not files:
        messages.append(f"{tool_name} blocked: each selected span must include a file path.")
    if not issue_about_tests(state.request.query):
        test_files = sorted(path for path in files if is_test_path(path))
        if test_files:
            messages.append(
                f"{tool_name} blocked: selected test files are not allowed unless "
                f"the issue is about tests. Remove: {', '.join(test_files[:4])}."
            )
    return messages
