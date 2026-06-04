# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for evaluator_pipeline skill_manager."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from openjiuwen.agent_evolving.evaluator.evaluator_pipeline.config import PipelineConfig
from openjiuwen.agent_evolving.evaluator.evaluator_pipeline.skill_manager import SkillManager


class TestSkillManagerInit:
    """Test SkillManager initialization."""

    @staticmethod
    def test_init_default_skill_root():
        """Test SkillManager uses default skill root when not specified."""
        config = PipelineConfig()
        manager = SkillManager(config)
        expected_root = Path("~/.jiuwenswarm/agent/workspace/skills").expanduser()
        assert manager.skill_root == expected_root

    @staticmethod
    def test_init_custom_skill_root():
        """Test SkillManager uses custom skill root from config."""
        config = PipelineConfig(agent_config={"skill_persistence_dir": "/custom/skills"})
        manager = SkillManager(config)
        assert manager.skill_root == Path("/custom/skills")

    @staticmethod
    def test_init_empty_state():
        """Test SkillManager initializes with empty state."""
        config = PipelineConfig()
        manager = SkillManager(config)
        assert manager.current_skill is None
        assert manager.current_evolutions is None
        assert manager.all_skills == {}
        assert manager.all_evolutions == {}
        assert manager.all_evolution_files == {}


class TestSkillManagerTaskInit:
    """Test SkillManager.init_for_task method."""

    @staticmethod
    def test_init_for_task_creates_dir(tmp_path: Path):
        """Test init_for_task creates skill directory."""
        config = PipelineConfig(agent_config={"skill_persistence_dir": str(tmp_path)})
        manager = SkillManager(config)
        manager.init_for_task("test_task")

        assert manager.skill_dir == tmp_path / "test_task"
        assert manager.skill_dir.exists()

    @staticmethod
    def test_init_for_task_loads_resolved_name(tmp_path: Path):
        """Test init_for_task loads resolved skill name from file."""
        config = PipelineConfig(agent_config={"skill_persistence_dir": str(tmp_path)})
        manager = SkillManager(config)
        
        # Create resolved name file
        task_dir = tmp_path / "test_task"
        task_dir.mkdir(parents=True)
        (task_dir / ".resolved_skill_name").write_text("custom_skill_name")

        manager.init_for_task("test_task")
        assert manager.resolved_skill_name == "custom_skill_name"

    @staticmethod
    def test_init_for_task_default_name(tmp_path: Path):
        """Test init_for_task uses task_id as default name."""
        config = PipelineConfig(agent_config={"skill_persistence_dir": str(tmp_path)})
        manager = SkillManager(config)
        manager.init_for_task("test_task")

        assert manager.resolved_skill_name == "test_task"


class TestSkillManagerSaveAndLoad:
    """Test SkillManager save and load methods."""

    @staticmethod
    def test_save_resolved_skill_name(tmp_path: Path):
        """Test _save_resolved_skill_name saves name to file."""
        config = PipelineConfig(agent_config={"skill_persistence_dir": str(tmp_path)})
        manager = SkillManager(config)
        manager.init_for_task("test_task")
        manager.resolved_skill_name = "my_skill"

        manager._save_resolved_skill_name()

        name_file = tmp_path / "test_task" / ".resolved_skill_name"
        assert name_file.exists()
        assert name_file.read_text() == "my_skill"

    @staticmethod
    def test_load_all_skills_empty_dir(tmp_path: Path):
        """Test load_all_skills returns empty dict when no skills exist."""
        config = PipelineConfig(agent_config={"skill_persistence_dir": str(tmp_path)})
        manager = SkillManager(config)
        manager.init_for_task("test_task")

        skills = manager.load_all_skills(verbose=False)
        assert skills == {}

    @staticmethod
    def test_load_all_skills_with_skills(tmp_path: Path):
        """Test load_all_skills loads skills from directory."""
        config = PipelineConfig(agent_config={"skill_persistence_dir": str(tmp_path)})
        manager = SkillManager(config)
        manager.init_for_task("test_task")

        # Create test skill
        skill_dir = tmp_path / "test_task" / "my_skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# My Skill\nContent")

        skills = manager.load_all_skills(verbose=False)
        assert "my_skill" in skills
        assert skills["my_skill"] == "# My Skill\nContent"

    @staticmethod
    def test_load_all_skills_with_evolutions(tmp_path: Path):
        """Test load_all_skills loads evolutions."""
        config = PipelineConfig(agent_config={"skill_persistence_dir": str(tmp_path)})
        manager = SkillManager(config)
        manager.init_for_task("test_task")

        # Create test skill with evolutions
        skill_dir = tmp_path / "test_task" / "my_skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Skill")
        (skill_dir / "evolutions.json").write_text('{"entries": []}')

        manager.load_all_skills(verbose=False)
        assert "my_skill" in manager.all_evolutions
        assert manager.all_evolutions["my_skill"] == '{"entries": []}'


class TestSkillManagerSaveAllSkills:
    """Test SkillManager.save_all_skills method."""

    @staticmethod
    def test_save_all_skills_creates_files(tmp_path: Path):
        """Test save_all_skills creates skill files."""
        config = PipelineConfig(agent_config={"skill_persistence_dir": str(tmp_path)})
        manager = SkillManager(config)
        manager.init_for_task("test_task")

        skills = {"skill1": "# Skill1\nContent"}
        saved_paths = manager.save_all_skills(skills, iteration=1)

        assert len(saved_paths) == 1
        skill_path = tmp_path / "test_task" / "skill1" / "iteration_001" / "SKILL.md"
        assert skill_path.exists()
        assert skill_path.read_text() == "# Skill1\nContent"

    @staticmethod
    def test_save_all_skills_creates_latest_copy(tmp_path: Path):
        """Test save_all_skills creates latest copy."""
        config = PipelineConfig(agent_config={"skill_persistence_dir": str(tmp_path)})
        manager = SkillManager(config)
        manager.init_for_task("test_task")

        skills = {"skill1": "# Skill1"}
        manager.save_all_skills(skills, iteration=1)

        latest_path = tmp_path / "test_task" / "skill1" / "latest" / "SKILL.md"
        assert latest_path.exists()
        assert latest_path.read_text() == "# Skill1"

    @staticmethod
    def test_save_all_skills_with_evolutions(tmp_path: Path):
        """Test save_all_skills saves evolutions."""
        config = PipelineConfig(agent_config={"skill_persistence_dir": str(tmp_path)})
        manager = SkillManager(config)
        manager.init_for_task("test_task")

        skills = {"skill1": "# Skill1"}
        evolutions = {"skill1": '{"entries": [{"id": "ev001"}]}'}
        manager.save_all_skills(skills, iteration=1, evolutions=evolutions)

        evo_path = tmp_path / "test_task" / "skill1" / "evolutions.json"
        assert evo_path.exists()


class TestSkillManagerMergeEvolutions:
    """Test SkillManager._merge_evolutions_for_skill method."""

    @staticmethod
    def test_merge_evolutions_empty_existing(tmp_path: Path):
        """Test merge_evolutions when no existing evolutions."""
        config = PipelineConfig(agent_config={"skill_persistence_dir": str(tmp_path)})
        manager = SkillManager(config)
        manager.init_for_task("test_task")

        new_content = json.dumps({"entries": [{"id": "ev001", "content": "test"}]})
        merged = manager._merge_evolutions_for_skill("skill1", new_content)

        merged_data = json.loads(merged)
        assert len(merged_data["entries"]) == 1
        assert merged_data["entries"][0]["id"] == "ev001"

    @staticmethod
    def test_merge_evolutions_with_existing(tmp_path: Path):
        """Test merge_evolutions merges with existing entries."""
        config = PipelineConfig(agent_config={"skill_persistence_dir": str(tmp_path)})
        manager = SkillManager(config)
        manager.init_for_task("test_task")

        # Create existing evolutions
        skill_dir = tmp_path / "test_task" / "skill1"
        skill_dir.mkdir(parents=True)
        existing_content = json.dumps({
            "entries": [{"id": "ev001", "content": "original"}],
            "skill_id": "skill1"
        })
        (skill_dir / "evolutions.json").write_text(existing_content)

        # New content with same id (should overwrite) and new entry
        new_content = json.dumps({
            "entries": [
                {"id": "ev001", "content": "updated"},
                {"id": "ev002", "content": "new"}
            ]
        })
        merged = manager._merge_evolutions_for_skill("skill1", new_content)

        merged_data = json.loads(merged)
        assert len(merged_data["entries"]) == 2
        assert merged_data["entries"][0]["content"] == "updated"
        assert merged_data["entries"][1]["id"] == "ev002"


class TestSkillManagerGetSkillDirPath:
    """Test SkillManager.get_skill_dir_path method."""

    @staticmethod
    def test_get_skill_dir_path_latest(tmp_path: Path):
        """Test get_skill_dir_path returns latest directory."""
        config = PipelineConfig(agent_config={"skill_persistence_dir": str(tmp_path)})
        manager = SkillManager(config)
        manager.init_for_task("test_task")

        path = manager.get_skill_dir_path("skill1")
        assert path == tmp_path / "test_task" / "skill1" / "latest"

    @staticmethod
    def test_get_skill_dir_path_with_iteration(tmp_path: Path):
        """Test get_skill_dir_path returns iteration directory."""
        config = PipelineConfig(agent_config={"skill_persistence_dir": str(tmp_path)})
        manager = SkillManager(config)
        manager.init_for_task("test_task")

        path = manager.get_skill_dir_path("skill1", iteration=5)
        assert path == tmp_path / "test_task" / "skill1" / "iteration_005"
