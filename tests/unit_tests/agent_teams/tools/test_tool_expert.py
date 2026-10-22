# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Leader expert tools: view_experts / view_expert_groups / spawn_expert (SDD-0032)."""

from __future__ import annotations

import builtins
import json
import pathlib
import shutil
import threading
from pathlib import Path
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.agent.agent_configurator import AgentConfigurator
from openjiuwen.agent_teams.agent.spawn_manager import _template_fields
from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.experts import (
    PackageLocation,
    apply_template_snapshot,
    bound_agent_group,
    compose_template_member,
    load_agent_group_package,
)
from openjiuwen.agent_teams.external import external_cli_agent_spec_from_template, merge_external_cli_config
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.rails.team_context import ExpertBinding
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.schema.team import (
    ExternalCliAgentSpec,
    ExternalCliBuiltinModel,
    ExternalCliMemberSpec,
    TeamMemberSpec,
    TeamRole,
)
from openjiuwen.agent_teams.spawn.external_cli_spawn import _external_cli_config_for_member
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase
from openjiuwen.agent_teams.tools.locales import make_translator
from openjiuwen.agent_teams.tools.member_options import get_member_agent_template, get_member_options
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.tool_factory import create_team_tools
from openjiuwen.agent_teams.tools.tool_member import SpawnTeammateTool
from openjiuwen.agent_teams.tools.tool_permissions import EXPERT_TOOLS
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.schema.deep_agent_spec import DeepAgentSpec, WorkspaceSpec

_TEAM = "expert_team"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _expert_package(root: Path, name: str, **fields: Any) -> Path:
    persona = root / "persona" / "identity.md"
    persona.parent.mkdir(parents=True, exist_ok=True)
    persona.write_text(f"You are {name}, working in {{{{ workspace }}}}.\n", encoding="utf-8")
    skill = root / "skills" / f"{name}-skill" / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text("---\nname: s\n---\n", encoding="utf-8")
    payload: dict[str, Any] = {
        "package_type": "agent_template",
        "name": name,
        "description": f"{name} description",
        "display_name": {"zh": f"{name} 专家", "en": f"{name} expert"},
        "category": "Testing",
        "version": "1.0.0",
        "persona": {"dir": "persona"},
        "skills": [{"dir": f"skills/{name}-skill"}],
    }
    payload.update(fields)
    _write_json(root / "manifest.json", payload)
    return root


def _group_package(root: Path) -> Path:
    _write_json(
        root / "manifest.json",
        {
            "package_type": "agent_group",
            "name": root.name,
            "display_name": {"zh": "设计团", "en": "Designers"},
            "description": "A design group",
            "instruction": "Collaborate on shots.",
            "tags": [{"id": "video"}],
            "agents": ["leader", "clip", "cli-member"],
        },
    )
    _expert_package(root / "agents" / "leader", "leader", persona={"dir": "."})
    (root / "agents" / "leader" / "AGENT.md").write_text("# leader rules\n", encoding="utf-8")
    _expert_package(root / "agents" / "clip", "clip", display_name={"zh": "视频镜头师", "en": "Shot Artist"})
    _expert_package(
        root / "agents" / "cli-member",
        "cli-member",
        runtime={"provider_name": "claudecode", "config": {"claude_turn_idle_timeout_s": 30}},
    )
    return root


class FakeCatalog:
    """In-memory ExpertCatalog over test package directories."""

    def __init__(self, experts: dict[str, Path], groups: dict[str, Path]):
        self.experts = experts
        self.groups = groups
        self.calls = 0

    async def list_expert_packages(self) -> list[PackageLocation]:
        self.calls += 1
        return [PackageLocation(name, path, "local") for name, path in self.experts.items()]

    async def locate_expert(self, name: str) -> PackageLocation:
        self.calls += 1
        if name not in self.experts:
            raise ValueError(f"expert {name!r} is not installed")
        return PackageLocation(name, self.experts[name], "builtin")

    async def list_group_packages(self) -> list[PackageLocation]:
        self.calls += 1
        return [PackageLocation(name, path, "builtin") for name, path in self.groups.items()]

    async def locate_group(self, name: str) -> PackageLocation:
        self.calls += 1
        if name not in self.groups:
            raise ValueError(f"group {name!r} is not installed")
        return PackageLocation(name, self.groups[name], "builtin")


@pytest.fixture
def packages(tmp_path: Path) -> dict[str, Path]:
    broken = tmp_path / "experts" / "broken"
    broken.mkdir(parents=True)
    (broken / "manifest.json").write_text("{not json", encoding="utf-8")
    return {
        "finance": _expert_package(tmp_path / "experts" / "finance", "finance"),
        "claude-expert": _expert_package(
            tmp_path / "experts" / "claude-expert",
            "claude-expert",
            runtime={"provider_name": "claudecode", "config": {"skill_conflict": "replace"}},
        ),
        "dsh-expert": _expert_package(
            tmp_path / "experts" / "dsh-expert", "dsh-expert", runtime={"provider_name": "dsh"}
        ),
        "broken": broken,
        "designer": _group_package(tmp_path / "groups" / "designer"),
    }


@pytest.fixture
def catalog(packages: dict[str, Path]) -> FakeCatalog:
    experts = {name: packages[name] for name in ("finance", "claude-expert", "dsh-expert", "broken")}
    return FakeCatalog(experts, {"designer": packages["designer"]})


@pytest_asyncio.fixture
async def db() -> AsyncIterator[TeamDatabase]:
    token = set_session_id("expert-session")
    database = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    try:
        await database.initialize()
        await database.team.create_team(team_name=_TEAM, display_name="Experts", leader_member_name="leader1")
        yield database
    finally:
        reset_session_id(token)
        await database.close()


def _backend(db: TeamDatabase, *external: ExternalCliAgentSpec) -> TeamBackend:
    return TeamBackend(
        team_name=_TEAM,
        member_name="leader1",
        is_leader=True,
        db=db,
        messager=AsyncMock(spec=Messager),
        external_cli_agents=list(external),
    )


def _tools(
    backend: TeamBackend,
    catalog: FakeCatalog | None,
    *,
    binding: ExpertBinding | None = None,
    lang: str = "cn",
) -> dict[str, Any]:
    tools = create_team_tools(
        role="leader",
        agent_team=backend,
        lang=lang,
        expert_catalog=catalog,
        expert_binding=binding or ExpertBinding(persona_workspace="/ws/teammate"),
    )
    return {tool.card.name: tool for tool in tools}


async def _member_count(db: TeamDatabase) -> int:
    return len(await db.member.get_team_members(_TEAM))


def _teammate_base() -> DeepAgentSpec:
    return DeepAgentSpec(language="cn", skills=["base-skill"], workspace=WorkspaceSpec(root_path="/ws/teammate"))


# ---------------------------------------------------------------------------
# 1. Gating
# ---------------------------------------------------------------------------


@pytest.mark.level0
@pytest.mark.parametrize("role", ["leader", "teammate"])
@pytest.mark.parametrize("dispatch_mode", ["autonomous", "scheduled"])
def test_expert_tools_only_exist_for_a_leader_with_a_catalog(
    db: TeamDatabase, catalog: FakeCatalog, role: str, dispatch_mode: str
) -> None:
    backend = _backend(db)
    without = {
        tool.card.name
        for tool in create_team_tools(role=role, agent_team=backend, dispatch_mode=dispatch_mode, lang="en")
    }
    with_catalog = {
        tool.card.name
        for tool in create_team_tools(
            role=role, agent_team=backend, dispatch_mode=dispatch_mode, lang="en", expert_catalog=catalog
        )
    }
    assert not without & EXPERT_TOOLS
    expected_extra = set(EXPERT_TOOLS) if role == "leader" else set()
    assert with_catalog - without == expected_extra
    assert without <= with_catalog


# ---------------------------------------------------------------------------
# 2-3. Views
# ---------------------------------------------------------------------------


@pytest.mark.level0
@pytest.mark.asyncio
async def test_view_experts_lists_without_arguments_and_skips_broken_packages(
    db: TeamDatabase, catalog: FakeCatalog
) -> None:
    tools = _tools(_backend(db), catalog)
    with (
        patch("openjiuwen.agent_teams.tools.tool_expert.aload_agent_template_package") as full_loader,
        patch("openjiuwen.agent_teams.tools.tool_expert.aload_agent_group_package") as group_loader,
    ):
        listed = await tools["view_experts"].invoke({})
        got = await tools["view_experts"].invoke({"action": "get", "name": "finance"})
        filtered = await tools["view_experts"].invoke({"query": "CLAUDE"})
    full_loader.assert_not_called()
    group_loader.assert_not_called()

    assert [item["name"] for item in listed.data["experts"]] == ["finance", "claude-expert", "dsh-expert"]
    text = tools["view_experts"].render_for_llm(listed)
    assert "display_name=finance 专家" in text and "runtime=claudecode" in text
    assert [item["name"] for item in filtered.data["experts"]] == ["claude-expert"]
    detail = got.data["expert"]
    assert (detail["version"], detail["source"], detail["skills"]) == ("1.0.0", "builtin", ["finance-skill"])


@pytest.mark.level1
@pytest.mark.asyncio
async def test_view_experts_get_needs_an_installed_name(db: TeamDatabase, catalog: FakeCatalog) -> None:
    tool = _tools(_backend(db), catalog)["view_experts"]
    missing = await tool.invoke({"action": "get"})
    assert missing.error == "'name' is required for action 'get'"
    unknown = await tool.invoke({"action": "get", "name": "nobody"})
    assert "not installed" in unknown.error and "finance" in unknown.error
    broken = await tool.invoke({"action": "get", "name": "broken"})
    assert "cannot be read" in broken.error


@pytest.mark.level0
@pytest.mark.asyncio
async def test_view_expert_groups_list_and_get(db: TeamDatabase, catalog: FakeCatalog) -> None:
    tool = _tools(_backend(db), catalog)["view_expert_groups"]
    with patch("openjiuwen.agent_teams.tools.tool_expert.aload_agent_group_package") as group_loader:
        listed = await tool.invoke({})
        by_capability = await tool.invoke({"capabilities": ["video", "audio"]})
        got = await tool.invoke({"action": "get", "name": "designer"})
    group_loader.assert_not_called()

    assert listed.data["groups"][0]["members"] == 2, "leader is not counted as spawnable"
    assert by_capability.data["groups"] == []
    members = {item["name"]: item for item in got.data["group"]["members"]}
    assert list(members) == ["leader", "clip", "cli-member"]
    assert members["leader"]["spawnable"] is False
    assert members["clip"]["display_name"] == "视频镜头师"
    assert (members["cli-member"]["runtime"], members["cli-member"]["spawnable"]) == ("claudecode", False), (
        "no claude CLI is declared for this team"
    )


@pytest.mark.level1
@pytest.mark.asyncio
async def test_view_expert_groups_expands_one_member(db: TeamDatabase, catalog: FakeCatalog) -> None:
    tool = _tools(_backend(db), catalog, lang="en")["view_expert_groups"]
    one = await tool.invoke({"action": "get", "name": "designer", "member": "clip"})
    assert one.data["member"]["display_name"] == "Shot Artist"
    assert one.data["member"]["group"] == "designer"
    assert "spawnable: true" in tool.render_for_llm(one)
    unknown = await tool.invoke({"action": "get", "name": "designer", "member": "ghost"})
    assert unknown.error == "Member 'ghost' is not in expert group 'designer' (members: leader, clip, cli-member)"


# ---------------------------------------------------------------------------
# 4-5. spawn_expert — member rows and rejections
# ---------------------------------------------------------------------------


@pytest.mark.level0
@pytest.mark.asyncio
async def test_spawn_single_expert_writes_persona_desc_and_snapshot(
    db: TeamDatabase, catalog: FakeCatalog, packages: dict[str, Path]
) -> None:
    backend = _backend(db)
    tool = _tools(backend, catalog)["spawn_expert"]
    result = await tool.invoke(
        {"expert": "finance", "member_name": "finance-1", "desc": "Covers funds", "prompt": "Answer briefly."}
    )
    assert result.success, result.error
    assert tool.render_for_llm(result).startswith("Expert member registered: member_name=finance-1")

    row = await backend.get_member("finance-1")
    assert row.role == TeamRole.TEAMMATE.value
    assert row.display_name == "finance 专家"
    assert row.desc == "finance description\n\nCovers funds"
    assert row.prompt == "You are finance, working in /ws/teammate.\n\nAnswer briefly."
    record = get_member_agent_template(row)
    assert record.ref == {"name": "finance", "group": None}
    assert record.version == "1.0.0"
    assert record.snapshot["prompt_sections"] == []
    assert Path(record.snapshot["skills"][0]["dir"]).name == "finance-skill"
    assert record.external_cli is None


@pytest.mark.level0
@pytest.mark.asyncio
async def test_spawn_group_member_and_template_desc_when_desc_omitted(
    db: TeamDatabase, catalog: FakeCatalog
) -> None:
    backend = _backend(db)
    tool = _tools(backend, catalog)["spawn_expert"]
    result = await tool.invoke({"expert": "clip", "group": "designer", "member_name": "clip-1"})
    assert result.success, result.error
    row = await backend.get_member("clip-1")
    assert row.desc == "clip description"
    assert row.display_name == "视频镜头师"
    assert "Collaborate on shots." in row.prompt, "the group instruction is part of the persona"
    assert get_member_agent_template(row).ref == {"name": "clip", "group": "designer"}


@pytest.mark.level1
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("inputs", "error"),
    [
        ({"expert": "leader", "group": "designer", "member_name": "x-1"}, "cannot be spawned as a teammate"),
        ({"expert": "nobody", "member_name": "x-1"}, "Expert 'nobody' is not installed"),
        ({"expert": "clip", "group": "nowhere", "member_name": "x-1"}, "Expert group 'nowhere' is not installed"),
        ({"expert": "ghost", "group": "designer", "member_name": "x-1"}, "members: clip, cli-member"),
        ({"expert": "broken", "member_name": "x-1"}, "Expert 'broken' cannot be loaded"),
        ({"expert": "finance", "member_name": "Bad_Name"}, "Invalid member_name"),
        ({"expert": "dsh-expert", "member_name": "x-1"}, "unsupported runtime"),
        ({"expert": "finance", "member_name": "x-1", "external_cli": {"fallback_model_name": "m"}}, "omit it"),
        ({"expert": "claude-expert", "member_name": "x-1"}, "is not declared"),
    ],
)
async def test_spawn_expert_rejections_write_no_member_row(
    db: TeamDatabase, catalog: FakeCatalog, inputs: dict[str, Any], error: str
) -> None:
    backend = _backend(db)
    result = await _tools(backend, catalog)["spawn_expert"].invoke(inputs)
    assert not result.success
    assert error in result.error
    assert await _member_count(db) == 0


@pytest.mark.level1
@pytest.mark.asyncio
async def test_spawn_expert_rejects_a_taken_member_name(db: TeamDatabase, catalog: FakeCatalog) -> None:
    tool = _tools(_backend(db), catalog)["spawn_expert"]
    assert (await tool.invoke({"expert": "finance", "member_name": "finance-1"})).success
    second = await tool.invoke({"expert": "finance", "member_name": "finance-1"})
    assert "already exists" in second.error
    assert await _member_count(db) == 1


@pytest.mark.level1
@pytest.mark.asyncio
async def test_spawn_expert_rejects_a_cli_whose_sdk_is_missing(db: TeamDatabase, catalog: FakeCatalog) -> None:
    from openjiuwen.agent_teams.external.cli_agent.backends import SdkRequirement

    backend = _backend(db, ExternalCliAgentSpec(cli_agent="claude"))
    missing = SdkRequirement(module="claude_agent_sdk", distribution="claude-agent-sdk", extra="claude")
    with patch("openjiuwen.agent_teams.external.cli_agent.backends.missing_sdk_requirement", return_value=missing):
        result = await _tools(backend, catalog)["spawn_expert"].invoke(
            {"expert": "claude-expert", "member_name": "cli-1"}
        )
    assert "is not installed in this environment" in result.error
    assert await _member_count(db) == 0


# ---------------------------------------------------------------------------
# 6-8, 12. Build from the row: spec selection, cold recovery, cross-path
# ---------------------------------------------------------------------------


@pytest.mark.level0
@pytest.mark.asyncio
async def test_spawned_member_is_built_from_its_row_snapshot_even_after_the_package_changes(
    db: TeamDatabase, catalog: FakeCatalog, packages: dict[str, Path]
) -> None:
    backend = _backend(db)
    tool = _tools(backend, catalog)["spawn_expert"]
    assert (await tool.invoke({"expert": "clip", "group": "designer", "member_name": "clip-1"})).success
    snapshot_at_spawn = get_member_agent_template(await backend.get_member("clip-1")).snapshot

    # Upgrade, then uninstall the package: the row alone rebuilds the member.
    _expert_package(packages["designer"] / "agents" / "clip", "clip", description="changed")
    shutil.rmtree(packages["designer"])
    snapshot, external = _template_fields(await backend.get_member("clip-1"))
    assert snapshot == snapshot_at_spawn
    assert external is None

    spec = TeamAgentSpec(agents={"leader": DeepAgentSpec(), "teammate": _teammate_base()})
    built = AgentConfigurator.resolve_agent_spec(spec, TeamRole.TEAMMATE, "clip-1", agent_template_spec=snapshot)
    assert built.agent_template_spec == snapshot
    assert built.skills == ["base-skill", "clip-skill"]
    plain = AgentConfigurator.resolve_agent_spec(spec, TeamRole.TEAMMATE, "other")
    assert plain is spec.agents["teammate"], "members without a snapshot keep the shared base"


@pytest.mark.level0
@pytest.mark.asyncio
async def test_predefined_and_spawned_members_assemble_identically(
    db: TeamDatabase, catalog: FakeCatalog, packages: dict[str, Path]
) -> None:
    template = load_agent_group_package(packages["designer"])["clip"]
    predefined = compose_template_member(_teammate_base(), template)

    backend = _backend(db)
    tool = _tools(backend, catalog)["spawn_expert"]
    assert (await tool.invoke({"expert": "clip", "group": "designer", "member_name": "clip-1"})).success
    row = await backend.get_member("clip-1")
    snapshot, _ = _template_fields(row)
    spawned_spec = apply_template_snapshot(_teammate_base(), snapshot)

    assert spawned_spec.agent_template_spec == predefined.spec.agent_template_spec
    assert spawned_spec.skills == predefined.spec.skills
    assert row.prompt == predefined.persona

    # A predefined member's own spec still wins over any snapshot.
    spec = TeamAgentSpec(agents={"leader": DeepAgentSpec(), "teammate": _teammate_base(), "clip": predefined.spec})
    chosen = AgentConfigurator.resolve_agent_spec(spec, TeamRole.TEAMMATE, "clip", agent_template_spec={"x": 1})
    assert chosen is spec.agents["clip"]


# ---------------------------------------------------------------------------
# 16. No blocking file IO on the tool path
# ---------------------------------------------------------------------------


def _refuse_on_thread(owner: Any, name: str, loop_thread: int) -> Any:
    """Patch ``owner.name`` so it fails on the event-loop thread and works on worker threads."""
    original = getattr(owner, name)

    def _guarded(*args: Any, **kwargs: Any) -> Any:
        if threading.get_ident() == loop_thread:
            raise AssertionError("blocking file IO on an expert tool path")
        return original(*args, **kwargs)

    return patch.object(owner, name, _guarded)


@pytest.mark.level0
@pytest.mark.asyncio
async def test_expert_tools_do_no_blocking_file_io(db: TeamDatabase, catalog: FakeCatalog) -> None:
    tools = _tools(_backend(db), catalog)
    loop_thread = threading.get_ident()
    blocking = [
        _refuse_on_thread(builtins, "open", loop_thread),
        *(
            _refuse_on_thread(pathlib.Path, name, loop_thread)
            for name in ("read_text", "read_bytes", "open", "is_file", "is_dir", "exists", "resolve", "iterdir", "rglob")
        ),
    ]
    for patcher in blocking:
        patcher.start()
    try:
        results = [
            await tools["view_experts"].invoke({}),
            await tools["view_experts"].invoke({"action": "get", "name": "finance"}),
            await tools["view_expert_groups"].invoke({}),
            await tools["view_expert_groups"].invoke({"action": "get", "name": "designer", "member": "clip"}),
            await tools["spawn_expert"].invoke({"expert": "finance", "member_name": "finance-1"}),
            await tools["spawn_expert"].invoke({"expert": "clip", "group": "designer", "member_name": "clip-1"}),
        ]
    finally:
        for patcher in blocking:
            patcher.stop()
    assert all(result.success for result in results), [result.error for result in results]


# ---------------------------------------------------------------------------
# 17. Bound AgentGroup: more instances of a predefined member
# ---------------------------------------------------------------------------


def _bound_spec(packages: dict[str, Path], *external: ExternalCliAgentSpec) -> TeamAgentSpec:
    templates = load_agent_group_package(packages["designer"])
    clip = compose_template_member(_teammate_base(), templates["clip"])
    return TeamAgentSpec(
        agents={"leader": DeepAgentSpec(), "teammate": _teammate_base(), "clip": clip.spec},
        agent_group_name="designer",
        predefined_members=[
            TeamMemberSpec(member_name="clip", display_name="视频镜头师", desc="clip description", prompt=clip.persona),
            ExternalCliMemberSpec(
                member_name="cli-member",
                display_name="CLI member",
                desc="cli description",
                prompt="cli persona",
                external_cli=external_cli_agent_spec_from_template(templates["cli-member"]),
            ),
        ],
        external_cli_agents=list(external),
    )


@pytest.mark.level0
@pytest.mark.asyncio
async def test_bound_group_member_spawns_from_the_team_spec_not_the_disk(
    db: TeamDatabase, catalog: FakeCatalog, packages: dict[str, Path]
) -> None:
    spec = _bound_spec(packages)
    backend = _backend(db)
    tools = _tools(backend, catalog, binding=ExpertBinding(bound_group=bound_agent_group(spec)))
    # The predefined clip already occupies the display name.
    await backend.spawn_member("clip", "视频镜头师", AgentCard(name="clip"))
    shutil.rmtree(packages["designer"])
    catalog.calls = 0

    result = await tools["spawn_expert"].invoke(
        {"expert": "clip", "group": "designer", "member_name": "clip-2", "desc": "负责第 11–20 镜"}
    )
    assert result.success, result.error
    assert catalog.calls == 0, "a bound group member never goes through the catalog"
    row = await backend.get_member("clip-2")
    assert row.display_name == "视频镜头师（clip-2）"
    assert row.prompt == spec.predefined_members[0].prompt
    assert row.desc == "clip description\n\n负责第 11–20 镜"
    assert get_member_agent_template(row).snapshot == spec.agents["clip"].agent_template_spec


@pytest.mark.level1
@pytest.mark.asyncio
async def test_bound_group_rejects_non_members_and_the_leader(
    db: TeamDatabase, catalog: FakeCatalog, packages: dict[str, Path]
) -> None:
    spec = _bound_spec(packages)
    tool = _tools(_backend(db), catalog, binding=ExpertBinding(bound_group=bound_agent_group(spec)))["spawn_expert"]
    not_member = await tool.invoke({"expert": "brief", "group": "designer", "member_name": "brief-1"})
    assert not_member.error == (
        "'brief' is not a spawnable member of the bound expert group 'designer' (members: clip, cli-member)"
    )
    leader = await tool.invoke({"expert": "leader", "group": "designer", "member_name": "leader-2"})
    assert "cannot be spawned as a teammate" in leader.error


# ---------------------------------------------------------------------------
# 18. External runtime experts
# ---------------------------------------------------------------------------


@pytest.mark.level0
@pytest.mark.asyncio
async def test_external_expert_registers_an_external_cli_member_with_the_merged_config(
    db: TeamDatabase, catalog: FakeCatalog
) -> None:
    declared = ExternalCliAgentSpec(cli_agent="claude", env={"HOST_ONLY": "1"}, skills=[{"dir": "/host/skill"}])
    backend = _backend(db, declared)
    tool = _tools(backend, catalog)["spawn_expert"]
    result = await tool.invoke({"expert": "claude-expert", "member_name": "cli-1"})
    assert result.success, result.error
    assert "cli_agent=claude" in tool.render_for_llm(result)

    row = await backend.get_member("cli-1")
    options = get_member_options(row)
    assert row.role == TeamRole.EXTERNAL_CLI.value
    assert options.cli_agent == "claude"
    assert options.agent_template.snapshot is None
    merged = ExternalCliAgentSpec.model_validate(options.agent_template.external_cli)
    assert merged.env == {"HOST_ONLY": "1"}, "declared host fields are kept"
    assert merged.skill_conflict == "replace"
    assert [Path(skill["dir"]).name for skill in merged.skills] == ["skill", "claude-expert-skill"]

    # Cold recovery / process spawn: the config is read back from the row.
    _, template_external = _template_fields(row)
    spec = TeamAgentSpec(agents={"leader": DeepAgentSpec()}, external_cli_agents=[declared])
    assert _external_cli_config_for_member(spec, "cli-1", "claude", template_external) == merged
    assert _external_cli_config_for_member(spec, "cli-1", "claude") == declared


@pytest.mark.level0
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "models",
    [
        {"model_name": "*"},
        {"model_name": "gpt"},
        {"fallback_model_name": 3},
        {"fallback_model_name": "pool-model"},
        {"builtin_model": "sonnet"},
        {"effort": "high"},
    ],
)
async def test_external_expert_model_rules_match_spawn_teammate_cli_agent(
    db: TeamDatabase, catalog: FakeCatalog, models: dict[str, Any]
) -> None:
    backend = _backend(db, ExternalCliAgentSpec(cli_agent="claude"))
    external_cli = {key: value for key, value in models.items() if key != "model_name"}
    expert = await _tools(backend, catalog, lang="en")["spawn_expert"].invoke(
        {"expert": "claude-expert", "member_name": "cli-1", "model_name": models.get("model_name"),
         "external_cli": external_cli}
    )
    direct = await SpawnTeammateTool(backend, make_translator("en")).invoke(
        {"member_name": "cli-2", "display_name": "d", "desc": "p", "cli_agent": "claude",
         "model_name": models.get("model_name"), "external_cli": external_cli}
    )
    assert not expert.success and not direct.success
    assert expert.error.replace("spawn_expert", "TOOL") == direct.error.replace("spawn_teammate", "TOOL")
    assert await _member_count(db) == 0


@pytest.mark.level1
def test_external_cli_schema_offers_builtin_models_only_when_declared(db: TeamDatabase, catalog: FakeCatalog) -> None:
    plain = _tools(_backend(db, ExternalCliAgentSpec(cli_agent="claude")), catalog)["spawn_expert"]
    assert set(plain.card.input_params["properties"]["external_cli"]["properties"]) == {"fallback_model_name"}
    with_catalog = _tools(
        _backend(db, ExternalCliAgentSpec(cli_agent="claude", builtin_models=[ExternalCliBuiltinModel(name="sonnet")])),
        catalog,
    )["spawn_expert"]
    properties = with_catalog.card.input_params["properties"]["external_cli"]["properties"]
    assert set(properties) == {"fallback_model_name", "builtin_model", "effort"}
    assert "<builtin_model_catalog>" in properties["builtin_model"]["description"]


@pytest.mark.level1
@pytest.mark.asyncio
async def test_bound_external_member_spawns_more_instances_with_the_predefined_config(
    db: TeamDatabase, catalog: FakeCatalog, packages: dict[str, Path]
) -> None:
    declared = ExternalCliAgentSpec(cli_agent="claude", env={"HOST": "1"})
    spec = _bound_spec(packages, declared)
    backend = _backend(db, declared)
    tool = _tools(backend, catalog, binding=ExpertBinding(bound_group=bound_agent_group(spec)))["spawn_expert"]
    result = await tool.invoke({"expert": "cli-member", "group": "designer", "member_name": "cli-member-2"})
    assert result.success, result.error
    row = await backend.get_member("cli-member-2")
    stored = ExternalCliAgentSpec.model_validate(get_member_agent_template(row).external_cli)
    assert stored == merge_external_cli_config(declared, spec.predefined_members[1].external_cli)
    assert stored.claude_turn_idle_timeout_s == 30
    assert row.prompt == "cli persona"


@pytest.mark.level1
def test_merge_rejects_mismatched_cli_kinds() -> None:
    with pytest.raises(ValueError, match="declared config is for 'codex'"):
        merge_external_cli_config(ExternalCliAgentSpec(cli_agent="codex"), ExternalCliAgentSpec(cli_agent="claude"))


# ---------------------------------------------------------------------------
# Injection: host setter → leader assembly → TeamToolRail
# ---------------------------------------------------------------------------


def _leader_tool_names(spec: TeamAgentSpec) -> set[str]:
    import asyncio

    native = spec.build().harness.inner_agent
    asyncio.run(native.ensure_initialized())
    return set(native.ability_manager._tools.keys())


@pytest.mark.level0
def test_host_catalog_reaches_the_leader_tool_rail(catalog: FakeCatalog) -> None:
    from openjiuwen.agent_teams.runtime import TeamRuntimeManager
    from openjiuwen.agent_teams.schema.blueprint import TransportSpec

    def _spec(team_name: str) -> TeamAgentSpec:
        return TeamAgentSpec(
            agents={"leader": DeepAgentSpec()},
            team_name=team_name,
            transport=TransportSpec(type="pyzmq", params={"team_id": team_name, "node_id": "team_leader"}),
        )

    assert TeamRuntimeManager.expert_catalog() is None
    assert not _leader_tool_names(_spec("no-catalog")) & EXPERT_TOOLS
    TeamRuntimeManager.set_expert_catalog(catalog)
    try:
        assert TeamRuntimeManager.expert_catalog() is catalog
        assert EXPERT_TOOLS <= _leader_tool_names(_spec("with-catalog"))
    finally:
        TeamRuntimeManager.set_expert_catalog(None)


@pytest.mark.level1
def test_expert_binding_reads_the_bound_group_and_persona_workspace(packages: dict[str, Path]) -> None:
    from openjiuwen.agent_teams.agent.agent_configurator import _derive_expert_binding

    binding = _derive_expert_binding(_bound_spec(packages))
    assert binding.persona_workspace == "/ws/teammate"
    assert binding.bound_group.name == "designer"
    assert binding.bound_group.member_names() == ["clip", "cli-member"]
    assert _derive_expert_binding(TeamAgentSpec(agents={"leader": DeepAgentSpec()})).bound_group is None


@pytest.mark.level1
def test_template_fields_survive_the_process_spawn_payload() -> None:
    """Process spawn ships the runtime context as JSON; no catalog is needed on the other side."""
    from openjiuwen.agent_teams.schema.team import TeamRuntimeContext

    ctx = TeamRuntimeContext(
        agent_template_spec={"agent_card": {"name": "clip"}, "skills": []},
        template_external_cli=ExternalCliAgentSpec(cli_agent="claude", env={"HOST": "1"}),
    )
    restored = TeamRuntimeContext.model_validate(ctx.model_dump(mode="json"))
    assert restored.agent_template_spec == ctx.agent_template_spec
    assert restored.template_external_cli == ctx.template_external_cli


# ---------------------------------------------------------------------------
# Review follow-ups
# ---------------------------------------------------------------------------


@pytest.mark.level1
@pytest.mark.asyncio
async def test_spawned_persona_uses_the_base_spec_language_like_predefined_members(
    db: TeamDatabase, catalog: FakeCatalog, packages: dict[str, Path]
) -> None:
    """The leader speaks en, the teammate base has no language: both paths render with "cn"."""
    base = DeepAgentSpec(language=None, workspace=WorkspaceSpec(root_path="/ws/teammate"))
    predefined = compose_template_member(base, load_agent_group_package(packages["designer"])["clip"])
    backend = _backend(db)
    tool = _tools(
        backend,
        catalog,
        binding=ExpertBinding(persona_workspace="/ws/teammate", persona_language=base.language),
        lang="en",
    )["spawn_expert"]
    assert (await tool.invoke({"expert": "clip", "group": "designer", "member_name": "clip-1"})).success
    assert (await backend.get_member("clip-1")).prompt == predefined.persona


@pytest.mark.level1
@pytest.mark.asyncio
async def test_spawn_expert_turns_unexpected_errors_into_a_failed_output(
    db: TeamDatabase, catalog: FakeCatalog
) -> None:
    async def _broken(name: str) -> PackageLocation:
        raise OSError("disk unavailable")

    catalog.locate_expert = _broken
    result = await _tools(_backend(db), catalog)["spawn_expert"].invoke({"expert": "finance", "member_name": "f-1"})
    assert not result.success
    assert result.error == "Internal error: disk unavailable"
    assert await _member_count(db) == 0


@pytest.mark.level1
@pytest.mark.asyncio
async def test_external_group_member_is_spawnable_only_when_its_cli_is_declared(
    db: TeamDatabase, catalog: FakeCatalog
) -> None:
    async def _cli_member_spawnable(backend: TeamBackend) -> bool:
        got = await _tools(backend, catalog)["view_expert_groups"].invoke({"action": "get", "name": "designer"})
        return {item["name"]: item for item in got.data["group"]["members"]}["cli-member"]["spawnable"]

    assert await _cli_member_spawnable(_backend(db)) is False
    assert await _cli_member_spawnable(_backend(db, ExternalCliAgentSpec(cli_agent="claude"))) is True


@pytest.mark.level1
@pytest.mark.asyncio
async def test_snapshot_of_a_deleted_package_fails_loudly_when_mounted(
    db: TeamDatabase, catalog: FakeCatalog, packages: dict[str, Path]
) -> None:
    """SDD-0032 §7 #7: a member whose package directory was removed cannot silently lose its tools."""
    from openjiuwen.harness.resources import resolve_agent_template_parts
    from openjiuwen.harness.schema.build_context import BuildContext
    from openjiuwen.harness.schema.extension_spec import AgentTemplateSpec

    tool_file = packages["finance"] / "tools" / "lookup.py"
    tool_file.parent.mkdir(parents=True)
    tool_file.write_text("class Lookup:\n    pass\n", encoding="utf-8")
    manifest = json.loads((packages["finance"] / "manifest.json").read_text(encoding="utf-8"))
    manifest["tools"] = [{"file": "tools/lookup.py", "class": "Lookup"}]
    (packages["finance"] / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    backend = _backend(db)
    assert (await _tools(backend, catalog)["spawn_expert"].invoke({"expert": "finance", "member_name": "f-1"})).success
    snapshot, _ = _template_fields(await backend.get_member("f-1"))
    shutil.rmtree(packages["finance"])
    with pytest.raises(Exception, match="lookup.py"):
        resolve_agent_template_parts(AgentTemplateSpec.model_validate(snapshot), BuildContext(language="cn"))


# ---------------------------------------------------------------------------
# Scheduled create_task: inline members declared from an expert template
# ---------------------------------------------------------------------------


def _scheduled_create_task(backend: TeamBackend, catalog: FakeCatalog | None, **binding: Any) -> Any:
    tools = create_team_tools(
        role="leader",
        agent_team=backend,
        dispatch_mode="scheduled",
        lang="cn",
        expert_catalog=catalog,
        expert_binding=ExpertBinding(persona_workspace="/ws/teammate", **binding),
    )
    return {tool.card.name: tool for tool in tools}["create_task"]


def _expert_task(task_id: str, member_name: str, **member: Any) -> dict:
    return {
        "task_id": task_id,
        "title": task_id,
        "content": "shoot it",
        "member": {"member_name": member_name, "lifetime": "shutdown", **member},
    }


@pytest.mark.level0
def test_inline_member_offers_expert_fields_only_with_a_catalog(db: TeamDatabase, catalog: FakeCatalog) -> None:
    def _member_schema(tool: Any) -> dict:
        return tool.card.input_params["properties"]["tasks"]["items"]["properties"]["member"]

    plain = _member_schema(_scheduled_create_task(_backend(db), None))
    assert "expert" not in plain["properties"]
    assert set(plain["required"]) == {"member_name", "display_name", "desc", "lifetime"}
    with_catalog = _member_schema(_scheduled_create_task(_backend(db), catalog))
    assert {"expert", "group"} <= set(with_catalog["properties"])
    assert set(with_catalog["required"]) == {"member_name", "lifetime"}


@pytest.mark.level0
@pytest.mark.asyncio
async def test_inline_member_runs_as_an_expert_like_spawn_expert(
    db: TeamDatabase, catalog: FakeCatalog, packages: dict[str, Path]
) -> None:
    backend = _backend(db)
    tool = _scheduled_create_task(backend, catalog)
    result = await tool.invoke(
        {
            "tasks": [
                _expert_task("t1", "clip-1", expert="clip", group="designer", desc="负责第 1–10 镜"),
                _expert_task("t2", "clip-2", expert="clip", group="designer"),
            ]
        }
    )
    assert result.success, result.error
    assert "expert=designer/clip" in tool.render_for_llm(result)

    first, second = await backend.get_member("clip-1"), await backend.get_member("clip-2")
    template = load_agent_group_package(packages["designer"])["clip"]
    assert first.prompt == compose_template_member(_teammate_base(), template).persona
    assert first.desc == "clip description\n\n负责第 1–10 镜"
    assert (first.display_name, second.display_name) == ("视频镜头师", "视频镜头师（clip-2）")
    record = get_member_agent_template(first)
    assert record.ref == {"name": "clip", "group": "designer"}
    assert record.snapshot["prompt_sections"] == []
    assert get_member_options(first).lifetime == "shutdown"


@pytest.mark.level1
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("member", "error"),
    [
        ({"expert": "finance", "external_cli": {"fallback_model_name": "m"}}, "runs natively"),
        ({"expert": "claude-expert", "cli_agent": "claude"}, "member.cli_agent and member.expert are exclusive"),
        ({"expert": "nobody"}, "member.expert: Expert 'nobody' is not installed"),
        ({"expert": "leader", "group": "designer"}, "cannot be spawned as a teammate"),
        ({"group": "designer"}, "member.group needs member.expert"),
    ],
)
async def test_inline_expert_rejections_write_nothing(
    db: TeamDatabase, catalog: FakeCatalog, member: dict, error: str
) -> None:
    backend = _backend(db, ExternalCliAgentSpec(cli_agent="claude"))
    result = await _scheduled_create_task(backend, catalog).invoke({"tasks": [_expert_task("t1", "x-1", **member)]})
    assert not result.success
    assert error in result.error
    assert await _member_count(db) == 0


@pytest.mark.level1
@pytest.mark.asyncio
async def test_inline_expert_without_a_catalog_is_rejected(db: TeamDatabase) -> None:
    result = await _scheduled_create_task(_backend(db), None).invoke(
        {"tasks": [_expert_task("t1", "clip-1", expert="clip", group="designer", display_name="d", desc="d")]}
    )
    assert "provides no expert catalog" in result.error
    assert await _member_count(db) == 0


# ---------------------------------------------------------------------------
# Scheduled create_task: inline members on an external CLI
# ---------------------------------------------------------------------------


def _cli_task(task_id: str, member_name: str, **member: Any) -> dict:
    return _expert_task(
        task_id,
        member_name,
        **{"cli_agent": "claude", "display_name": "Reviewer", "desc": "reviews", "prompt": "review it", **member},
    )


@pytest.mark.level0
def test_inline_member_offers_cli_fields_only_when_cli_kinds_are_declared(
    db: TeamDatabase, catalog: FakeCatalog
) -> None:
    def _member_props(backend: TeamBackend) -> dict:
        tool = _scheduled_create_task(backend, catalog)
        return tool.card.input_params["properties"]["tasks"]["items"]["properties"]["member"]["properties"]

    assert not {"cli_agent", "external_cli"} & set(_member_props(_backend(db)))
    plain = _member_props(_backend(db, ExternalCliAgentSpec(cli_agent="claude")))
    assert set(plain["external_cli"]["properties"]) == {"fallback_model_name"}
    with_catalog = _member_props(
        _backend(db, ExternalCliAgentSpec(cli_agent="claude", builtin_models=[ExternalCliBuiltinModel(name="sonnet")]))
    )
    assert set(with_catalog["external_cli"]["properties"]) == {"fallback_model_name", "builtin_model", "effort"}
    assert "<builtin_model_catalog>" in with_catalog["external_cli"]["properties"]["builtin_model"]["description"]


@pytest.mark.level0
@pytest.mark.parametrize("builtin_models", [[], [ExternalCliBuiltinModel(name="sonnet")]])
def test_spawn_teammate_cli_fields_match_the_inline_member(
    db: TeamDatabase, catalog: FakeCatalog, builtin_models: list[ExternalCliBuiltinModel]
) -> None:
    """spawn_teammate and scheduled create_task's inline member declare an external CLI member alike."""
    backend = _backend(db, ExternalCliAgentSpec(cli_agent="claude", builtin_models=builtin_models))
    tool = _scheduled_create_task(backend, catalog)
    member = tool.card.input_params["properties"]["tasks"]["items"]["properties"]["member"]["properties"]
    teammate = SpawnTeammateTool(backend, make_translator("cn")).card.input_params["properties"]
    for field in ("cli_agent", "external_cli"):
        assert teammate[field]["type"] == member[field]["type"]
    assert set(teammate["external_cli"]["properties"]) == set(member["external_cli"]["properties"])


@pytest.mark.level0
@pytest.mark.asyncio
async def test_inline_external_expert_registers_like_spawn_expert(db: TeamDatabase, catalog: FakeCatalog) -> None:
    declared = ExternalCliAgentSpec(cli_agent="claude", env={"HOST_ONLY": "1"}, skills=[{"dir": "/host/skill"}])
    backend = _backend(db, declared)
    spawned = await _tools(backend, catalog)["spawn_expert"].invoke({"expert": "claude-expert", "member_name": "cli-1"})
    assert spawned.success, spawned.error
    tool = _scheduled_create_task(backend, catalog)
    result = await tool.invoke({"tasks": [_expert_task("t1", "cli-2", expert="claude-expert", lifetime="dormant")]})
    assert result.success, result.error
    assert "lifetime=dormant, expert=claude-expert, cli_agent=claude]" in tool.render_for_llm(result)

    reference, inline = await backend.get_member("cli-1"), await backend.get_member("cli-2")
    assert inline.role == TeamRole.EXTERNAL_CLI.value
    assert backend.get_external_cli_agent("cli-2") == "claude"
    assert get_member_options(inline).lifetime == "dormant"
    assert inline.prompt == reference.prompt
    assert get_member_agent_template(inline) == get_member_agent_template(reference)
    # Retirement reads the row only: the scheduler's scan and the member's own settle.
    await db.member.update_member_status("cli-2", _TEAM, "ready")
    assert ("cli-2", "ready", "dormant") in await backend.list_retirable_members()
    assert await backend.get_member_lifetime("cli-2") == "dormant"


@pytest.mark.level0
@pytest.mark.asyncio
async def test_inline_cli_agent_member_registers_like_spawn_teammate(db: TeamDatabase, catalog: FakeCatalog) -> None:
    backend = _backend(db, ExternalCliAgentSpec(cli_agent="claude"))
    tool = _scheduled_create_task(backend, catalog)
    result = await tool.invoke({"tasks": [_cli_task("t1", "rev-1")]})
    assert result.success, result.error
    assert "lifetime=shutdown, cli_agent=claude]" in tool.render_for_llm(result)

    row = await backend.get_member("rev-1")
    options = get_member_options(row)
    assert (row.role, row.display_name, row.desc, row.prompt) == (
        TeamRole.EXTERNAL_CLI.value,
        "Reviewer",
        "reviews",
        "review it",
    )
    assert (options.cli_agent, options.lifetime, options.agent_template) == ("claude", "shutdown", None)
    assert (await backend.task_manager.get("t1")).assignee == "rev-1"


@pytest.mark.level1
@pytest.mark.asyncio
async def test_cli_members_need_no_private_prompt(db: TeamDatabase, catalog: FakeCatalog) -> None:
    backend = _backend(db, ExternalCliAgentSpec(cli_agent="claude"))
    inline = await _scheduled_create_task(backend, catalog).invoke({"tasks": [_cli_task("t1", "rev-1", prompt="")]})
    assert inline.success, inline.error
    tool = SpawnTeammateTool(backend, make_translator("en"))
    assert "prompt" not in tool.card.input_params["required"]
    direct = await tool.invoke({"member_name": "rev-2", "display_name": "d", "desc": "d", "cli_agent": "claude"})
    assert direct.success, direct.error
    assert [(await backend.get_member(name)).prompt for name in ("rev-1", "rev-2")] == ["", ""]


@pytest.mark.level0
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "models",
    [
        {"model_name": "*"},
        {"fallback_model_name": 3},
        {"fallback_model_name": "pool-model"},
        {"builtin_model": "sonnet"},
        {"effort": "high"},
    ],
)
async def test_inline_cli_member_model_rules_match_spawn_teammate(
    db: TeamDatabase, catalog: FakeCatalog, models: dict[str, Any]
) -> None:
    backend = _backend(db, ExternalCliAgentSpec(cli_agent="claude"))
    external_cli = {key: value for key, value in models.items() if key != "model_name"}
    member = {"external_cli": external_cli, **({"model_name": models["model_name"]} if "model_name" in models else {})}
    inline = await _scheduled_create_task(backend, catalog).invoke({"tasks": [_cli_task("t1", "rev-1", **member)]})
    direct = await SpawnTeammateTool(backend, make_translator("en")).invoke(
        {"member_name": "rev-2", "display_name": "d", "desc": "p", "cli_agent": "claude", **member}
    )
    assert not inline.success and not direct.success
    assert inline.error.endswith(direct.error.replace("spawn_teammate", "create_task"))
    assert await _member_count(db) == 0


@pytest.mark.level1
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("member", "error"),
    [
        ({"cli_agent": "codex"}, "cli_agent 'codex' is not declared"),
        ({"cli_agent": None, "external_cli": {"fallback_model_name": "m"}}, "applies only to an external CLI member"),
        ({"external_cli": "claude"}, "member.external_cli must be an object"),
    ],
)
async def test_inline_cli_member_rejections_write_nothing(
    db: TeamDatabase, catalog: FakeCatalog, member: dict, error: str
) -> None:
    backend = _backend(db, ExternalCliAgentSpec(cli_agent="claude"))
    result = await _scheduled_create_task(backend, catalog).invoke({"tasks": [_cli_task("t1", "rev-1", **member)]})
    assert not result.success
    assert error in result.error
    assert await _member_count(db) == 0


@pytest.mark.level1
@pytest.mark.asyncio
async def test_inline_cli_member_needs_declared_cli_kinds(db: TeamDatabase, catalog: FakeCatalog) -> None:
    result = await _scheduled_create_task(_backend(db), catalog).invoke({"tasks": [_cli_task("t1", "rev-1")]})
    assert "declares no external CLI kinds" in result.error
    assert await _member_count(db) == 0


@pytest.mark.level1
@pytest.mark.asyncio
async def test_rejected_batch_rolls_back_external_cli_members(db: TeamDatabase, catalog: FakeCatalog) -> None:
    backend = _backend(db, ExternalCliAgentSpec(cli_agent="claude"))
    result = await _scheduled_create_task(backend, catalog).invoke(
        {
            "tasks": [
                _cli_task("t1", "rev-1"),
                _expert_task("t2", "cli-2", expert="claude-expert"),
                _cli_task("t3", "rev-3", external_cli={"fallback_model_name": "pool-model"}),
            ]
        }
    )
    assert not result.success
    assert await _member_count(db) == 0
    assert backend.external_cli_agent_names() == frozenset()
