# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for ``WorkspaceLayout`` path rules (design-v5 206).

The layout module is the single source of truth for workspace-internal
relative paths. These tests pin the exact formulas so writer and reader
cannot silently diverge.
"""

from pathlib import Path

from openjiuwen.agent_teams.team_workspace.layout import (
    MEMBER_CARD_FILE,
    MEMBER_IDENTITY_REL,
    MEMBER_PROMPT_FILE,
    PROMPTS_IDENTITY,
    PROMPTS_SYSTEM,
    PROMPTS_TOOL,
    TEAM_CARD_FILE,
    TEAM_PROMPT_FILE,
    TOOL_PARAM_FILE_FMT,
    WorkspaceLayout,
)


class TestWorkspaceRootPaths:
    """A/C class targets under the workspace root."""

    def test_system_dir(self):
        assert WorkspaceLayout.system_dir(Path("/w")) == Path("/w") / PROMPTS_SYSTEM

    def test_system_file_is_lang_suffixed(self):
        assert WorkspaceLayout.system_file(Path("/w"), "x", "cn") == Path("/w/prompts/system/x.cn.md")

    def test_tool_dir(self):
        assert WorkspaceLayout.tool_dir(Path("/w")) == Path("/w") / PROMPTS_TOOL

    def test_tool_md_file_mirrors_domain(self):
        target = WorkspaceLayout.tool_md_file(Path("/w/tool"), Path("task"), "submit_plan", "cn")
        assert target == Path("/w/tool/task/submit_plan.cn.md")

    def test_tool_param_file_flat_at_root(self):
        assert WorkspaceLayout.tool_param_file(Path("/w/tool"), "cn") == Path("/w/tool/tool.param.cn.md")


class TestIterators:
    """Directory scans yield sorted, param-excluding files."""

    def test_iter_system_files(self, tmp_path):
        # The argument is the workspace root; files live under prompts/system.
        system_dir = WorkspaceLayout.system_dir(tmp_path)
        system_dir.mkdir(parents=True)
        (system_dir / "b.en.md").write_text("b", encoding="utf-8")
        (system_dir / "a.en.md").write_text("a", encoding="utf-8")
        (system_dir / "c.cn.md").write_text("c", encoding="utf-8")
        found = [p.name for p in WorkspaceLayout.iter_system_files(tmp_path, "en")]
        assert found == ["a.en.md", "b.en.md"]

    def test_iter_system_files_missing_dir_yields_nothing(self, tmp_path):
        assert list(WorkspaceLayout.iter_system_files(tmp_path, "en")) == []

    def test_iter_tool_md_files_excludes_param(self, tmp_path):
        (tmp_path / "submit_plan.en.md").write_text("x", encoding="utf-8")
        (tmp_path / TOOL_PARAM_FILE_FMT.format(lang="en")).write_text("{}", encoding="utf-8")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "nested.en.md").write_text("y", encoding="utf-8")
        found = [p.name for p in WorkspaceLayout.iter_tool_md_files(tmp_path, "en")]
        assert found == ["nested.en.md", "submit_plan.en.md"]  # rglob sorted: "sub/…" < "submit…"

    def test_iter_tool_param_file_single(self, tmp_path):
        (tmp_path / TOOL_PARAM_FILE_FMT.format(lang="en")).write_text("{}", encoding="utf-8")
        found = [p.name for p in WorkspaceLayout.iter_tool_param_file(tmp_path, "en")]
        assert found == [TOOL_PARAM_FILE_FMT.format(lang="en")]

    def test_iter_tool_param_file_missing(self, tmp_path):
        assert list(WorkspaceLayout.iter_tool_param_file(tmp_path, "en")) == []


class TestIdentityFiles:
    """B class team + member identity file names."""

    def test_team_identity(self):
        assert WorkspaceLayout.team_identity_dir(Path("/w")) == Path("/w") / PROMPTS_IDENTITY
        assert WorkspaceLayout.team_card_file(Path("/w")) == Path("/w/prompts/identity/team_card.md")
        assert WorkspaceLayout.team_prompt_file(Path("/w")) == Path("/w/prompts/identity/team_prompt.md")
        assert TEAM_CARD_FILE == "team_card.md"
        assert TEAM_PROMPT_FILE == "team_prompt.md"

    def test_member_identity(self):
        member_dir = Path("/m")
        assert WorkspaceLayout.member_card_file(member_dir) == member_dir / MEMBER_IDENTITY_REL / MEMBER_CARD_FILE
        assert WorkspaceLayout.member_prompt_file(member_dir) == member_dir / MEMBER_IDENTITY_REL / MEMBER_PROMPT_FILE
        assert MEMBER_CARD_FILE == "card.md"
        assert MEMBER_PROMPT_FILE == "member_prompt.md"


class TestFrameworkRoots:
    """Framework source roots used by the assembler/loader."""

    def test_framework_prompts_dir_points_into_package(self):
        assert WorkspaceLayout.framework_prompts_dir("cn").name == "cn"
        assert WorkspaceLayout.framework_prompts_dir("cn").parts[-2] == "prompts"

    def test_framework_prompt_file(self):
        target = WorkspaceLayout.framework_prompt_file("x", "cn")
        assert target.name == "x.md"

    def test_iter_framework_prompt_files_matches_glob(self, tmp_path, monkeypatch):
        # Pin the module-level package anchor to tmp so the scan is hermetic.
        monkeypatch.setattr(
            "openjiuwen.agent_teams.team_workspace.layout._AGENT_TEAMS_PKG_ROOT",
            tmp_path,
        )
        (tmp_path / "prompts" / "cn").mkdir(parents=True)
        (tmp_path / "prompts" / "cn" / "b.md").write_text("b", encoding="utf-8")
        (tmp_path / "prompts" / "cn" / "a.md").write_text("a", encoding="utf-8")
        found = [p.name for p in WorkspaceLayout.iter_framework_prompt_files("cn")]
        assert found == ["a.md", "b.md"]

    def test_framework_descs_dir_and_scan(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "openjiuwen.agent_teams.team_workspace.layout._AGENT_TEAMS_PKG_ROOT",
            tmp_path,
        )
        (tmp_path / "tools" / "locales" / "descs" / "cn" / "task").mkdir(parents=True)
        (tmp_path / "tools" / "locales" / "descs" / "cn" / "task" / "submit_plan.md").write_text(
            "x", encoding="utf-8"
        )
        assert WorkspaceLayout.framework_descs_dir("cn").parts[-2] == "descs"
        found = [p.name for p in WorkspaceLayout.iter_framework_desc_files("cn")]
        assert found == ["submit_plan.md"]
