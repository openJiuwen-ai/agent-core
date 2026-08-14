# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for ProgressiveToolRail BM25 index lifecycle."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.core.foundation.tool import ToolCard, ToolExposure, ToolInfo
from openjiuwen.harness.rails.progressive_tool_rail import ProgressiveToolRail
from openjiuwen.harness.schema.config import DeepAgentConfig


class _Registry:
    def __init__(self, cards):
        self.cards = list(cards)
        self.registry_revision = 0

    def add_ability(self, card, _tool):
        self.cards.append(card)
        self.registry_revision += 1
        return SimpleNamespace(added=True)

    def list(self):
        return list(self.cards)

    def add_card(self, card):
        self.cards.append(card)
        self.registry_revision += 1


def _agent(registry):
    return SimpleNamespace(
        card=SimpleNamespace(id="index-test-agent"),
        ability_manager=registry,
    )


def _deferred(name: str, description: str) -> ToolCard:
    return ToolCard(
        id=name,
        name=name,
        description=description,
        exposure=ToolExposure.DEFERRED,
        input_params={"type": "object", "properties": {}},
    )


@pytest.mark.asyncio
async def test_index_is_built_at_startup_finalization_and_reused_until_registry_changes():
    registry = _Registry(
        [
            _deferred("cron_create_job", "Create a calendar reminder"),
            ToolCard(
                id="direct_tool",
                name="direct_tool",
                description="Already visible directly",
                exposure=ToolExposure.DIRECT,
            ),
        ]
    )
    rail = ProgressiveToolRail(
        DeepAgentConfig(progressive_tool_enabled=True, language="cn")
    )
    agent = _agent(registry)

    rail.init(agent)
    assert rail._tool_search_index is None
    rail.finalize_startup(agent)
    initial_index = rail._tool_search_index

    assert initial_index is not None
    assert initial_index.document_count == 1

    matches = await rail._search_tools("calendar", limit=5)
    assert [item["name"] for item in matches] == ["cron_create_job"]
    assert rail._tool_search_index is initial_index

    registry.add_card(_deferred("calendar_list_events", "List calendar events"))
    matches = await rail._search_tools("calendar", limit=5)

    assert rail._tool_search_index is not initial_index
    assert rail._tool_search_index.document_count == 2
    assert {item["name"] for item in matches} == {
        "cron_create_job",
        "calendar_list_events",
    }


@pytest.mark.asyncio
async def test_index_fallback_can_be_built_from_cached_tool_infos_for_isolated_rail_tests():
    """A rail double without an AbilityManager still has deterministic search."""

    rail = ProgressiveToolRail(
        DeepAgentConfig(progressive_tool_enabled=True, language="cn")
    )
    rail._cached_all_tool_infos = [
        ToolInfo(
            name="cron_create_job",
            description="Create a calendar reminder",
            parameters={"type": "object", "properties": {}},
        )
    ]

    matches = await rail._search_tools("calendar", limit=5)

    assert [item["name"] for item in matches] == ["cron_create_job"]
    assert rail._tool_search_index is not None
    assert rail._tool_search_index.document_count == 1
