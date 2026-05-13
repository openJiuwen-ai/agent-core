# coding: utf-8
"""Spec loader / SpecRegistry behaviour."""

from __future__ import annotations

import pytest
import yaml

from openjiuwen.agent_teams.cli.spec_loader import (
    SpecRegistry,
    _expand_env_vars,
    load_spec_yaml,
)
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.core.common.exception.errors import BaseError

pytestmark = pytest.mark.level0


def _make_spec(team_name: str) -> TeamAgentSpec:
    return TeamAgentSpec.model_validate(
        {
            "agents": {"leader": {}},
            "team_name": team_name,
        },
    )


def test_expand_env_vars_handles_str_dict_list_recursively(monkeypatch):
    monkeypatch.setenv("FOO", "bar")
    monkeypatch.setenv("API_KEY", "secret")

    result = _expand_env_vars(
        {
            "key": "${FOO}",
            "nested": {"k": "value-${API_KEY}"},
            "items": ["${FOO}", "${MISSING}", "literal"],
        },
    )

    assert result["key"] == "bar"
    assert result["nested"]["k"] == "value-secret"
    assert result["items"] == ["bar", "${MISSING}", "literal"]


def test_load_spec_yaml_strips_runtime_block(tmp_path):
    yaml_path = tmp_path / "team.yaml"
    yaml_path.write_text(
        yaml.safe_dump(
            {
                "agents": {"leader": {}},
                "team_name": "yaml_team",
                "runtime": {"session_id": "s1", "initial_query": "hi"},
            },
        ),
        encoding="utf-8",
    )

    spec, runtime = load_spec_yaml(yaml_path)

    assert isinstance(spec, TeamAgentSpec)
    assert spec.team_name == "yaml_team"
    assert runtime == {"session_id": "s1", "initial_query": "hi"}


def test_load_spec_yaml_returns_empty_runtime_when_absent(tmp_path):
    yaml_path = tmp_path / "team.yaml"
    yaml_path.write_text(
        yaml.safe_dump({"agents": {"leader": {}}, "team_name": "no_runtime"}),
        encoding="utf-8",
    )

    spec, runtime = load_spec_yaml(yaml_path)

    assert spec.team_name == "no_runtime"
    assert runtime == {}


def test_load_spec_yaml_raises_on_missing_file(tmp_path):
    missing = tmp_path / "does_not_exist.yaml"

    with pytest.raises(BaseError):
        load_spec_yaml(missing)


def test_spec_registry_inmemory_takes_priority_over_yaml(tmp_path, monkeypatch):
    yaml_path = tmp_path / "team.yaml"
    yaml_path.write_text(
        yaml.safe_dump(
            {
                "agents": {"leader": {}},
                "team_name": "shared",
            },
        ),
        encoding="utf-8",
    )
    registry = SpecRegistry()
    registry.add_inmemory(_make_spec("shared"))
    yaml_entry = registry.add_yaml(yaml_path)

    # yaml load should not displace the in-memory entry
    assert yaml_entry.source == "in-memory"
    assert registry.get("shared").source == "in-memory"


def test_spec_registry_bulk_register_uses_spec_team_name():
    registry = SpecRegistry()
    spec = _make_spec("real_name")

    registry.bulk_register({"declared_name": spec})

    # entry registered under spec.team_name, not the dict key
    assert registry.get("real_name") is not None
    assert registry.get("declared_name") is None


def test_spec_registry_lists_entries_in_insertion_order():
    registry = SpecRegistry()
    registry.add_inmemory(_make_spec("a"))
    registry.add_inmemory(_make_spec("b"))
    registry.add_inmemory(_make_spec("c"))

    assert registry.names() == ["a", "b", "c"]
