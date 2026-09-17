# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for ``WorkspaceAssembler`` write side (design-v5 block A).

Covers: A-class full-directory scan into ``prompts/system/``, C-class
domain mirror + param JSON, B-class team/member identity files, idempotent
re-assembly, and the evolution protection on re-assembly.
"""

import json
from pathlib import Path

import pytest

from openjiuwen.agent_teams.paths import (
    configure_openjiuwen_home,
    reset_openjiuwen_home,
    team_member_workspace_dir,
    team_workspace_dir,
)
from openjiuwen.agent_teams.team_workspace.assembler import WorkspaceAssembler
from openjiuwen.agent_teams.team_workspace.frontmatter import (
    read_frontmatter,
    write_frontmatter,
)
from openjiuwen.agent_teams.team_workspace.layout import (
    TOOL_PARAM_FILE_FMT,
    WorkspaceLayout,
)


@pytest.fixture
def assembler(tmp_path):
    """Isolated home — real ``agent_teams.paths`` functions resolve under tmp."""
    home = tmp_path / "home"
    home.mkdir()
    configure_openjiuwen_home(str(home))
    try:
        yield WorkspaceAssembler()
    finally:
        reset_openjiuwen_home()


def _team_root() -> Path:
    return team_workspace_dir("T")


class TestATemplateScan:
    """A-class: full-directory scan mirrors every framework prompt md."""

    def test_every_framework_prompt_lands_in_system(self, assembler):
        assembler.write_system_and_tool_prompts(team_name="T", language="cn")
        system_dir = _team_root() / "prompts" / "system"
        framework_files = list(WorkspaceLayout.iter_framework_prompt_files("cn"))
        assert len(framework_files) > 0  # the framework has prompts to mirror
        landed = sorted(p.name for p in system_dir.glob("*.cn.md"))
        expected = sorted(f"{p.stem}.cn.md" for p in framework_files)
        assert landed == expected

    def test_a_class_frontmatter_kind_and_baseline(self, assembler):
        assembler.write_system_and_tool_prompts(team_name="T", language="cn")
        sample = sorted((_team_root() / "prompts" / "system").glob("*.cn.md"))[0]
        meta, body = read_frontmatter(sample.read_text(encoding="utf-8"))
        from openjiuwen.agent_teams.team_workspace.frontmatter import body_sha256

        assert meta["kind"] == "prompt"
        assert meta["baseline_sha256"] == body_sha256(body)
        assert meta["evolved"] is False


class TestCToolScan:
    """C-class: domain mirror + param-level JSON dict."""

    def test_tool_md_mirrors_desc_domains(self, assembler):
        assembler.write_system_and_tool_prompts(team_name="T", language="cn")
        tool_dir = _team_root() / "prompts" / "tool"
        desc_files = list(WorkspaceLayout.iter_framework_desc_files("cn"))
        assert len(desc_files) > 0
        for md in desc_files:
            rel = md.relative_to(WorkspaceLayout.framework_descs_dir("cn"))
            mirrored = tool_dir / rel.with_suffix(".cn.md")
            assert mirrored.exists(), f"missing C-class mirror: {mirrored}"

    def test_tool_param_dict_lands_flat(self, assembler):
        assembler.write_system_and_tool_prompts(team_name="T", language="cn")
        param_file = _team_root() / "prompts" / "tool" / TOOL_PARAM_FILE_FMT.format(lang="cn")
        assert param_file.exists()
        meta, body = read_frontmatter(param_file.read_text(encoding="utf-8"))
        assert meta["kind"] == "tool_params"
        parsed = json.loads(body)
        assert isinstance(parsed, dict)
        assert len(parsed) > 0
        # The file is the full STRINGS dict verbatim — dotted keys unchanged.
        assert any("." in key for key in parsed)

    def test_tool_param_handwritten_survives_reassembly(self, assembler):
        assembler.write_system_and_tool_prompts(team_name="T", language="cn")
        param_file = _team_root() / "prompts" / "tool" / TOOL_PARAM_FILE_FMT.format(lang="cn")
        param_file.write_text('{"my.custom.key": "hand edited"}', encoding="utf-8")
        assembler.write_system_and_tool_prompts(team_name="T", language="cn")
        assert param_file.read_text(encoding="utf-8") == '{"my.custom.key": "hand edited"}'


class TestBIdentity:
    """B-class: team files only when values exist; member files on spawn."""

    def test_team_card_written_only_with_value(self, assembler):
        assembler.write_team_identity(team_name="T", team_desc="team desc")
        card = _team_root() / "prompts" / "identity" / "team_card.md"
        assert card.exists()
        _, body = read_frontmatter(card.read_text(encoding="utf-8"))
        assert body == "team desc"

    def test_team_card_skipped_without_value(self, assembler):
        assembler.write_team_identity(team_name="T")
        card = _team_root() / "prompts" / "identity" / "team_card.md"
        assert not card.exists()

    def test_member_identity_lands_in_link_dir(self, assembler):
        assembler.write_member_identity(
            team_name="T",
            member_name="leader",
            member_desc="desc",
            member_prompt="prompt",
        )
        identity = team_member_workspace_dir("T", "leader") / "prompts" / "identity"
        _, card_body = read_frontmatter((identity / "card.md").read_text(encoding="utf-8"))
        _, prompt_body = read_frontmatter((identity / "member_prompt.md").read_text(encoding="utf-8"))
        assert card_body == "desc"
        assert prompt_body == "prompt"


class TestIdempotentReassembly:
    """Re-assembly reuses baselines and never clobbers evolved files."""

    def test_second_assembly_is_noop_on_unevolved(self, assembler):
        assembler.write_system_and_tool_prompts(team_name="T", language="cn")
        sample = sorted((_team_root() / "prompts" / "system").glob("*.cn.md"))[0]
        first = sample.read_text(encoding="utf-8")
        assembler.write_system_and_tool_prompts(team_name="T", language="cn")
        assert sample.read_text(encoding="utf-8") == first

    def test_evolved_a_class_survives_reassembly(self, assembler):
        assembler.write_system_and_tool_prompts(team_name="T", language="cn")
        target = sorted((_team_root() / "prompts" / "system").glob("*.cn.md"))[0]
        meta, _ = read_frontmatter(target.read_text(encoding="utf-8"))
        target.write_text(write_frontmatter(meta, "evolved body"), encoding="utf-8")
        assembler.write_system_and_tool_prompts(team_name="T", language="cn")
        _, body = read_frontmatter(target.read_text(encoding="utf-8"))
        assert body == "evolved body"

    def test_malformed_frontmatter_rebuilt_on_reassembly(self, assembler):
        assembler.write_system_and_tool_prompts(team_name="T", language="cn")
        target = sorted((_team_root() / "prompts" / "system").glob("*.cn.md"))[0]
        target.write_text("---\nkind: [unclosed\n---\nbody", encoding="utf-8")
        assembler.write_system_and_tool_prompts(team_name="T", language="cn")
        meta, body = read_frontmatter(target.read_text(encoding="utf-8"))
        assert body != "body"  # rebuilt from the framework default, not kept
        assert meta["baseline_sha256"] is not None


class TestSharedCacheWriteGuard:
    """A teammate sharing the leader's cache must not re-read evolved md.

    The cache-hit guard (:meth:`WorkspaceCache.has_template` /
    ``has_tool_md`` / ``is_tools_loaded``) makes a teammate sharing the
    leader's ``WorkspaceCache`` instance skip the whole read-write-judge
    pass on its ``coordination.start``. This keeps the cache stable for
    the whole session: an evolution-party edit made mid-session must not
    reach a teammate that re-enters the write path (which would pollute
    the leader's primed baseline with the evolved value).
    """

    @pytest.fixture
    def cached_assembler(self, tmp_path):
        """Isolated home + a shared WorkspaceCache bound to team ``T``."""
        from openjiuwen.agent_teams.team_workspace.workspace_cache import WorkspaceCache
        from openjiuwen.agent_teams.team_workspace.workspace_store import WorkspaceStore

        home = tmp_path / "home"
        home.mkdir()
        configure_openjiuwen_home(str(home))
        cache = WorkspaceCache(WorkspaceStore(), "T", language="cn")
        try:
            yield WorkspaceAssembler(WorkspaceStore(), cache=cache), cache
        finally:
            reset_openjiuwen_home()

    def test_teammate_write_skips_when_leader_primed(self, cached_assembler):
        """Leader primes the cache; a teammate sharing it skips re-reading."""
        assembler, cache = cached_assembler
        # Leader's first write primes the cache dict (every A-class file).
        assembler.write_system_and_tool_prompts(team_name="T", language="cn")
        sample = sorted((_team_root() / "prompts" / "system").glob("*.cn.md"))[0]
        # The cache key is the framework stem (no language suffix).
        name = sample.name.removesuffix(".cn.md")
        leader_text = sample.read_text(encoding="utf-8")
        assert cache.has_template(name)  # leader primed it

        # Evolve the file on disk *after* the leader primed the cache — this
        # is the mid-session evolution-party edit a teammate must not pick up.
        meta, _ = read_frontmatter(leader_text)
        sample.write_text(write_frontmatter(meta, "evolved body"), encoding="utf-8")

        # A teammate sharing the cache re-runs the write pass: the guard sees
        # the resident value and skips, so the cache keeps the leader's prime.
        assembler.write_system_and_tool_prompts(team_name="T", language="cn")
        # The cache still serves the leader's primed baseline, not the evolved
        # body written to disk mid-session (the dict was never re-read).
        cached = cache.get_template(name)
        _, cached_body = read_frontmatter(cached.content)
        assert cached_body != "evolved body"

    def test_resident_none_does_not_block_baseline_seed(self, cached_assembler):
        """A read-side miss stores None; the write side still seeds the baseline.

        ``get_template`` stores ``None`` on a miss (marks "no file value") so
        the miss path is not retried. That sentinel is not a write-side prime:
        the write guard (:meth:`has_template`) must report False for it, and
        ``_write_baseline`` must still seed the missing file's baseline.
        """
        assembler, cache = cached_assembler
        # A read-side lookup for a not-yet-written file caches None.
        assert cache.get_template("leader_bootstrap") is None
        assert not cache.has_template("leader_bootstrap")  # None is not a prime

        # The write pass now seeds the baseline despite the resident None.
        assembler.write_system_and_tool_prompts(team_name="T", language="cn")
        seeded = _team_root() / "prompts" / "system" / "leader_bootstrap.cn.md"
        assert seeded.is_file()  # baseline seeded, not skipped
        assert cache.has_template("leader_bootstrap")  # now a real prime

    def test_read_side_scan_does_not_skip_leader_c_class_write(self, cached_assembler):
        """A read-side C-class scan (``_load_tools``) must not block the write pass.

        ``_load_tools`` sets the read-side ``_tools_loaded`` flag; the write
        guard reads the separate write-side ``_tools_primed`` flag, so a
        read-side scan that fires before the leader's write pass does not
        make the leader skip seeding the C-class files.
        """
        assembler, cache = cached_assembler
        # A read-side tool lookup triggers the lazy C-class scan.
        cache.get_tool_md("create_task")
        assert cache.is_tools_loaded() is False  # read-side scan, not a write prime

        # The leader's write pass still seeds every C-class tool md.
        assembler.write_system_and_tool_prompts(team_name="T", language="cn")
        tool_dir = _team_root() / "prompts" / "tool"
        tool_files = sorted(p.name for p in tool_dir.rglob("*.cn.md"))
        assert tool_files  # C-class files seeded, not skipped
        assert cache.is_tools_loaded() is True  # write pass marked _tools_primed
