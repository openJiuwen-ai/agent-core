# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Agent 输出解析工具集。

提供从 agent 文本输出中提取结构化数据的通用函数：
- ``parse_tasks``: 解析 JSON 任务列表
- ``parse_learnings``: 解析 JSON 经验列表
- ``parse_gaps``: 解析 markdown 表格中的竞品差距
- ``extract_text``: 从 OutputSchema chunk 提取文本
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, List

from openjiuwen.auto_harness.schema import (
    Gap,
    OptimizationTask,
)

logger = logging.getLogger(__name__)


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
