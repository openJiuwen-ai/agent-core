# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for SkillsBench benchmark adapter."""

from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from openjiuwen.agent_evolving.evaluator.evaluator_pipeline.adapters.benchmarks.skillsbench import (
    SkillsBenchAdapter,
)


class TestSkillsBenchAdapterInit:
    """Test SkillsBenchAdapter initialization."""

    @staticmethod
    def test_default_init():
        """Test default initialization."""
        adapter = SkillsBenchAdapter()
        
        assert adapter._repo_url is None
        assert adapter._repo_path == Path("./skillsbench")
        assert adapter._tasks_dir == Path("tasks")
        assert adapter._workspace_dir == "/workspace"
        assert adapter._skills_mode == "with_skills"

    @staticmethod
    def test_init_with_config():
        """Test initialization with config."""
        config = {
            "repo_url": "https://example.com/repo.git",
            "repo_path": "./custom_repo",
            "tasks_dir": "custom_tasks",
            "workspace_dir": "/custom/workspace",
            "skills_mode": "without_skills",
        }
        adapter = SkillsBenchAdapter(config)
        
        assert adapter._repo_url == "https://example.com/repo.git"
        assert adapter._repo_path == Path("./custom_repo")
        assert adapter._tasks_dir == Path("custom_tasks")
        assert adapter._workspace_dir == "/custom/workspace"
        assert adapter._skills_mode == "without_skills"

    @staticmethod
    def test_name_staticmethod():
        """Test name() static method."""
        assert SkillsBenchAdapter.name() == "skillsbench"


class TestSkillsBenchAdapterCloneRepo:
    """Test SkillsBenchAdapter clone_repo method."""

    @staticmethod
    def test_clone_repo_no_url():
        """Test clone_repo when no URL configured."""
        adapter = SkillsBenchAdapter()
        result = adapter.clone_repo()
        
        assert result is True  # Returns True when using local directory

    @staticmethod
    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    def test_clone_repo_already_exists(mock_exists, mock_run):
        """Test clone_repo when repo already exists."""
        mock_exists.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stdout="Already up to date.", stderr="")
        
        adapter = SkillsBenchAdapter({
            "repo_url": "https://example.com/repo.git",
        })
        result = adapter.clone_repo()
        
        assert result is True
        mock_run.assert_called_once()

    @staticmethod
    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    def test_clone_repo_clone_new(mock_exists, mock_run):
        """Test clone_repo when repo doesn't exist."""
        mock_exists.return_value = False
        mock_run.return_value = MagicMock(returncode=0, stdout="Cloning into...", stderr="")
        
        adapter = SkillsBenchAdapter({
            "repo_url": "https://example.com/repo.git",
        })
        result = adapter.clone_repo()
        
        assert result is True
        mock_run.assert_called_once()

    @staticmethod
    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    def test_clone_repo_clone_failure(mock_exists, mock_run):
        """Test clone_repo when git clone fails."""
        mock_exists.return_value = False
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Authentication failed")
        
        adapter = SkillsBenchAdapter({
            "repo_url": "https://example.com/repo.git",
        })
        result = adapter.clone_repo()
        
        assert result is False

    @staticmethod
    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    def test_clone_repo_timeout(mock_exists, mock_run):
        """Test clone_repo when git clone times out."""
        import subprocess as sp
        mock_exists.return_value = False
        mock_run.side_effect = sp.TimeoutExpired(cmd=["git", "clone"], timeout=300)
        
        adapter = SkillsBenchAdapter({
            "repo_url": "https://example.com/repo.git",
        })
        result = adapter.clone_repo()
        
        assert result is False


class TestSkillsBenchAdapterLoadTasks:
    """Test SkillsBenchAdapter load_tasks method."""

    @staticmethod
    def test_load_tasks_dir_not_exists():
        """Test load_tasks when tasks directory doesn't exist."""
        adapter = SkillsBenchAdapter()
        
        with patch.object(Path, "exists", return_value=False):
            tasks = adapter.load_tasks()
        
        assert tasks == []


class TestSkillsBenchAdapterCalculatePassRate:
    """Test SkillsBenchAdapter _calculate_pass_rate method."""

    @staticmethod
    def test_calculate_pass_rate_all_passed():
        """Test calculate pass rate when all tests pass."""
        output = "test1 passed\ntest2 passed\n2 passed"
        result = SkillsBenchAdapter._calculate_pass_rate(output)
        
        assert result == 1.0

    @staticmethod
    def test_calculate_pass_rate_some_failed():
        """Test calculate pass rate when some tests fail."""
        # Format: "X passed" and "Y failed" on separate lines
        output = "test_example.py::test1 PASSED\ntest_example.py::test2 FAILED\n1 passed, 1 failed"
        result = SkillsBenchAdapter._calculate_pass_rate(output)
        
        # 1 passed, 1 failed = 1/2 = 0.5
        assert result == 0.5

    @staticmethod
    def test_calculate_pass_rate_with_errors():
        """Test calculate pass rate when tests have errors."""
        output = "test_example.py::test1 PASSED\ntest_example.py::test2 ERROR\n1 passed, 1 error"
        result = SkillsBenchAdapter._calculate_pass_rate(output)
        
        # 1 passed, 1 error = 1/2 = 0.5
        assert result == 0.5

    @staticmethod
    def test_calculate_pass_rate_no_tests():
        """Test calculate pass rate when no tests found."""
        output = "No tests found"
        result = SkillsBenchAdapter._calculate_pass_rate(output)
        
        assert result == 0.0


class TestSkillsBenchAdapterExtractFailedTests:
    """Test SkillsBenchAdapter _extract_failed_tests method."""

    @staticmethod
    def test_extract_failed_tests_with_failures():
        """Test extract failed tests from output."""
        output = """
FAILED test_example.py::test_func1 - AssertionError
PASSED test_example.py::test_func2
FAILED test_example.py::test_func3 - ValueError
"""
        result = SkillsBenchAdapter._extract_failed_tests(output)
        
        assert "test_example.py::test_func1" in result
        assert "test_example.py::test_func3" in result
        assert "test_example.py::test_func2" not in result

    @staticmethod
    def test_extract_failed_tests_with_errors():
        """Test extract failed tests with errors."""
        output = """
ERROR test_example.py::test_setup - Exception
PASSED test_example.py::test_func
"""
        result = SkillsBenchAdapter._extract_failed_tests(output)
        
        assert "test_example.py::test_setup" in result

    @staticmethod
    def test_extract_failed_tests_no_failures():
        """Test extract failed tests when no failures."""
        output = "All tests passed\n2 passed"
        result = SkillsBenchAdapter._extract_failed_tests(output)
        
        assert result == []
