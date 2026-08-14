# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the local BM25 index used by progressive tool search."""

from __future__ import annotations

from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.harness.tools.tool_discovery.bm25 import BM25ToolIndex


def _tool(name: str, description: str) -> ToolInfo:
    return ToolInfo(
        name=name,
        description=description,
        parameters={"type": "object", "properties": {}},
    )


def test_bm25_builds_once_and_ranks_name_and_description_matches():
    index = BM25ToolIndex.build(
        [
            _tool("cron_create_job", "Create a calendar reminder"),
            _tool("calendar_list_events", "List calendar events"),
            _tool("slack_send_message", "Send a Slack message"),
        ]
    )

    assert index.document_count == 3
    matches = index.search("calendar reminder", limit=2)

    assert [tool.name for tool in matches] == [
        "cron_create_job",
        "calendar_list_events",
    ]


def test_bm25_empty_query_and_limit_are_safe():
    index = BM25ToolIndex.build([_tool("cron_create_job", "Create a reminder")])

    assert index.search("", limit=10) == []
    assert index.search("reminder", limit=0) == []
