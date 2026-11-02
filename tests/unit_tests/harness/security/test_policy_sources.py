"""The source catalog and settings schema shared with security hosts."""

import pytest

from openjiuwen.harness.security.permission_engine.policy_sources import (
    DEFAULT_LAYER_ORDER,
    DEFAULT_SOURCE_ORDER,
    POLICY_SOURCE_IDS,
    validate_policy_sources,
    validate_source_order,
)


def test_source_catalog_has_default_priority():
    assert DEFAULT_SOURCE_ORDER == ("user", "cloud", "builtin")
    assert DEFAULT_LAYER_ORDER == ("user", "user_group", "cloud", "cloud_group", "builtin", "builtin_group")
    assert POLICY_SOURCE_IDS == frozenset(DEFAULT_LAYER_ORDER)


def test_policy_source_settings_allow_reorder_disable_and_omission():
    sources = [{"id": "cloud", "enabled": False}, {"id": "user", "enabled": True}]
    assert validate_policy_sources(sources) == sources
    assert validate_policy_sources([]) == []


@pytest.mark.parametrize("sources, message", [
    ({}, "policy_sources must be a list"),
    ([{"id": "user"}], "each policy source requires id and enabled"),
    ([{"id": "user", "enabled": 1}], "policy source enabled must be boolean"),
    ([{"id": "other", "enabled": True}], "unknown or duplicate policy source"),
    ([{"id": "user", "enabled": True}, {"id": "user", "enabled": False}],
     "unknown or duplicate policy source"),
])
def test_policy_source_settings_reject_invalid_shape(sources, message):
    with pytest.raises(ValueError, match=message):
        validate_policy_sources(sources)


@pytest.mark.parametrize("order", [[], ["builtin", "user"], ["cloud"]])
def test_runtime_source_order_allows_selected_sources(order):
    assert validate_source_order(order) == order


@pytest.mark.parametrize("order", [None, "user", ["user", "user"], ["other"], [1]])
def test_runtime_source_order_rejects_invalid_shape(order):
    with pytest.raises(ValueError, match="invalid permissions.composition source_order"):
        validate_source_order(order)


@pytest.mark.parametrize("schema_version", [True, 0, 3, "2"])
def test_runtime_source_order_rejects_unknown_schema_version(schema_version):
    with pytest.raises(ValueError, match="schema_version"):
        validate_source_order([], schema_version=schema_version)
