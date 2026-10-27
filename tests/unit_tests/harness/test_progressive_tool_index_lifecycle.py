# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for ProgressiveToolRail BM25 index lifecycle."""

from __future__ import annotations

from types import SimpleNamespace

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


def _deferred(
    name: str,
    description: str,
    *,
    properties=None,
) -> ToolCard:
    return ToolCard(
        id=name,
        name=name,
        description=description,
        exposure=ToolExposure.DEFERRED,
        input_params={"type": "object", "properties": {}},
        properties=properties or {},
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


@pytest.mark.asyncio
async def test_index_rebuilds_when_deferred_parameters_change_without_registry_revision():
    card = _deferred("calendar_create", "Create a calendar event")
    registry = _Registry([card])
    rail = ProgressiveToolRail(
        DeepAgentConfig(progressive_tool_enabled=True, language="cn")
    )
    agent = _agent(registry)

    rail.init(agent)
    rail.finalize_startup(agent)
    initial_index = rail._tool_search_index

    await rail._search_tools("calendar", limit=5)
    assert rail._tool_search_index is initial_index

    card.input_params["properties"]["starts_at"] = {"type": "string"}
    matches = await rail._search_tools("starts_at", limit=5)

    assert rail._tool_search_index is not initial_index
    assert rail._tool_search_index.source_fingerprint != initial_index.source_fingerprint
    assert [item["name"] for item in matches] == ["calendar_create"]


@pytest.mark.asyncio
async def test_discovery_alias_matches_but_result_keeps_raw_tool_schema():
    card = _deferred(
        "cron_create_job",
        "Create a scheduled job",
        properties={
            "progressive_discovery": {
                "display": {"en": "Create reminders and scheduled jobs"},
                "aliases": ["提醒我", "定时提醒", "闹钟"],
            }
        },
    )
    registry = _Registry([card])
    rail = ProgressiveToolRail(
        DeepAgentConfig(progressive_tool_enabled=True, language="en")
    )
    rail.init(_agent(registry))
    rail.finalize_startup(_agent(registry))

    matches = await rail._search_tools("请提醒我明天开会", limit=5)

    assert matches == [
        {
            "name": "cron_create_job",
            "description": "Create a scheduled job",
            "parameters": {"type": "object", "properties": {}},
        }
    ]
    assert rail._collect_deferred_tool_descriptions() == {
        "cron_create_job": "Create reminders and scheduled jobs"
    }


@pytest.mark.parametrize(
    ("language", "display", "expected"),
    [
        ("cn", {"zh": "创建定时任务"}, "创建定时任务"),
        (" CN ", {" ZH ": " 创建定时任务 "}, "创建定时任务"),
        ("zh", {"cn": "创建定时任务"}, "创建定时任务"),
        (" EN ", {" en ": " Create scheduled jobs "}, "Create scheduled jobs"),
        ("cn", {"cn": "   ", "zh": "创建定时任务"}, "创建定时任务"),
        ("en", {"zh": "创建定时任务"}, "Raw tool description"),
        ("cn", {"en": "Create scheduled jobs"}, "Raw tool description"),
    ],
)
def test_discovery_display_normalizes_language_keys_and_cn_zh_aliases(
    language,
    display,
    expected,
):
    card = _deferred(
        "cron_create_job",
        "Raw tool description",
        properties={"progressive_discovery": {"display": display}},
    )
    registry = _Registry([card])
    rail = ProgressiveToolRail(
        DeepAgentConfig(progressive_tool_enabled=True, language="en")
    )
    rail._config.language = language
    agent = _agent(registry)
    rail.init(agent)
    rail.finalize_startup(agent)

    assert rail._collect_deferred_tool_descriptions() == {
        "cron_create_job": expected
    }


def test_discovery_display_uses_prompt_builder_language_for_catalog_rendering():
    card = _deferred(
        "cron_create_job",
        "Raw tool description",
        properties={
            "progressive_discovery": {
                "display": {
                    "en": "Create scheduled jobs",
                    "zh": "创建定时任务",
                }
            }
        },
    )
    registry = _Registry([card])
    rail = ProgressiveToolRail(
        DeepAgentConfig(progressive_tool_enabled=True, language="en")
    )
    agent = _agent(registry)
    agent.system_prompt_builder = SimpleNamespace(language="cn")
    rail.init(agent)
    rail.finalize_startup(agent)

    assert rail._collect_deferred_tool_descriptions(agent) == {
        "cron_create_job": "创建定时任务"
    }


@pytest.mark.asyncio
async def test_index_rebuilds_when_discovery_alias_changes_in_place():
    card = _deferred(
        "calendar_create",
        "Create an event",
        properties={"progressive_discovery": {"aliases": ["会议"]}},
    )
    registry = _Registry([card])
    rail = ProgressiveToolRail(
        DeepAgentConfig(progressive_tool_enabled=True, language="en")
    )
    agent = _agent(registry)
    rail.init(agent)
    rail.finalize_startup(agent)
    initial_index = rail._tool_search_index

    assert await rail._search_tools("提醒", limit=5) == []
    card.properties["progressive_discovery"]["aliases"] = ["提醒"]
    matches = await rail._search_tools("提醒", limit=5)

    assert rail._tool_search_index is not initial_index
    assert [item["name"] for item in matches] == ["calendar_create"]
