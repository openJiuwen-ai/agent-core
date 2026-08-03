# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for team_factory: spec assembly, skill-path resolution, and agent_customizer."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openjiuwen.agent_teams.reliability.anomaly import Severity
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.harness.rails.evolution.trajectory_rail import TrajectoryRail
from openjiuwen.harness.rails.skills.skill_use_rail import SkillUseRail
from openjiuwen.rsi.config import EvaluatorConfig
from openjiuwen.rsi.evaluator import team_factory as tf
from openjiuwen.rsi.evaluator.eval_team_rail import EvalTeamRail
from openjiuwen.rsi.evaluator.team_factory import (
    DEFAULT_SKILL_MD_FILENAME,
    DEFAULT_TEAM_NAME,
    EVAL_TEAM_DB_FILENAME,
    TeamSkillTeamFactory,
    get_team_spec_customizer,
    resolve_team_name_from_skill_path,
)

pytestmark = pytest.mark.level0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _eval_config(**overrides: object) -> EvaluatorConfig:
    base = {"model_config_ref": ""}
    base.update(overrides)
    return EvaluatorConfig(**base)


def _write_model_config(
    tmp_path: Path,
    *,
    timeout: float | None = None,
    max_retries: int | None = None,
    request_lines: list[str] | None = None,
) -> Path:
    path = tmp_path / "model.yaml"
    client_lines = [
        "  model_client_config:",
        "    client_provider: OpenAI",
        "    api_base: http://127.0.0.1:8000/v1",
        "    api_key: test-key",
        "    verify_ssl: false",
    ]
    if timeout is not None:
        client_lines.append(f"    timeout: {timeout}")
    if max_retries is not None:
        client_lines.append(f"    max_retries: {max_retries}")
    path.write_text(
        "\n".join(
            [
                "model:",
                *client_lines,
                "  model_request_config:",
                "    model: glm-5",
                *(request_lines or []),
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _write_skill_md(
    directory: Path,
    *,
    team_name: str | None = "math_tutoring_team",
    body: str = "# team skill\n",
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    if team_name is None:
        frontmatter = "---\n---\n"
    else:
        frontmatter = f"---\nname: {team_name}\n---\n"
    skill_md = directory / DEFAULT_SKILL_MD_FILENAME
    skill_md.write_text(frontmatter + body, encoding="utf-8")
    return skill_md


class _FakeRail:
    """Minimal rail stand-in for harness rail replacement tests."""


class _FakeAgent:
    """Minimal DeepAgent stand-in that tracks pending rails."""

    def __init__(self) -> None:
        self._pending_rails: list[object] = []
        self.stripped_types: list[tuple[type, ...]] = []
        self.bound_harnesses: list[object] = []
        self.loaded_harness_paths: list[str] = []

    def add_rail(self, rail: object) -> _FakeAgent:
        self._pending_rails.append(rail)
        return self

    def strip_rails_by_type(self, rail_types: tuple[type, ...]) -> int:
        self.stripped_types.append(rail_types)
        before = len(self._pending_rails)
        self._pending_rails = [rail for rail in self._pending_rails if not isinstance(rail, rail_types)]
        return before - len(self._pending_rails)

    def bind_expert_harness(self, harness: object) -> None:
        self.bound_harnesses.append(harness)
        rails = list(getattr(harness, "rails", []) or [])
        if not rails:
            return
        self.strip_rails_by_type(tuple(type(rail) for rail in rails))
        self._pending_rails.extend(rails)

    async def load_plugin(self, path: str) -> None:
        self.loaded_harness_paths.append(path)

    def find_rails_by_type(self, rail_types: tuple[type, ...]) -> list[object]:
        return [rail for rail in self._pending_rails if isinstance(rail, rail_types)]


class _FakeLogger:
    """Capture loguru-style logger calls for diagnostics assertions."""

    def __init__(self) -> None:
        self.infos: list[str] = []
        self.warnings: list[str] = []

    def info(self, message: str, *args: object) -> None:
        self.infos.append(message.format(*args))

    def warning(self, message: str, *args: object) -> None:
        self.warnings.append(message.format(*args))


def _rails_of_type(agent: _FakeAgent, rail_type: type) -> list[object]:
    return [rail for rail in agent._pending_rails if isinstance(rail, rail_type)]


def _factory(config: EvaluatorConfig | None = None) -> TeamSkillTeamFactory:
    return TeamSkillTeamFactory(config=config or _eval_config())


def _build_customizer(
    factory: TeamSkillTeamFactory,
    *,
    team_skill_ref_path: str | Path | None = None,
    harness_refs: dict[str, str] | None = None,
    trajectory_dir: str | Path | None = None,
    workspace_dir: Path | None = None,
    team_name: str = DEFAULT_TEAM_NAME,
):
    return factory._build_agent_customizer(
        team_skill_ref_path=team_skill_ref_path,
        harness_refs=harness_refs or {},
        trajectory_dir=trajectory_dir,
        case_dir=workspace_dir,
        team_name=team_name,
    )


# ---------------------------------------------------------------------------
# _build_spec_from_config
# ---------------------------------------------------------------------------


class TestBuildSpecFromConfig:
    def test_minimal_inprocess_spec(self, tmp_path: Path) -> None:
        spec = tf._build_spec_from_config(_eval_config(model_config_ref=str(_write_model_config(tmp_path))))

        assert isinstance(spec, TeamAgentSpec)
        assert spec.team_name == DEFAULT_TEAM_NAME
        assert spec.spawn_mode == "inprocess"
        assert spec.lifecycle == "temporary"
        assert spec.transport.type == "inprocess"
        assert "leader" in spec.agents
        assert "teammate" in spec.agents
        leader_model = spec.agents["leader"].model
        teammate_model = spec.agents["teammate"].model
        assert leader_model is not None
        assert teammate_model is not None
        assert leader_model.model_request_config.model_name == "glm-5"
        assert teammate_model.model_request_config.model_name == "glm-5"
        assert leader_model.model_client_config.api_base == "http://127.0.0.1:8000/v1"

    def test_team_name_override(self, tmp_path: Path) -> None:
        spec = tf._build_spec_from_config(
            _eval_config(model_config_ref=str(_write_model_config(tmp_path))),
            team_name="custom_team",
        )
        assert spec.team_name == "custom_team"

    def test_output_dir_enables_team_workspace_and_role_stable_base(self, tmp_path: Path) -> None:
        ws = tmp_path / "case" / "workspace"
        spec = tf._build_spec_from_config(
            _eval_config(model_config_ref=str(_write_model_config(tmp_path))),
            output_dir=ws,
            team_name="scoped_team",
        )

        assert spec.workspace is not None
        assert spec.workspace.enabled is True
        leader_ws = spec.agents["leader"].workspace
        teammate_ws = spec.agents["teammate"].workspace
        assert leader_ws is not None
        assert teammate_ws is not None
        assert leader_ws.stable_base is True
        assert teammate_ws.stable_base is True

    def test_output_dir_mounts_sys_operation_rail_for_team_agents(self, tmp_path: Path) -> None:
        spec = tf._build_spec_from_config(
            _eval_config(model_config_ref=str(_write_model_config(tmp_path))),
            output_dir=tmp_path / "case_001",
            team_name="fs_team",
        )

        leader_rails = [rail.type for rail in spec.agents["leader"].rails or []]
        teammate_rails = [rail.type for rail in spec.agents["teammate"].rails or []]
        assert "core.sys_operation" in leader_rails
        assert "core.sys_operation" in teammate_rails

    def test_output_dir_pins_case_scoped_sqlite(self, tmp_path: Path) -> None:
        case_dir = tmp_path / "case_001"
        spec = tf._build_spec_from_config(
            _eval_config(model_config_ref=str(_write_model_config(tmp_path))),
            output_dir=case_dir,
            team_name="db_team",
        )

        assert spec.storage is not None
        assert spec.storage.type == "sqlite"
        expected_db = (case_dir / EVAL_TEAM_DB_FILENAME).resolve()
        assert spec.storage.params["connection_string"] == str(expected_db)

    def test_model_router_when_model_config_ref_present(self, tmp_path: Path) -> None:
        spec = tf._build_spec_from_config(
            _eval_config(model_config_ref=str(_write_model_config(tmp_path))),
            team_name="router_team",
        )

        assert spec.model_router is not None
        assert spec.model_router.model_names == ["glm-5"]
        assert spec.model_router.api_base_url == "http://127.0.0.1:8000/v1"
        assert spec.model_router.api_key == "test-key"
        assert spec.model_pool_strategy == "router"

    def test_model_router_threads_timeout_and_max_retries(self, tmp_path: Path) -> None:
        spec = tf._build_spec_from_config(
            _eval_config(model_config_ref=str(_write_model_config(tmp_path, timeout=1800.0, max_retries=5))),
            team_name="router_team",
        )

        assert spec.model_router is not None
        client_meta = spec.model_router.metadata["client"]
        assert client_meta["verify_ssl"] is False
        assert client_meta["timeout"] == 1800.0
        assert client_meta["max_retries"] == 5

    def test_execution_model_disables_provider_reasoning_by_default(self, tmp_path: Path) -> None:
        spec = tf._build_spec_from_config(
            _eval_config(model_config_ref=str(_write_model_config(tmp_path))),
            team_name="router_team",
        )

        expected_extra_body = {
            "thinking": {"type": "disabled"},
            "enable_thinking": False,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        assert spec.agents["leader"].model.model_request_config.model_dump()["extra_body"] == expected_extra_body
        assert spec.agents["teammate"].model.model_request_config.model_dump()["extra_body"] == expected_extra_body
        assert spec.model_router.metadata["request"]["extra_body"] == expected_extra_body

    def test_execution_model_preserves_explicit_reasoning_config(self, tmp_path: Path) -> None:
        spec = tf._build_spec_from_config(
            _eval_config(
                model_config_ref=str(
                    _write_model_config(
                        tmp_path,
                        request_lines=[
                            "    extra_body:",
                            "      thinking:",
                            "        type: enabled",
                            "      chat_template_kwargs:",
                            "        enable_thinking: true",
                        ],
                    )
                )
            ),
            team_name="router_team",
        )

        extra_body = spec.agents["teammate"].model.model_request_config.model_dump()["extra_body"]
        assert extra_body["thinking"] == {"type": "enabled"}
        assert extra_body["chat_template_kwargs"]["enable_thinking"] is True
        assert extra_body["enable_thinking"] is False

    def test_model_router_expands_env_api_key(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        model_config = tmp_path / "model.yaml"
        model_config.write_text(
            "\n".join(
                [
                    "model_client_config:",
                    "  client_provider: OpenAI",
                    "  api_base: https://token-plan.example/v1",
                    "  api_key: ${TOKEN_PLAN_API_KEY}",
                    "model_request_config:",
                    "  model: deepseek-v4-flash",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("TOKEN_PLAN_API_KEY", "expanded-token-plan-key")

        spec = tf._build_spec_from_config(
            _eval_config(model_config_ref=str(model_config)),
            team_name="router_team",
        )

        assert spec.model_router is not None
        assert spec.model_router.api_key == "expanded-token-plan-key"
        assert spec.model_router.api_key != "${TOKEN_PLAN_API_KEY}"

    def test_no_model_router_without_model_config_ref(self) -> None:
        spec = tf._build_spec_from_config(EvaluatorConfig(), team_name="no_router")
        assert spec.model_router is None

    def test_enables_local_only_execution_reliability_for_evaluation_team(self) -> None:
        spec = tf._build_spec_from_config(EvaluatorConfig(), team_name="reliable-eval")

        assert spec.reliability is not None
        assert spec.reliability.enabled is True
        detectors = spec.reliability.detectors
        assert detectors.tool_error.enabled is False
        assert detectors.model_error.enabled is False
        assert detectors.output_length.enabled is True
        assert detectors.compaction.enabled is False
        assert detectors.pingpong.enabled is False
        repeat = detectors.repeat_tool
        assert repeat.enabled is True
        assert repeat.history_size == 36
        assert repeat.repeat_warn == 3
        assert repeat.loop_block == 6
        assert repeat.global_stop == 12
        actions = spec.reliability.policy.severity_actions
        assert [action.value for action in actions[Severity.LOW]] == ["local_steer", "observe_only"]
        for severity in (Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL):
            assert [action.value for action in actions[severity]] == ["local_steer"]


# ---------------------------------------------------------------------------
# Skill path resolution
# ---------------------------------------------------------------------------


class TestResolveTeamSkillDir:
    def test_directory_ref_returns_self(self, tmp_path: Path) -> None:
        team_skill_dir = tmp_path / "team_skill"
        team_skill_dir.mkdir()
        assert Path(tf._resolve_team_skill_dir(team_skill_dir)) == team_skill_dir.resolve()

    def test_file_ref_returns_parent_directory(self, tmp_path: Path) -> None:
        team_skill_dir = tmp_path / "team_skill"
        team_skill_dir.mkdir()
        marker = team_skill_dir / "notes.yaml"
        marker.write_text("x", encoding="utf-8")
        assert Path(tf._resolve_team_skill_dir(marker)) == team_skill_dir.resolve()

    def test_skill_md_ref_returns_parent_even_when_missing(self, tmp_path: Path) -> None:
        team_skill_dir = tmp_path / "team_skill"
        skill_md = team_skill_dir / DEFAULT_SKILL_MD_FILENAME
        assert Path(tf._resolve_team_skill_dir(skill_md)) == team_skill_dir.resolve()


class TestResolveTeamSkillMdPath:
    def test_directory_ref_appends_skill_md(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "team_skill"
        _write_skill_md(skill_dir)
        resolved = tf._resolve_team_skill_md_path(skill_dir)
        assert resolved == (skill_dir / DEFAULT_SKILL_MD_FILENAME).resolve()

    def test_skill_md_file_ref_returns_self(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "team_skill"
        skill_md = _write_skill_md(skill_dir, team_name="direct")
        assert tf._resolve_team_skill_md_path(skill_md) == skill_md.resolve()


class TestResolveTeamSkillRailConfig:
    def test_directory_ref_mounts_parent_root_and_enables_skill(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "team_skill"
        skill_dir.mkdir()

        rail_config = tf.resolve_team_skill_rail_config(skill_dir)

        assert Path(rail_config.skills_root) == tmp_path.resolve()
        assert rail_config.enabled_skill == "team_skill"

    def test_skill_md_ref_uses_same_mount_config(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "team_skill"
        skill_md = _write_skill_md(skill_dir, team_name="team_skill")

        rail_config = tf.resolve_team_skill_rail_config(skill_md)

        assert Path(rail_config.skills_root) == tmp_path.resolve()
        assert rail_config.enabled_skill == "team_skill"


class TestReadTeamNameFromSkillMd:
    def test_reads_name_from_frontmatter(self, tmp_path: Path) -> None:
        skill_md = _write_skill_md(tmp_path / "skill", team_name="  padded_name  ")
        assert tf._read_team_name_from_skill_md(skill_md) == "padded_name"

    def test_returns_none_when_file_missing(self, tmp_path: Path) -> None:
        assert tf._read_team_name_from_skill_md(tmp_path / "missing" / "SKILL.md") is None

    def test_returns_none_without_yaml_frontmatter(self, tmp_path: Path) -> None:
        path = tmp_path / "plain.md"
        path.write_text("# no frontmatter\n", encoding="utf-8")
        assert tf._read_team_name_from_skill_md(path) is None

    def test_returns_none_when_name_missing_or_blank(self, tmp_path: Path) -> None:
        blank = tmp_path / "blank"
        _write_skill_md(blank, team_name="   ")
        assert tf._read_team_name_from_skill_md(blank / "SKILL.md") is None


class TestResolveTeamNameFromSkillPath:
    def test_empty_path_uses_default(self) -> None:
        assert resolve_team_name_from_skill_path(None) == DEFAULT_TEAM_NAME
        assert resolve_team_name_from_skill_path("") == DEFAULT_TEAM_NAME

    def test_reads_name_from_skill_directory(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "team_skill"
        _write_skill_md(skill_dir, team_name="from_skill")
        assert resolve_team_name_from_skill_path(skill_dir) == "from_skill"

    def test_reads_name_when_ref_is_skill_md_file(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "team_skill"
        skill_md = _write_skill_md(skill_dir, team_name="from_file")
        assert resolve_team_name_from_skill_path(skill_md) == "from_file"

    def test_falls_back_on_missing_skill_md(self, tmp_path: Path) -> None:
        empty_dir = tmp_path / "empty_skill"
        empty_dir.mkdir()
        assert resolve_team_name_from_skill_path(empty_dir) == DEFAULT_TEAM_NAME

    def test_falls_back_on_parse_errors(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        skill_dir = tmp_path / "team_skill"
        skill_dir.mkdir()

        def _boom(_path: Path) -> Path:
            raise OSError("read failed")

        monkeypatch.setattr(tf, "_resolve_team_skill_md_path", _boom)
        assert resolve_team_name_from_skill_path(skill_dir) == DEFAULT_TEAM_NAME


# ---------------------------------------------------------------------------
# TeamSkillTeamFactory — spec assembly
# ---------------------------------------------------------------------------


class TestTeamSkillTeamFactorySpecAssembly:
    def test_build_base_spec_has_no_customizer(self) -> None:
        spec = _factory().build_base_spec()
        assert isinstance(spec, TeamAgentSpec)
        assert get_team_spec_customizer(spec) is None
        assert spec.team_name == DEFAULT_TEAM_NAME

    def test_load_team_name_from_skill_path(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "team_skill"
        _write_skill_md(skill_dir, team_name="loaded_name")
        assert _factory().load_team_name_from_skill_path(skill_dir) == "loaded_name"

    def test_load_team_name_returns_none_when_unreadable(self, tmp_path: Path) -> None:
        assert _factory().load_team_name_from_skill_path(tmp_path / "missing") is None

    def test_create_team_spec_wires_customizer_and_team_name(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "team_skill"
        _write_skill_md(skill_dir, team_name="case_team")
        output_dir = tmp_path / "case_001"

        spec = _factory().create_team_spec(
            team_skill_ref_path=skill_dir,
            harness_refs={},
            output_dir=output_dir,
        )

        assert spec.team_name == "case_team"
        assert spec.spawn_mode == "inprocess"
        assert get_team_spec_customizer(spec) is not None
        assert callable(get_team_spec_customizer(spec))
        assert spec.workspace is not None
        assert spec.storage is not None
        assert str(output_dir / EVAL_TEAM_DB_FILENAME) in spec.storage.params["connection_string"]

    def test_create_team_spec_does_not_mutate_build_base_spec(self, tmp_path: Path) -> None:
        factory = _factory()
        base = factory.build_base_spec()
        case = factory.create_team_spec(
            team_skill_ref_path=tmp_path / "team_skill",
            output_dir=tmp_path / "case_001",
        )
        assert get_team_spec_customizer(base) is None
        assert get_team_spec_customizer(case) is not None
        assert case is not base

    def test_create_team_spec_customizer_excluded_from_model_dump(self, tmp_path: Path) -> None:
        spec = _factory().create_team_spec(output_dir=tmp_path / "case_001")
        assert "agent_customizer" not in spec.model_dump()

    def test_create_team_spec_does_not_predefine_team_leader_role_from_skill(
        self,
        tmp_path: Path,
    ) -> None:
        skill_dir = tmp_path / "team_skill"
        roles_dir = skill_dir / "roles"
        roles_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            """---
name: webpage_team
roles:
  - id: team_leader
    kind: ai_agent
    purpose: Coordinate work.
    skills: []
    tools: []
  - id: builder
    kind: ai_agent
    purpose: Build artifacts.
    skills: []
    tools: []
---

# Webpage Team
""",
            encoding="utf-8",
        )

        spec = _factory().create_team_spec(
            team_skill_ref_path=skill_dir,
            output_dir=tmp_path / "case",
        )

        assert [member.member_name for member in spec.predefined_members] == ["builder"]


# ---------------------------------------------------------------------------
# agent_customizer closure
# ---------------------------------------------------------------------------


class TestAgentCustomizer:
    def test_mounts_skill_use_rail_for_all_members(self, tmp_path: Path) -> None:
        team_skill_dir = tmp_path / "team_skill"
        team_skill_dir.mkdir()
        customizer = _build_customizer(_factory(), team_skill_ref_path=team_skill_dir)

        leader_agent = _FakeAgent()
        teammate_agent = _FakeAgent()
        customizer(leader_agent, "team_leader", "leader")
        customizer(teammate_agent, "alice", "teammate")

        for agent in (leader_agent, teammate_agent):
            skill_rails = _rails_of_type(agent, SkillUseRail)
            assert len(skill_rails) == 1
            assert Path(skill_rails[0].skills_dir) == team_skill_dir.parent.resolve()
            assert skill_rails[0].skill_mode == SkillUseRail.SKILL_MODE_ALL
            assert skill_rails[0].enabled_skills == {team_skill_dir.name}

        assert _rails_of_type(leader_agent, SkillUseRail)[0].include_tools is False
        assert _rails_of_type(teammate_agent, SkillUseRail)[0].include_tools is True

    def test_skips_skill_use_rail_without_team_skill_ref(self) -> None:
        agent = _FakeAgent()
        _build_customizer(_factory(), team_skill_ref_path=None)(agent, "alice", "teammate")
        assert not _rails_of_type(agent, SkillUseRail)

    def test_mounts_trajectory_rail_per_member(self, tmp_path: Path) -> None:
        trajectory_dir = tmp_path / "case_001" / "tr"
        customizer = _build_customizer(
            _factory(),
            team_skill_ref_path=tmp_path / "team_skill",
            trajectory_dir=trajectory_dir,
        )

        agent = _FakeAgent()
        customizer(agent, "alice", "teammate")

        trajectory_rails = _rails_of_type(agent, TrajectoryRail)
        assert len(trajectory_rails) == 1
        store = trajectory_rails[0].trajectory_store
        assert store._base_dir == trajectory_dir.resolve()
        assert store._get_file_path(None) == trajectory_dir.resolve() / "alice.jsonl"

    def test_trajectory_uses_role_when_member_name_missing(self, tmp_path: Path) -> None:
        trajectory_dir = tmp_path / "case_001" / "tr"
        customizer = _build_customizer(
            _factory(),
            trajectory_dir=trajectory_dir,
        )

        agent = _FakeAgent()
        customizer(agent, None, "leader")

        trajectory_rails = _rails_of_type(agent, TrajectoryRail)
        store = trajectory_rails[0].trajectory_store
        assert store._base_dir == trajectory_dir.resolve()
        assert store._get_file_path(None) == trajectory_dir.resolve() / "leader.jsonl"

    def test_skips_trajectory_rail_when_dir_none(self) -> None:
        agent = _FakeAgent()
        _build_customizer(_factory(), trajectory_dir=None)(agent, "alice", "teammate")
        assert not _rails_of_type(agent, TrajectoryRail)

    def test_case_isolation_for_trajectory_stores(self, tmp_path: Path) -> None:
        factory = _factory()
        spec_a = factory.create_team_spec(
            team_skill_ref_path=tmp_path / "team_skill",
            output_dir=tmp_path / "case_a",
        )
        spec_b = factory.create_team_spec(
            team_skill_ref_path=tmp_path / "team_skill",
            output_dir=tmp_path / "case_b",
        )

        agent_a = _FakeAgent()
        agent_b = _FakeAgent()
        customizer_a = get_team_spec_customizer(spec_a)
        customizer_b = get_team_spec_customizer(spec_b)
        assert customizer_a is not None
        assert customizer_b is not None
        customizer_a(agent_a, "alice", "teammate")
        customizer_b(agent_b, "alice", "teammate")

        store_a = _rails_of_type(agent_a, TrajectoryRail)[0].trajectory_store
        store_b = _rails_of_type(agent_b, TrajectoryRail)[0].trajectory_store
        assert store_a._base_dir != store_b._base_dir
        assert store_a._base_dir.name == "tr"
        assert store_b._base_dir.name == "tr"
        assert store_a._get_file_path(None).name == "alice.jsonl"
        assert store_b._get_file_path(None).name == "alice.jsonl"

    def test_loads_harness_rails_for_matching_member_only(self, tmp_path: Path) -> None:
        harness_dir = tmp_path / "expert_harness"
        harness_dir.mkdir()
        customizer = _build_customizer(
            _factory(),
            harness_refs={"alice": str(harness_dir)},
        )

        matched = _FakeAgent()
        unmatched = _FakeAgent()
        customizer(matched, "alice", "teammate")
        customizer(unmatched, "bob", "teammate")

        matched_loaders = _rails_of_type(matched, tf._DeferredExpertHarnessLoadRail)
        assert len(matched_loaders) == 1
        assert matched_loaders[0].harness_path == str(harness_dir)
        assert not _rails_of_type(unmatched, tf._DeferredExpertHarnessLoadRail)

    def test_does_not_bind_business_harness_to_team_leader(self, tmp_path: Path) -> None:
        harness_dir = tmp_path / "leader_harness"
        harness_dir.mkdir()
        customizer = _build_customizer(
            _factory(),
            harness_refs={"team_leader": str(harness_dir)},
        )

        leader = _FakeAgent()
        customizer(leader, "team_leader", "leader")

        assert leader.bound_harnesses == []
        assert not _rails_of_type(leader, tf._DeferredExpertHarnessLoadRail)

    def test_replaces_same_type_harness_rails(self, tmp_path: Path) -> None:
        harness_dir = tmp_path / "expert_harness"
        harness_dir.mkdir()
        customizer = _build_customizer(
            _factory(),
            harness_refs={"alice": str(harness_dir)},
        )

        agent = _FakeAgent()
        old_rail = _FakeRail()
        agent._pending_rails.append(old_rail)
        customizer(agent, "alice", "teammate")

        assert old_rail in agent._pending_rails
        assert len(_rails_of_type(agent, tf._DeferredExpertHarnessLoadRail)) == 1
        assert agent.stripped_types == []

    def test_logs_harness_bind_resource_summary(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        harness_dir = tmp_path / "expert_harness"
        harness_dir.mkdir()
        fake_logger = _FakeLogger()
        monkeypatch.setattr(tf, "logger", fake_logger)

        customizer = _build_customizer(
            _factory(),
            harness_refs={"alice": str(harness_dir)},
        )
        agent = _FakeAgent()
        customizer(agent, "alice", "teammate")
        loader = _rails_of_type(agent, tf._DeferredExpertHarnessLoadRail)[0]
        loader.init(agent)
        import asyncio

        asyncio.run(loader.before_invoke(SimpleNamespace()))

        logs = "\n".join(fake_logger.infos)
        assert agent.loaded_harness_paths == [str(harness_dir)]
        assert "RSI member harness load start member=alice role=teammate" in logs
        assert f"harness_path={harness_dir}" in logs
        assert "RSI member harness load success member=alice role=teammate" in logs

    def test_skips_harness_rails_when_pkg_invalid(self, tmp_path: Path) -> None:
        class _FailingAgent(_FakeAgent):
            async def load_plugin(self, path: str) -> None:
                raise FileNotFoundError(path)

        customizer = _build_customizer(
            _factory(),
            team_skill_ref_path=tmp_path / "team_skill",
            harness_refs={"alice": str(tmp_path / "missing_harness")},
        )
        agent = _FailingAgent()
        customizer(agent, "alice", "teammate")
        loader = _rails_of_type(agent, tf._DeferredExpertHarnessLoadRail)[0]
        loader.init(agent)

        import asyncio

        with pytest.raises(FileNotFoundError):
            asyncio.run(loader.before_invoke(SimpleNamespace()))

    def test_attaches_eval_team_rail_when_case_dir_set(self, tmp_path: Path) -> None:
        case_dir = tmp_path / "case"
        customizer = _build_customizer(
            _factory(),
            workspace_dir=case_dir,
            team_name="eval_team",
        )

        agent = _FakeAgent()
        customizer(agent, "leader", "leader")

        eval_rails = _rails_of_type(agent, EvalTeamRail)
        assert len(eval_rails) == 1
        assert eval_rails[0]._team_name == "eval_team"
        assert eval_rails[0]._workspace_dir.name == "team-workspace"
        assert eval_rails[0]._dest_dir == case_dir / "artifacts"

    def test_is_synchronous_no_asyncio_run(self, tmp_path: Path) -> None:
        spec = _factory().create_team_spec(
            team_skill_ref_path=tmp_path / "team_skill",
            output_dir=tmp_path / "case_001",
        )
        agent = _FakeAgent()
        customizer = get_team_spec_customizer(spec)
        assert customizer is not None
        with patch("asyncio.run") as asyncio_run:
            customizer(agent, "alice", "teammate")
        asyncio_run.assert_not_called()
