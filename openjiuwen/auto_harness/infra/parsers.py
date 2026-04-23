# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Agent 输出解析工具集。

提供从 agent 文本输出中提取结构化数据的通用函数：
- ``parse_tasks``: 解析 JSON 任务列表
- ``parse_learnings``: 解析 JSON 经验列表
- ``parse_pr_draft``: 解析 PR draft JSON
- ``parse_gaps``: 解析 markdown 表格中的竞品差距
- ``extract_text``: 从 OutputSchema chunk 提取文本
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, List

from openjiuwen.auto_harness.pipelines import (
    normalize_pipeline_name,
)
from openjiuwen.auto_harness.schema import (
    Gap,
    OptimizationTask,
    PipelineSelectionArtifact,
    PullRequestDraft,
)

logger = logging.getLogger(__name__)
_ALLOWED_PR_KINDS = {
    "bug",
    "task",
    "feature",
    "refactor",
    "clean_code",
}


def parse_tasks(raw: str) -> List[OptimizationTask]:
    """从 agent 输出中解析 JSON 任务列表。

    支持 ```json ... ``` 包裹和裸 JSON 数组。

    Args:
        raw: agent 输出的原始文本。

    Returns:
        解析后的 OptimizationTask 列表。
    """
    match = re.search(
        r"```json\s*(.*?)\s*```", raw, re.DOTALL,
    )
    json_str: str
    if match:
        json_str = match.group(1)
    else:
        arr_match = re.search(r"\[.*]", raw, re.DOTALL)
        if not arr_match:
            return []
        json_str = arr_match.group(0)

    try:
        items = json.loads(json_str)
    except json.JSONDecodeError:
        logger.warning("Failed to parse plan JSON")
        return []

    tasks: List[OptimizationTask] = []
    for item in items:
        if isinstance(item, dict) and "topic" in item:
            tasks.append(OptimizationTask(
                topic=item["topic"],
                description=item.get(
                    "description", "",
                ),
                files=item.get("files", []),
                expected_effect=item.get(
                    "expected_effect", "",
                ),
                pipeline_name=normalize_pipeline_name(
                    str(item.get("pipeline_name", ""))
                ),
            ))
    return tasks


def parse_learnings(raw: str) -> List[dict]:
    """从 learnings agent 输出中解析 JSON 经验列表。

    Args:
        raw: agent 输出的原始文本。

    Returns:
        字典列表，每个包含 type/topic/summary/details。
    """
    match = re.search(
        r"```json\s*(.*?)\s*```", raw, re.DOTALL,
    )
    json_str: str
    if match:
        json_str = match.group(1)
    else:
        arr_match = re.search(r"\[.*]", raw, re.DOTALL)
        if not arr_match:
            return []
        json_str = arr_match.group(0)

    try:
        items = json.loads(json_str)
    except json.JSONDecodeError:
        logger.warning("Failed to parse learnings JSON")
        return []

    if not isinstance(items, list):
        return []
    return [
        item for item in items
        if isinstance(item, dict) and "topic" in item
    ]


def parse_pr_draft_with_error(
    raw: str,
) -> tuple[PullRequestDraft | None, str]:
    """Parse a PR draft JSON response with detailed errors."""
    match = re.search(
        r"```json\s*(.*?)\s*```", raw, re.DOTALL
    )
    json_str: str
    if match:
        json_str = match.group(1)
    else:
        obj_match = re.search(r"\{.*}", raw, re.DOTALL)
        if not obj_match:
            return None, "未找到 JSON 对象"
        json_str = obj_match.group(0)

    try:
        item = json.loads(json_str)
    except json.JSONDecodeError:
        logger.warning("Failed to parse PR draft JSON")
        return None, "JSON 解析失败"
    if not isinstance(item, dict):
        return None, "JSON 顶层必须是对象"

    title = str(item.get("title", "")).strip()
    body = str(item.get("body", "")).strip()
    kind = str(item.get("kind", "")).strip()
    if not kind and body:
        match = re.search(
            r"(?m)^/kind\s+([a-z_]+)\s*$",
            body,
        )
        if match:
            kind = match.group(1).strip()
    if not title or not body:
        return None, "缺少 title 或 body"
    if kind not in _ALLOWED_PR_KINDS:
        return (
            None,
            "kind 必须是 bug/task/feature/refactor/clean_code 之一",
        )
    return PullRequestDraft(
        title=title,
        body=body,
        kind=kind,
    ), ""


def parse_pr_draft(
    raw: str,
) -> PullRequestDraft | None:
    """Parse a PR draft JSON response."""
    draft, _ = parse_pr_draft_with_error(raw)
    return draft


def parse_pipeline_selection(
    raw: str,
) -> PipelineSelectionArtifact | None:
    """Parse a selector agent JSON response."""
    match = re.search(
        r"```json\s*(.*?)\s*```", raw, re.DOTALL
    )
    json_str: str
    if match:
        json_str = match.group(1)
    else:
        obj_match = re.search(r"\{.*}", raw, re.DOTALL)
        if not obj_match:
            return None
        json_str = obj_match.group(0)

    try:
        item = json.loads(json_str)
    except json.JSONDecodeError:
        logger.warning("Failed to parse pipeline selection JSON")
        return None
    if not isinstance(item, dict):
        return None
    pipeline_name = normalize_pipeline_name(
        str(item.get("pipeline_name", "")).strip()
    )
    if not pipeline_name:
        return None
    alternatives = item.get("alternatives", [])
    if not isinstance(alternatives, list):
        alternatives = []
    required_inputs = item.get(
        "required_inputs", []
    )
    if not isinstance(required_inputs, list):
        required_inputs = []
    try:
        confidence = float(
            item.get("confidence", 0.0)
        )
    except (TypeError, ValueError):
        confidence = 0.0
    return PipelineSelectionArtifact(
        pipeline_name=pipeline_name,
        reason=str(item.get("reason", "")),
        alternatives=[
            normalize_pipeline_name(str(v))
            for v in alternatives
        ],
        confidence=confidence,
        risk_level=str(
            item.get("risk_level", "")
        ),
        required_inputs=[
            str(v) for v in required_inputs
        ],
        fallback_pipeline=normalize_pipeline_name(
            str(item.get("fallback_pipeline", ""))
        ),
    )


def extract_text(chunk: Any) -> str:
    """从 OutputSchema chunk 中提取文本内容。

    Args:
        chunk: OutputSchema 实例。

    Returns:
        提取的文本，无内容时返回空字符串。
    """
    if hasattr(chunk, "payload"):
        payload = chunk.payload
        if isinstance(payload, dict):
            return str(payload.get("content", ""))
    return ""


def parse_gaps(raw_text: str) -> List[Gap]:
    """Parse structured text into ``Gap`` objects.

    Accepts a markdown table where each row has columns:
    ``competitor | feature | current_state |
    gap_description | impact | feasibility |
    suggested_approach | target_files``

    Args:
        raw_text: Markdown table or similar text.

    Returns:
        Gaps sorted by priority descending.
    """
    gaps: list[Gap] = []
    lines = raw_text.strip().splitlines()

    for line in lines:
        line = line.strip()
        if not line or line.startswith("|--"):
            continue
        if not line.startswith("|"):
            continue
        cells = [
            c.strip() for c in line.split("|")[1:-1]
        ]
        if len(cells) < 8:
            continue
        # Skip header row
        if cells[0].lower() in ("competitor", "竞品"):
            continue
        gap = _row_to_gap(cells)
        if gap:
            gaps.append(gap)

    gaps.sort(key=lambda g: g.priority, reverse=True)
    logger.info("Parsed %d gaps from raw text", len(gaps))
    return gaps


def _row_to_gap(cells: list[str]) -> Gap | None:
    """Convert a table row (8 cells) into a Gap.

    Args:
        cells: List of cell strings from a markdown row.

    Returns:
        A ``Gap`` instance, or ``None`` on parse error.
    """
    try:
        target_files = [
            f.strip()
            for f in cells[7].split(",")
            if f.strip()
        ]
        return Gap(
            id=uuid.uuid4().hex[:8],
            competitor=cells[0],
            feature=cells[1],
            current_state=cells[2],
            gap_description=cells[3],
            impact=float(cells[4]),
            feasibility=float(cells[5]),
            suggested_approach=cells[6],
            target_files=target_files,
        )
    except (ValueError, IndexError):
        logger.warning(
            "Skipping malformed gap row: %s",
            " | ".join(cells[:4]),
        )
        return None
