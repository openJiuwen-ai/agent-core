# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for openjiuwen.extensions.context_evolver.offline_memory.role_normalizer."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.extensions.context_evolver.offline_memory.role_normalizer import RoleNormalizer

from tests.unit_tests.fixtures.mock_llm import create_json_response, mock_llm_context


@dataclass
class _Member:
    member_name: str
    display_name: str
    desc: str


def _model() -> Model:
    return Model(
        ModelClientConfig(client_provider="OpenAI", api_key="mock-api-key", api_base="http://mock", verify_ssl=False),
        ModelRequestConfig(model_name="mock-model"),
    )


class TestConstruction:
    def test_invalid_mode_raises(self) -> None:
        with pytest.raises(ValueError):
            RoleNormalizer("bogus")

    def test_dynamic_without_taxonomy_path_raises(self) -> None:
        with pytest.raises(ValueError):
            RoleNormalizer("dynamic", model=_model())

    def test_dynamic_without_model_raises(self, tmp_path) -> None:
        with pytest.raises(ValueError):
            RoleNormalizer("dynamic", tmp_path / "role_taxonomy.yaml")

    def test_dynamic_seeds_in_memory_taxonomy_from_seed(self, tmp_path) -> None:
        # Loading a missing taxonomy_path falls back to SEED_TAXONOMY and
        # eagerly persists it, so the file exists right after construction
        # (see RoleNormalizer.__init__).
        normalizer = RoleNormalizer("dynamic", tmp_path / "role_taxonomy.yaml", model=_model())
        assert "leader" in normalizer._taxonomy
        assert (tmp_path / "role_taxonomy.yaml").exists()


class TestPredefinedMode:
    @pytest.mark.asyncio
    async def test_resolve_is_identity_no_llm_call(self) -> None:
        normalizer = RoleNormalizer("predefined")
        with mock_llm_context() as mock_llm:
            role_type = await normalizer.resolve(_Member("researcher_1", "Researcher", "gathers evidence"))
        assert role_type == "researcher_1"
        assert mock_llm.call_count == 0


class TestDynamicMode:
    @pytest.mark.asyncio
    async def test_classifies_into_existing_taxonomy_entry(self, tmp_path) -> None:
        normalizer = RoleNormalizer("dynamic", tmp_path / "role_taxonomy.yaml", model=_model())
        with mock_llm_context() as mock_llm:
            mock_llm.set_responses([create_json_response({"role_type": "researcher", "is_new": False})])
            role_type = await normalizer.resolve(_Member("moe-expert", "MoE Expert", "researches MoE architectures"))
        assert role_type == "researcher"

    @pytest.mark.asyncio
    async def test_new_role_type_persisted_to_taxonomy(self, tmp_path) -> None:
        taxonomy_path = tmp_path / "role_taxonomy.yaml"
        normalizer = RoleNormalizer("dynamic", taxonomy_path, model=_model())
        with mock_llm_context() as mock_llm:
            mock_llm.set_responses([create_json_response({
                "role_type": "fact_researcher",
                "is_new": True,
                "new_description": "Verifies factual claims against sources.",
            })])
            role_type = await normalizer.resolve(_Member("fact-checker", "Fact Checker", "verifies claims"))
        assert role_type == "fact_researcher"

        from openjiuwen.extensions.context_evolver.offline_memory import bank_io

        taxonomy = bank_io.load_yaml(taxonomy_path)
        assert taxonomy["fact_researcher"] == "Verifies factual claims against sources."

    @pytest.mark.asyncio
    async def test_same_persona_seen_twice_only_costs_one_call(self, tmp_path) -> None:
        normalizer = RoleNormalizer("dynamic", tmp_path / "role_taxonomy.yaml", model=_model())
        with mock_llm_context() as mock_llm:
            mock_llm.set_responses([create_json_response({"role_type": "researcher", "is_new": False})])
            member = _Member("moe-expert", "MoE Expert", "researches MoE architectures")
            first = await normalizer.resolve(member)
            second = await normalizer.resolve(member)
        assert first == second == "researcher"
        assert mock_llm.call_count == 1

    @pytest.mark.asyncio
    async def test_empty_role_type_falls_back_to_domain_specialist(self, tmp_path) -> None:
        normalizer = RoleNormalizer("dynamic", tmp_path / "role_taxonomy.yaml", model=_model())
        with mock_llm_context() as mock_llm:
            mock_llm.set_responses([create_json_response({"role_type": "", "is_new": False})])
            role_type = await normalizer.resolve(_Member("mystery", "Mystery Persona", "unclear function"))
        assert role_type == "domain_specialist"

    @pytest.mark.asyncio
    async def test_resolve_roster_maps_every_member(self, tmp_path) -> None:
        normalizer = RoleNormalizer("dynamic", tmp_path / "role_taxonomy.yaml", model=_model())
        with mock_llm_context() as mock_llm:
            mock_llm.set_responses([
                create_json_response({"role_type": "researcher", "is_new": False}),
                create_json_response({"role_type": "writer", "is_new": False}),
            ])
            roster = [
                _Member("moe-expert", "MoE Expert", "researches MoE"),
                _Member("copy-editor", "Copy Editor", "writes the final doc"),
            ]
            mapping = await normalizer.resolve_roster(roster)
        assert mapping == {"moe-expert": "researcher", "copy-editor": "writer"}
