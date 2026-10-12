# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pinning model-group (IntelliRouter) members to one model.

A pinned member keeps the single logical ``"*"`` pool entry; only its own
client config switches to the router's ``tag-filtered`` strategy.
"""

from __future__ import annotations

import pytest

from openjiuwen.agent_teams.models.allocator import (
    IntelliRouterAllocator,
    resolve_member_model,
)
from openjiuwen.agent_teams.models.pool import (
    IntelliRouterConfig,
    IntelliRouterDeployment,
    ModelPoolEntry,
)
from openjiuwen.agent_teams.schema.team import TeamSpec
from openjiuwen.agent_teams.tools.member_options import (
    build_member_options,
    load_member_options,
)


def _pool(*, strategy: str = "ordered-failover", strategy_kwargs: dict | None = None) -> list[ModelPoolEntry]:
    """Two ``fast`` deployments and one ``smart``, each tagged with its model name."""
    config = IntelliRouterConfig(
        deployments=[
            IntelliRouterDeployment(
                model_name="fast",
                api_key="sk-1",
                api_base="https://a.test",
                id="fast-1",
                provider="deepseek",
                fallback_tag="fast",
            ),
            IntelliRouterDeployment(
                model_name="fast",
                api_key="sk-2",
                api_base="https://a.test",
                id="fast-2",
                provider="deepseek",
                fallback_tag="fast",
            ),
            IntelliRouterDeployment(
                model_name="smart",
                api_key="sk-3",
                api_base="https://b.test",
                id="smart-1",
                provider="openai",
                fallback_tag="smart",
            ),
        ],
        strategy=strategy,
        strategy_kwargs=strategy_kwargs or {},
    )
    return config.to_pool_entries()


def _router(model_config):
    return model_config.model_client_config.intelli_router


def _team_spec(pool: list[ModelPoolEntry]) -> TeamSpec:
    return TeamSpec(
        team_name="t",
        display_name="t",
        model_pool=pool,
        model_pool_strategy="intelli_router",
    )


@pytest.mark.level0
def test_untagged_allocation_keeps_the_group_strategy():
    allocation = IntelliRouterAllocator(_pool()).allocate()

    assert allocation.entry.model_name == "*"
    assert allocation.route_tag is None
    assert _router(allocation.to_team_model_config()).strategy == "ordered-failover"
    assert allocation.to_db_ref() == {"model_name": "*", "model_index": 0}


@pytest.mark.level0
def test_model_name_pins_the_member_through_tag_filtered():
    pool = _pool()
    allocation = IntelliRouterAllocator(pool).allocate("fast")

    # Still the logical entry: physical deployments are never allocated.
    assert allocation.entry is pool[0]
    router = _router(allocation.to_team_model_config())
    assert router.strategy == "tag-filtered"
    assert router.strategy_kwargs == {"fallback_tag": "fast"}
    assert allocation.to_db_ref() == {"model_name": "*", "model_index": 0, "route_tag": "fast"}


@pytest.mark.level0
def test_pinning_does_not_touch_the_shared_pool_entry():
    pool = _pool()
    allocator = IntelliRouterAllocator(pool)

    allocator.allocate("fast").to_team_model_config()

    assert pool[0].metadata["client"]["intelli_router"]["strategy"] == "ordered-failover"
    assert _router(allocator.allocate().to_team_model_config()).strategy == "ordered-failover"


@pytest.mark.level1
def test_group_strategy_kwargs_are_replaced_not_merged():
    pool = _pool(strategy="tag-filtered", strategy_kwargs={"fallback_tag": "fast", "other": 1})

    router = _router(IntelliRouterAllocator(pool).allocate("fast").to_team_model_config())

    assert router.strategy_kwargs == {"fallback_tag": "fast"}


@pytest.mark.level1
def test_unknown_model_name_is_rejected_instead_of_using_the_whole_group():
    assert IntelliRouterAllocator(_pool()).allocate("missing") is None


@pytest.mark.level1
def test_untagged_deployment_cannot_be_selected_by_its_model_name():
    config = IntelliRouterConfig(
        deployments=[
            IntelliRouterDeployment(model_name="fast", api_key="k", api_base="https://a.test", provider="deepseek"),
        ],
    )

    assert IntelliRouterAllocator(config.to_pool_entries()).allocate("fast") is None


@pytest.mark.level1
def test_tag_filtered_group_only_offers_its_own_tag():
    allocator = IntelliRouterAllocator(_pool(strategy="tag-filtered", strategy_kwargs={"fallback_tag": "fast"}))

    assert allocator.list_route_tags() == ["fast"]
    # Pinning to another model would bypass the group's own restriction.
    assert allocator.allocate("smart") is None


@pytest.mark.level0
def test_list_route_tags_is_sorted_and_unique():
    assert IntelliRouterAllocator(_pool()).list_route_tags() == ["fast", "smart"]


@pytest.mark.level0
def test_resolve_member_model_reapplies_the_persisted_tag():
    resolved = resolve_member_model(_team_spec(_pool()), model_name="*", model_index=0, route_tag="smart")

    assert _router(resolved).strategy == "tag-filtered"
    assert _router(resolved).strategy_kwargs == {"fallback_tag": "smart"}


@pytest.mark.level1
def test_resolve_member_model_drops_a_tag_the_group_no_longer_serves():
    resolved = resolve_member_model(_team_spec(_pool()), model_name="*", model_index=0, route_tag="retired")

    assert _router(resolved).strategy == "ordered-failover"


@pytest.mark.level0
def test_member_options_round_trip_the_route_tag():
    allocation = IntelliRouterAllocator(_pool()).allocate("fast")

    options = load_member_options(build_member_options(model_ref=allocation.to_db_ref()))

    assert options.model_ref.model_name == "*"
    assert options.model_ref.route_tag == "fast"


@pytest.mark.level1
def test_member_options_without_a_tag_stay_untagged():
    raw = build_member_options(model_ref={"model_name": "gpt-4", "model_index": 1})

    assert load_member_options(raw).model_ref.route_tag is None
    assert "route_tag" not in raw
