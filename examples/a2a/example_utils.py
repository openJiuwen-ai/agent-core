# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Small helpers shared by ``examples/a2a`` client scripts."""

from __future__ import annotations

from openjiuwen.core.controller.schema.task import TaskStatus
from openjiuwen.core.single_agent.schema.agent_result import AgentResult


def print_invoke_result(
    result: AgentResult,
    *,
    label: str = "invoke",
    expect_text: str | None = None,
) -> None:
    """Print an invoke ``AgentResult`` and verify it looks like a completed call."""
    print(f"--- {label} ---")
    print(f"status={result.status!r} task_id={result.task_id!r} session={result.sessionId!r}")
    if result.artifacts:
        for index, artifact in enumerate(result.artifacts):
            text = artifact.parts[0].text if artifact.parts else None
            print(f"artifact[{index}]: {text!r}")
    else:
        print("artifacts: []")

    if result.status != TaskStatus.COMPLETED:
        raise RuntimeError(f"{label}: expected status COMPLETED, got {result.status!r}")

    if expect_text is not None:
        texts = [
            part.text
            for artifact in result.artifacts
            for part in artifact.parts
            if part.text
        ]
        if not any(expect_text in text for text in texts):
            raise RuntimeError(f"{label}: expected {expect_text!r} in artifacts, got {texts!r}")


def print_stream_chunk(label: str, chunk: AgentResult) -> None:
    """Print one streaming chunk."""
    text = None
    if chunk.artifacts and chunk.artifacts[0].parts:
        text = chunk.artifacts[0].parts[0].text
    print(f"{label}: status={chunk.status!r} artifacts={len(chunk.artifacts)} text={text!r}")
