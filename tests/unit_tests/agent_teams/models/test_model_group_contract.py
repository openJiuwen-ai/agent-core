# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Boundary and cross-layer contract tests for compiler-driven model groups.

These tests intentionally exercise the public DTO boundary without starting a
real LLM client.  They protect the single logical ``*`` Team entry contract,
while also asserting that the legacy named-pool allocator remains unchanged.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openjiuwen.agent_teams.models.allocator import (
    ByModelNameAllocator,
    IntelliRouterAllocator,
    resolve_member_model,
)
from openjiuwen.agent_teams.models.pool import (
    INTELLI_ROUTER_PROVIDER,
    INTELLI_ROUTER_UNIFIED_MODEL,
    IntelliRouterConfig,
    IntelliRouterDeployment,
    ModelPoolEntry,
    materialize_model_group,
)
from openjiuwen.agent_teams.schema.team import TeamSpec
from openjiuwen.core.foundation.llm.schema.config import (
    IntelliRouterConfig as CoreRouterConfig,
)
from openjiuwen.core.foundation.llm.schema.config import (
    IntelliRouterDeploymentConfig as CoreDeploymentConfig,
)
from openjiuwen.core.foundation.llm.schema.config import (
    ModelClientConfig,
    ModelRequestConfig,
)


def _compiled_group(**kwargs):
    deployment = CoreDeploymentConfig(
        route_id="r1",
        model_id="m1",
        model_name="qwen-plus",
        provider="openai",
        api_key="secret",
        api_base="https://provider.example",
        request_defaults={"temperature": 0.2},
    )
    router = CoreRouterConfig(
        model_group_id="group-1",
        deployments=[deployment],
        strategy="ordered-failover",
        strategy_kwargs={"weight": 1},
        num_retries=2,
        timeout=15.0,
        enable_health_check=False,
        health_check_interval=300.0,
        enable_observability=False,
        web_dashboard_port=0,
    )
    client = ModelClientConfig(
        client_provider=INTELLI_ROUTER_PROVIDER,
        intelli_router=router,
        verify_ssl=False,
    )
    compiled = SimpleNamespace(
        selected_type="model_group",
        selected_id="group-1",
        model_client_config=client,
        model_request_config=ModelRequestConfig(model="must-not-leak", temperature=0.7),
    )
    for key, value in kwargs.items():
        setattr(compiled, key, value)
    return compiled


def _router_pool() -> list[ModelPoolEntry]:
    return IntelliRouterConfig(
        deployments=[
            IntelliRouterDeployment(
                id="r1",
                model_name="same",
                provider="openai",
                api_key="k1",
                api_base="https://a.example",
            ),
            IntelliRouterDeployment(
                id="r2",
                model_name="same",
                provider="anthropic",
                api_key="k2",
                api_base="https://b.example",
            ),
            IntelliRouterDeployment(
                id="r3",
                model_name="other",
                provider="openai",
                api_key="k3",
                api_base="https://c.example/v1",
            ),
        ],
    ).to_pool_entries()


def test_materialize_model_group_emits_json_safe_single_wildcard_entry():
    entry = materialize_model_group(_compiled_group())[0]
    assert entry.model_name == INTELLI_ROUTER_UNIFIED_MODEL
    assert entry.api_provider == INTELLI_ROUTER_PROVIDER
    assert entry.api_key == entry.api_base_url == ""
    assert entry.metadata["client"]["verify_ssl"] is False
    assert entry.metadata["request"] == {"temperature": 0.7}
    assert entry.metadata["client"]["intelli_router"]["deployments"][0]["route_id"] == "r1"
    json.dumps(entry.model_dump(mode="json"))


@pytest.mark.parametrize(
    "compiled, selection, message",
    [
        (None, None, "required"),
        (SimpleNamespace(selected_type="model", selected_id="m"), None, "selected_type"),
        (_compiled_group(selected_id="different"), SimpleNamespace(type="model_group", id="group-1"), "does not match"),
        (
            _compiled_group(model_client_config=SimpleNamespace(client_provider="OpenAI", intelli_router=None)),
            None,
            "intelli_router",
        ),
        (
            _compiled_group(
                model_client_config=SimpleNamespace(client_provider=INTELLI_ROUTER_PROVIDER, intelli_router=None)
            ),
            None,
            "deployments",
        ),
    ],
)
def test_materialize_model_group_rejects_invalid_compiler_contract(compiled, selection, message):
    with pytest.raises(ValueError, match=message):
        materialize_model_group(compiled, selection)


def test_materialize_model_group_rejects_empty_or_malformed_deployment_catalog():
    compiled = _compiled_group()
    compiled.model_client_config.intelli_router.deployments = []
    with pytest.raises(ValueError):
        materialize_model_group(compiled)
    for deployments in ([{"model_name": "", "provider": "openai"}], [{"model_name": "m", "provider": ""}]):
        compiled = _compiled_group()
        compiled.model_client_config.intelli_router.deployments = [
            CoreDeploymentConfig(route_id="r", model_name=d["model_name"], provider=d["provider"]) for d in deployments
        ]
        entry = materialize_model_group(compiled)[0]
        with pytest.raises(ValueError):
            IntelliRouterAllocator([entry])


def test_intelli_router_allocator_only_allocates_logical_entry_and_projects_real_models():
    allocator = IntelliRouterAllocator(_router_pool())
    assert allocator.allocate("*").entry.model_name == "*"
    assert allocator.allocate("same") is None
    assert allocator.allocate("other") is None
    assert allocator.allocate(provider_filter=lambda _: False).entry.model_name == "*"
    projected = allocator.list_cli_models(provider_filter=lambda provider: provider == "openai")
    assert [(item.model, item.provider) for item in projected] == [("same", "openai"), ("other", "openai")]
    assert [item.api_base for item in allocator.resolve_cli_models("same")] == [
        "https://a.example",
        "https://b.example",
    ]
    assert allocator.resolve_cli_models("*") == []
    assert allocator.resolve_cli_models("missing") == []


def test_intelli_router_cli_projection_is_snapshot_and_does_not_rewrite_api_base():
    pool = _router_pool()
    allocator = IntelliRouterAllocator(pool)
    pool[0].metadata["client"]["intelli_router"]["deployments"][0]["api_key"] = "rotated"
    candidate = allocator.resolve_cli_models("other")[0]
    assert candidate.api_base == "https://c.example/v1"
    assert candidate.api_key == "k3"
    assert candidate.route_id == "r3"


def test_intelli_router_allocator_rejects_non_single_star_shapes():
    pool = _router_pool()
    with pytest.raises(ValueError, match="exactly one"):
        IntelliRouterAllocator(pool + [pool[0]])
    named = pool[0].model_copy(update={"model_name": "physical"})
    with pytest.raises(ValueError, match="exactly one"):
        IntelliRouterAllocator([named])


def test_legacy_by_model_name_pool_and_checkpoint_coordinates_still_resolve():
    first = ModelPoolEntry(model_name="legacy", api_key="k1", api_base_url="https://a", api_provider="OpenAI")
    second = ModelPoolEntry(model_name="legacy", api_key="k2", api_base_url="https://b", api_provider="OpenAI")
    allocator = ByModelNameAllocator([first, second])
    assert allocator.allocate("legacy").entry.api_key == "k1"
    assert allocator.allocate("legacy").entry.api_key == "k2"
    spec = TeamSpec(
        team_name="legacy", display_name="legacy", model_pool=[first, second], model_pool_strategy="by_model_name"
    )
    assert resolve_member_model(spec, model_name="legacy", model_index=1).model_client_config.api_key == "k2"
    assert resolve_member_model(spec, model_name="legacy", model_index=99).model_client_config.api_key == "k1"
    assert resolve_member_model(spec, model_name="missing", model_index=0) is None


def test_intelli_router_config_is_one_entry_even_with_duplicate_physical_names():
    entries = _router_pool()
    assert [entry.model_name for entry in entries] == ["*"]
    assert len(entries[0].metadata["client"]["intelli_router"]["deployments"]) == 3
