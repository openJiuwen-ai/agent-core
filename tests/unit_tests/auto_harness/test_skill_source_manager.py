# coding: utf-8
"""Unit tests for SkillSourceManager — community skill clone, scan, and copy."""

import os
import shutil
import textwrap
from pathlib import Path
from unittest import TestCase

from openjiuwen.auto_harness.infra.skill_source_manager import (
    SkillMatch,
    _load_skill_description,
    _patch_skill_frontmatter,
    _repo_name_from_url,
    community_skill_cache_skill_dirs,
    copy_skill_to_extension,
    format_community_skill_list,
    scan_skills,
)
from openjiuwen.auto_harness.schema import AutoHarnessConfig, ExtensionDesign


class TestRepoNameFromUrl(TestCase):
    def test_github_url(self):
        assert _repo_name_from_url(
            "https://github.com/anthropics/skills.git"
        ) == "anthropics-skills"

    def test_baoyu_url(self):
        assert _repo_name_from_url(
            "https://github.com/JimLiu/baoyu-skills.git"
        ) == "JimLiu-baoyu-skills"

    def test_no_git_suffix(self):
        assert _repo_name_from_url(
            "https://github.com/owner/repo"
        ) == "owner-repo"

    def test_single_segment(self):
        result = _repo_name_from_url("repo")
        assert result == "repo"


class TestLoadSkillDescription(TestCase):
    def test_with_frontmatter(self):
        md = textwrap.dedent(
            """\
            ---
            name: pptx
            description: Create PowerPoint presentations
            ---
            # PPTX Skill
            Body content here.
            """
        )
        tmp = Path(self._tmp_dir) / "pptx" / "SKILL.md"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(md, encoding="utf-8")
        desc = _load_skill_description(tmp)
        assert desc == "Create PowerPoint presentations"

    def test_no_frontmatter(self):
        tmp = Path(self._tmp_dir) / "raw" / "SKILL.md"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text("# Just markdown\nNo frontmatter.", encoding="utf-8")
        desc = _load_skill_description(tmp)
        assert desc == ""

    def test_missing_file(self):
        desc = _load_skill_description(Path("/nonexistent/SKILL.md"))
        assert desc == ""

    def setUp(self):
        import tempfile

        self._tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self._tmp_dir, ignore_errors=True)


class TestPatchSkillFrontmatter(TestCase):
    def test_add_missing_fields(self):
        md = textwrap.dedent(
            """\
            ---
            name: pptx
            ---
            # PPTX
            """
        )
        tmp = Path(self._tmp_dir) / "pptx" / "SKILL.md"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(md, encoding="utf-8")
        _patch_skill_frontmatter(tmp, "pptx")
        patched = tmp.read_text(encoding="utf-8")
        assert "description:" in patched
        assert "Community skill: pptx" in patched

    def test_no_frontmatter_at_all(self):
        tmp = Path(self._tmp_dir) / "bare" / "SKILL.md"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text("# Bare skill\nContent.", encoding="utf-8")
        _patch_skill_frontmatter(tmp, "bare")
        patched = tmp.read_text(encoding="utf-8")
        assert patched.startswith("---")
        assert "name: bare" in patched
        assert "description:" in patched

    def test_complete_frontmatter_no_patch(self):
        md = textwrap.dedent(
            """\
            ---
            name: pdf
            description: Generate PDF files
            ---
            # PDF Skill
            """
        )
        tmp = Path(self._tmp_dir) / "pdf" / "SKILL.md"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(md, encoding="utf-8")
        _patch_skill_frontmatter(tmp, "pdf")
        patched = tmp.read_text(encoding="utf-8")
        # Should remain unchanged
        assert patched == md

    def setUp(self):
        import tempfile

        self._tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self._tmp_dir, ignore_errors=True)


class TestScanSkills(TestCase):
    def test_scan_finds_skills(self):
        # Create a fake skill cache dir with one repo
        repo_dir = Path(self._cache_dir) / "anthropics-skills"
        pptx_dir = repo_dir / "pptx"
        pptx_dir.mkdir(parents=True)
        pptx_md = pptx_dir / "SKILL.md"
        pptx_md.write_text(
            textwrap.dedent(
                """\
                ---
                name: pptx
                description: Create PowerPoint presentations
                ---
                # PPTX Skill
                """
            ),
            encoding="utf-8",
        )

        # Non-skill dir (no SKILL.md) should be skipped
        other_dir = repo_dir / "not_a_skill"
        other_dir.mkdir()

        config = AutoHarnessConfig(
            data_dir=self._tmp_dir,
            community_skill_repos=[
                "https://github.com/anthropics/skills.git"
            ],
        )
        result = scan_skills(config)
        assert "pptx" in result
        assert result["pptx"].description == "Create PowerPoint presentations"
        assert "not_a_skill" not in result

    def test_scan_empty_cache(self):
        config = AutoHarnessConfig(
            data_dir=self._tmp_dir,
            community_skill_repos=[
                "https://github.com/nonexistent/repo.git"
            ],
        )
        result = scan_skills(config)
        assert result == {}

    def setUp(self):
        import tempfile

        self._tmp_dir = tempfile.mkdtemp()
        # Cache dir must match resolved_community_skill_cache_dir
        # which defaults to {data_dir}/skills-cache/
        self._cache_dir = os.path.join(self._tmp_dir, "skills-cache")
        os.makedirs(self._cache_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self._tmp_dir, ignore_errors=True)


class TestCopySkillToExtension(TestCase):
    def test_copy_skill_success(self):
        # Create source skill
        repo_dir = Path(self._cache_dir) / "anthropics-skills"
        pptx_dir = repo_dir / "pptx"
        pptx_dir.mkdir(parents=True)
        pptx_md = pptx_dir / "SKILL.md"
        pptx_md.write_text(
            textwrap.dedent(
                """\
                ---
                name: pptx
                description: Create PowerPoint presentations
                ---
                # PPTX Skill
                """
            ),
            encoding="utf-8",
        )

        config = AutoHarnessConfig(
            data_dir=self._tmp_dir,
            community_skill_repos=[
                "https://github.com/anthropics/skills.git"
            ],
        )

        ext_root = Path(self._tmp_dir) / "ext_root"
        ext_root.mkdir()

        result = copy_skill_to_extension("pptx", ext_root, config)
        assert result is not None
        assert result.name == "pptx"
        assert (ext_root / "skills" / "pptx" / "SKILL.md").is_file()

    def test_copy_skill_not_found(self):
        config = AutoHarnessConfig(
            data_dir=self._tmp_dir,
            community_skill_repos=[],
        )
        ext_root = Path(self._tmp_dir) / "ext_root2"
        ext_root.mkdir()
        result = copy_skill_to_extension("nonexistent", ext_root, config)
        assert result is None

    def setUp(self):
        import tempfile

        self._tmp_dir = tempfile.mkdtemp()
        # Cache dir must match resolved_community_skill_cache_dir
        # which defaults to {data_dir}/skills-cache/
        self._cache_dir = os.path.join(self._tmp_dir, "skills-cache")
        os.makedirs(self._cache_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self._tmp_dir, ignore_errors=True)


class TestCommunitySkillCacheSkillDirs(TestCase):
    def test_returns_existing_dirs(self):
        repo_dir = Path(self._cache_dir) / "anthropics-skills"
        repo_dir.mkdir(parents=True)

        config = AutoHarnessConfig(
            data_dir=self._tmp_dir,
            community_skill_repos=[
                "https://github.com/anthropics/skills.git"
            ],
        )
        dirs = community_skill_cache_skill_dirs(config)
        assert len(dirs) == 1
        assert "anthropics-skills" in dirs[0]

    def test_empty_when_no_cache(self):
        config = AutoHarnessConfig(
            data_dir=self._tmp_dir,
            community_skill_repos=[
                "https://github.com/nonexistent/repo.git"
            ],
        )
        dirs = community_skill_cache_skill_dirs(config)
        assert dirs == []

    def setUp(self):
        import tempfile

        self._tmp_dir = tempfile.mkdtemp()
        self._cache_dir = os.path.join(self._tmp_dir, "skills-cache")
        os.makedirs(self._cache_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self._tmp_dir, ignore_errors=True)


class TestFormatCommunitySkillList(TestCase):
    def test_format_with_skills(self):
        repo_dir = Path(self._cache_dir) / "anthropics-skills"
        pptx_dir = repo_dir / "pptx"
        pptx_dir.mkdir(parents=True)
        pptx_md = pptx_dir / "SKILL.md"
        pptx_md.write_text(
            textwrap.dedent(
                """\
                ---
                name: pptx
                description: Create PowerPoint presentations
                ---
                # PPTX Skill
                """
            ),
            encoding="utf-8",
        )

        config = AutoHarnessConfig(
            data_dir=self._tmp_dir,
            community_skill_repos=[
                "https://github.com/anthropics/skills.git"
            ],
        )
        result = format_community_skill_list(config)
        assert "pptx" in result
        assert "Create PowerPoint presentations" in result

    def test_format_empty_cache(self):
        config = AutoHarnessConfig(
            data_dir=self._tmp_dir,
            community_skill_repos=[],
        )
        result = format_community_skill_list(config)
        assert "无" in result

    def setUp(self):
        import tempfile

        self._tmp_dir = tempfile.mkdtemp()
        # Cache dir must match resolved_community_skill_cache_dir
        # which defaults to {data_dir}/skills-cache/
        self._cache_dir = os.path.join(self._tmp_dir, "skills-cache")
        os.makedirs(self._cache_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self._tmp_dir, ignore_errors=True)


class TestExtensionDesignSkillSource(TestCase):
    def test_default_values(self):
        design = ExtensionDesign()
        assert design.skill_source == ""

    def test_community_source(self):
        design = ExtensionDesign(
            extension_name="pptx_gen",
            skill_source="community:pptx",
        )
        assert design.skill_source == "community:pptx"

    def test_from_dict(self):
        from openjiuwen.auto_harness.schema import AutoHarnessConfig

        config = AutoHarnessConfig.load_from_dict(
            {
                "data_dir": "/tmp/ah",
                "community_skill_repos": [
                    "https://github.com/test/skills.git"
                ],
                "community_skill_cache_dir": "/tmp/skills-cache",
            }
        )
        assert config.community_skill_repos == [
            "https://github.com/test/skills.git"
        ]
        assert config.community_skill_cache_dir == "/tmp/skills-cache"
        assert config.resolved_community_skill_cache_dir == "/tmp/skills-cache"

    def test_default_repos(self):
        config = AutoHarnessConfig()
        assert len(config.community_skill_repos) == 2
        assert "anthropics" in config.community_skill_repos[0]
        assert "baoyu-skills" in config.community_skill_repos[1]


class TestSkillFrontmatterValidation(TestCase):
    """Test SKILL.md frontmatter validation."""

    def test_validate_valid_frontmatter(self):
        from openjiuwen.auto_harness.infra.runtime_extension_static_checks import (
            _validate_skill_frontmatter,
        )

        md = textwrap.dedent(
            """\
            ---
            name: pptx
            description: Create PowerPoint presentations
            ---
            # PPTX Skill
            """
        )
        tmp = Path(self._tmp_dir) / "pptx" / "SKILL.md"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(md, encoding="utf-8")
        errors = _validate_skill_frontmatter(tmp)
        assert errors == []

    def test_validate_missing_name(self):
        from openjiuwen.auto_harness.infra.runtime_extension_static_checks import (
            _validate_skill_frontmatter,
        )

        md = textwrap.dedent(
            """\
            ---
            description: Create PowerPoint presentations
            ---
            # PPTX Skill
            """
        )
        tmp = Path(self._tmp_dir) / "no_name" / "SKILL.md"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(md, encoding="utf-8")
        errors = _validate_skill_frontmatter(tmp)
        assert len(errors) == 1
        assert "name" in errors[0]

    def test_validate_missing_description(self):
        from openjiuwen.auto_harness.infra.runtime_extension_static_checks import (
            _validate_skill_frontmatter,
        )

        md = textwrap.dedent(
            """\
            ---
            name: pptx
            ---
            # PPTX Skill
            """
        )
        tmp = Path(self._tmp_dir) / "no_desc" / "SKILL.md"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(md, encoding="utf-8")
        errors = _validate_skill_frontmatter(tmp)
        assert len(errors) == 1
        assert "description" in errors[0]

    def test_validate_no_frontmatter(self):
        from openjiuwen.auto_harness.infra.runtime_extension_static_checks import (
            _validate_skill_frontmatter,
        )

        tmp = Path(self._tmp_dir) / "bare" / "SKILL.md"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text("# Just markdown", encoding="utf-8")
        errors = _validate_skill_frontmatter(tmp)
        assert len(errors) > 0
        assert "frontmatter" in errors[0]

    def setUp(self):
        import tempfile

        self._tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self._tmp_dir, ignore_errors=True)