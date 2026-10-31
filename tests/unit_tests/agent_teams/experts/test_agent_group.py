# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""AgentGroup package: strict loader (sync / async) and display card.

The loader rules moved here from jiuwenswarm unchanged; the tests that came
with them are kept as they were and extended to the async loader and card.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import pytest

from openjiuwen.agent_teams.experts import (
    ExpertRef,
    aload_agent_group_package,
    load_agent_group_package,
    load_agent_group_package_bundle,
    read_agent_group_card,
)
from openjiuwen.agent_teams.external import external_cli_agent_spec_from_template


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _minimal_group(tmp_path: Path) -> Path:
    group = tmp_path / "group"
    _write_json(
        group / "manifest.json",
        {
            "name": "group",
            "package_type": "agent_group",
            "agents": ["leader", "member1"],
        },
    )
    for name in ("leader", "member1"):
        _write_json(
            group / "agents" / name / "manifest.json",
            {
                "package_type": "agent_template",
                "name": f"{name} display name",
                "description": f"{name} description",
                "persona": {"dir": "." if name == "leader" else "./persona"},
            },
        )
        persona = group / "agents" / name / "persona" / f"{name}.md"
        persona.parent.mkdir(parents=True, exist_ok=True)
        persona.write_text(f"# {name}\n", encoding="utf-8")
    (group / "agents" / "leader" / "AGENT.md").write_text(
        "# Leader rules\n",
        encoding="utf-8",
    )
    return group


def _update_manifest(path: Path, **fields: object) -> None:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest.update(fields)
    _write_json(path, manifest)


@pytest.mark.level0
def test_load_agent_group_uses_directory_as_id_and_name_as_display_name(
    tmp_path: Path,
) -> None:
    group = _minimal_group(tmp_path)

    templates = load_agent_group_package(group)

    assert templates["leader"].agent_card.id == "leader"
    assert templates["leader"].agent_card.name == "leader display name"
    assert templates["member1"].agent_card.id == "member1"
    assert templates["member1"].agent_card.name == "member1 display name"


@pytest.mark.level0
def test_load_agent_group_bundle_reuses_manifest_and_templates(tmp_path: Path) -> None:
    group = _minimal_group(tmp_path)
    _update_manifest(
        group / "manifest.json",
        instruction="  analyze carefully  ",
        capabilities=["analysis", "risk", "analysis", ""],
    )

    package = load_agent_group_package_bundle(group)

    assert package.package_dir == group.resolve()
    assert package.manifest["name"] == "group"
    assert set(package.templates) == {"leader", "member1"}
    assert package.instruction == "analyze carefully"
    assert package.capabilities == ("analysis", "risk")


@pytest.mark.level1
def test_load_agent_group_rejects_member_agent_md(tmp_path: Path) -> None:
    group = _minimal_group(tmp_path)
    (group / "agents" / "member1" / "AGENT.md").write_text(
        "# unexpected\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must not contain AGENT.md"):
        load_agent_group_package(group)


@pytest.mark.level1
@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"tags": [{"id": "legal", "zh": "法律", "en": "Legal"}]}, ("legal",)),
        ({"tags": [{"zh": "法律", "en": "Legal"}, {"en": "Risk"}, "finance"]},
         ("法律", "Risk", "finance")),
        ({"tags": [{"id": "legal"}, {"id": "legal"}, {}, " "]}, ("legal",)),
        ({"capabilities": ["finance"], "tags": [{"id": "legal"}]}, ("finance",)),
        ({"capabilities": [], "tags": [{"id": "legal"}]}, ()),
        ({}, ()),
    ],
)
def test_group_capabilities_use_stable_tags_unless_explicit(tmp_path, fields, expected):
    group = _minimal_group(tmp_path)
    _update_manifest(group / "manifest.json", **fields)

    assert load_agent_group_package_bundle(group).capabilities == expected


@pytest.mark.level1
def test_load_agent_group_discovers_unlisted_skills(tmp_path: Path) -> None:
    group = _minimal_group(tmp_path)
    skill_dir = group / "skills" / "discovered_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "# Discovered skill\n",
        encoding="utf-8",
    )

    templates = load_agent_group_package(group)

    for template in templates.values():
        assert [Path(skill.dir).name for skill in template.skills] == [
            "discovered_skill"
        ]


@pytest.mark.level1
def test_load_agent_group_merges_declared_and_discovered_skills(
    tmp_path: Path,
) -> None:
    group = _minimal_group(tmp_path)
    _update_manifest(group / "manifest.json", skills=["declared_skill"])

    for name in ("declared_skill", "another_skill"):
        skill_dir = group / "skills" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")

    templates = load_agent_group_package(group)

    for template in templates.values():
        assert [Path(skill.dir).name for skill in template.skills] == [
            "declared_skill",
            "another_skill",
        ]


def _external_member(group: Path, provider_name: str) -> None:
    shared = group / "skills" / "shared"
    shared.mkdir(parents=True)
    (shared / "SKILL.md").write_text("# shared\n", encoding="utf-8")
    _update_manifest(group / "manifest.json", skills=[{"dir": "./skills/shared", "mode": "all"}])

    member_skill = group / "agents" / "member1" / "skills" / "review"
    member_skill.mkdir(parents=True)
    (member_skill / "SKILL.md").write_text("# review\n", encoding="utf-8")
    _update_manifest(
        group / "agents" / "member1" / "manifest.json",
        skills=[{"dir": "./skills/review", "mode": "all"}],
        runtime={
            "provider_name": provider_name,
            "provider_version": "0.1.0",
            "config": {"skill_conflict": "replace"},
        },
    )


@pytest.mark.level1
@pytest.mark.parametrize(
    ("provider_name", "cli_agent"),
    [("codex", "codex"), ("claudecode", "claude")],
)
def test_load_external_runtime_reuses_cli_config_and_manifest_skill_paths(
    tmp_path: Path,
    provider_name: str,
    cli_agent: str,
) -> None:
    group = _minimal_group(tmp_path)
    _external_member(group, provider_name)

    templates = load_agent_group_package(group)
    template = templates["member1"]
    assert template.runtime is not None
    assert template.runtime.provider_name == provider_name
    config = external_cli_agent_spec_from_template(template)
    assert config is not None
    assert config.cli_agent == cli_agent
    assert config.skill_conflict == "replace"
    assert [Path(skill["dir"]).name for skill in config.skills] == ["review", "shared"]


@pytest.mark.level1
def test_load_external_runtime_rejects_leader(tmp_path: Path) -> None:
    group = _minimal_group(tmp_path)
    _update_manifest(
        group / "agents" / "leader" / "manifest.json",
        runtime={"provider_name": "codex", "provider_version": "0.1.0", "config": {}},
    )

    with pytest.raises(ValueError, match="leader does not support"):
        load_agent_group_package(group)


@pytest.mark.level1
@pytest.mark.parametrize(
    "agents",
    [
        ["member1"],
        ["leader", "leader"],
        ["leader", "../member1"],
    ],
)
def test_load_agent_group_rejects_invalid_roster(
    tmp_path: Path,
    agents: list[str],
) -> None:
    group = _minimal_group(tmp_path)
    _update_manifest(group / "manifest.json", agents=agents)

    with pytest.raises(ValueError):
        load_agent_group_package(group)


@pytest.mark.level1
@pytest.mark.parametrize("member_name", ["Member1", "_member", "1member", "成员"])
def test_load_agent_group_rejects_member_name_outside_routing_pattern(
    tmp_path: Path,
    member_name: str,
) -> None:
    group = _minimal_group(tmp_path)
    (group / "agents" / "member1").rename(group / "agents" / member_name)
    _update_manifest(group / "manifest.json", agents=["leader", member_name])

    with pytest.raises(ValueError, match="invalid agent name in agent_group manifest"):
        load_agent_group_package(group)


@pytest.mark.level1
@pytest.mark.parametrize("member_name", ["backend-dev-1", "narrative_designer"])
def test_load_agent_group_accepts_kebab_and_snake_case_member_name(
    tmp_path: Path,
    member_name: str,
) -> None:
    group = _minimal_group(tmp_path)
    (group / "agents" / "member1").rename(group / "agents" / member_name)
    _update_manifest(group / "manifest.json", agents=["leader", member_name])

    templates = load_agent_group_package(group)

    assert set(templates) == {"leader", member_name}


# ---------------------------------------------------------------------------
# Async loader: same result or same exception as the sync loader.
# ---------------------------------------------------------------------------


def _leader_with_narrow_persona(group: Path) -> None:
    _update_manifest(group / "agents" / "leader" / "manifest.json", persona={"dir": "./persona"})
    _update_manifest(group / "manifest.json", instruction="Work together.")


def _with_skills(group: Path) -> None:
    _update_manifest(group / "manifest.json", skills=["declared_skill"])
    for name in ("declared_skill", "another_skill"):
        (group / "skills" / name).mkdir(parents=True)
        (group / "skills" / name / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")


def _external(group: Path) -> None:
    _external_member(group, "codex")


def _member_agent_md(group: Path) -> None:
    (group / "agents" / "member1" / "AGENT.md").write_text("x", encoding="utf-8")


def _missing_member_dir(group: Path) -> None:
    _update_manifest(group / "manifest.json", agents=["leader", "member1", "ghost"])


def _name_mismatch(group: Path) -> None:
    _update_manifest(group / "manifest.json", name="other")


def _no_leader_agent_md(group: Path) -> None:
    (group / "agents" / "leader" / "AGENT.md").unlink()


def _missing_shared_skill(group: Path) -> None:
    _update_manifest(group / "manifest.json", skills=["absent"])


VARIANTS: dict[str, Callable[[Path], None]] = {
    "minimal": lambda group: None,
    "leader_with_narrow_persona": _leader_with_narrow_persona,
    "skills": _with_skills,
    "external_member": _external,
    "member_agent_md": _member_agent_md,
    "missing_member_dir": _missing_member_dir,
    "name_mismatch": _name_mismatch,
    "no_leader_agent_md": _no_leader_agent_md,
    "missing_shared_skill": _missing_shared_skill,
}


def _sync_outcome(group: Path) -> tuple[str, object]:
    try:
        package = load_agent_group_package_bundle(group)
    except Exception as exc:  # the exact exception is the outcome under test
        return "error", (type(exc), str(exc))
    return "ok", (package.package_dir, package.manifest, {k: v.model_dump(mode="json") for k, v in package.templates.items()})


async def _async_outcome(group: Path) -> tuple[str, object]:
    try:
        package = await aload_agent_group_package(group)
    except Exception as exc:  # the exact exception is the outcome under test
        return "error", (type(exc), str(exc))
    return "ok", (package.package_dir, package.manifest, {k: v.model_dump(mode="json") for k, v in package.templates.items()})


@pytest.mark.level0
@pytest.mark.asyncio
@pytest.mark.parametrize("variant", sorted(VARIANTS))
async def test_async_loader_matches_sync_loader(tmp_path: Path, variant: str) -> None:
    group = _minimal_group(tmp_path)
    VARIANTS[variant](group)
    assert await _async_outcome(group) == _sync_outcome(group)


@pytest.mark.level1
def test_narrow_leader_persona_mounts_agent_md_once(tmp_path: Path) -> None:
    group = _minimal_group(tmp_path)
    _leader_with_narrow_persona(group)
    leader = load_agent_group_package(group)["leader"]
    names = [section.name for section in leader.prompt_sections]
    assert names == ["identity", "agent_group_leader_rules", "agent_group_instruction"]


# ---------------------------------------------------------------------------
# Card
# ---------------------------------------------------------------------------


@pytest.mark.level0
@pytest.mark.asyncio
async def test_group_card_lists_members_in_manifest_order_named_by_directory(tmp_path: Path) -> None:
    group = _minimal_group(tmp_path)
    (group / "agents" / "member1").rename(group / "agents" / "zeta")
    _write_json(
        group / "agents" / "alpha" / "manifest.json",
        {"package_type": "agent_template", "name": "Alpha", "display_name": {"zh": "甲", "en": "Alpha"}},
    )
    _update_manifest(
        group / "manifest.json",
        agents=["leader", "zeta", "alpha"],
        display_name={"zh": "测试团", "en": "Test Group"},
        description="group description",
        instruction="  rules  ",
        tags=[{"id": "qa", "zh": "质检"}],
        version="2.0.0",
    )

    card = await read_agent_group_card(group)

    assert [member.name for member in card.members] == ["leader", "zeta", "alpha"]
    assert card.members[1].ref == ExpertRef(name="zeta", group="group")
    assert card.members[1].card.name == "member1 display name", "the manifest name is kept on the member card"
    assert card.members[1].display_label("en") == "member1 display name"
    assert card.members[2].display_label("cn") == "甲"
    assert card.members[0].is_leader
    assert (card.display_label("cn"), card.instruction, card.capabilities, card.version) == (
        "测试团",
        "rules",
        ("qa",),
        "2.0.0",
    )


@pytest.mark.level1
@pytest.mark.asyncio
async def test_group_card_reads_manifests_only(tmp_path: Path) -> None:
    group = _minimal_group(tmp_path)
    (group / "agents" / "member1" / "AGENT.md").write_text("loader would reject this", encoding="utf-8")
    card = await read_agent_group_card(group)
    assert card.member("member1") is not None


@pytest.mark.level1
@pytest.mark.asyncio
async def test_group_card_fails_on_a_missing_member_manifest(tmp_path: Path) -> None:
    group = _minimal_group(tmp_path)
    _update_manifest(group / "manifest.json", agents=["leader", "member1", "ghost"])
    with pytest.raises(ValueError, match="not readable"):
        await read_agent_group_card(group)

