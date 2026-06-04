# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from openjiuwen.core.common.logging import logger

from ...base import BaseBenchAdapter, register_benchmark
from ...docker_env import DockerEnvironment
from ...models import EvalResult, Task


@register_benchmark("skillsbench")
class SkillsBenchAdapter(BaseBenchAdapter):
    @staticmethod
    def _git_path() -> str:
        git_path = shutil.which("git")
        if git_path is None:
            raise RuntimeError("git executable not found in PATH")
        return git_path

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self._repo_url = self._config.get("repo_url")
        self._repo_path = Path(self._config.get("repo_path", "./skillsbench"))
        self._tasks_dir = Path(self._config.get("tasks_dir", "tasks"))
        self._workspace_dir = self._config.get("workspace_dir", "/workspace")
        self._skills_mode = self._config.get("skills_mode", "with_skills")

    @staticmethod
    def name() -> str:
        return "skillsbench"

    def clone_repo(self) -> bool:
        """Automatically clone or update the skillsbench repository."""
        if not self._repo_url:
            logger.info(f"  No repo_url configured, using local tasks dir: {self._tasks_dir}")
            return True

        logger.info(f"  Cloning skillsbench repo: {self._repo_url} -> {self._repo_path}")
        
        if self._repo_path.exists():
            logger.info(f"  Repo already exists, pulling latest changes...")
            try:
                result = subprocess.run(
                    [self._git_path(), "pull"],
                    cwd=self._repo_path,
                    capture_output=True,
                    text=True,
                    timeout=120
                )
                if result.returncode == 0:
                    logger.info(f"  ✅ Repo updated successfully")
                else:
                    logger.warning(f"  ⚠ Git pull failed: {result.stderr}")
            except subprocess.TimeoutExpired:
                logger.warning(f"  ⚠ Git pull timed out")
        else:
            # Clone new repository
            try:
                result = subprocess.run(
                    [self._git_path(), "clone", self._repo_url, str(self._repo_path)],
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                if result.returncode == 0:
                    logger.info(f"  ✅ Repo cloned successfully")
                else:
                    logger.error(f"  ❌ Git clone failed: {result.stderr}")
                    return False
            except subprocess.TimeoutExpired:
                logger.error(f"  ❌ Git clone timed out")
                return False

        # Update tasks_dir to cloned directory
        tasks_in_repo = self._repo_path / "tasks"
        if tasks_in_repo.exists():
            self._tasks_dir = tasks_in_repo
            logger.info(f"  Updated tasks_dir to: {self._tasks_dir}")
        else:
            logger.warning(f"  ⚠ tasks directory not found in repo, using default: {self._tasks_dir}")

        return True

    def load_tasks(self) -> list[Task]:
        tasks: list[Task] = []
        if not self._tasks_dir.exists():
            logger.warning(f"Tasks directory not found: {self._tasks_dir}")
            return tasks

        for task_dir in sorted(self._tasks_dir.iterdir()):
            if not task_dir.is_dir():
                continue
            task = self._load_single_task(task_dir)
            if task is not None:
                tasks.append(task)

        logger.info(f"Loaded {len(tasks)} tasks from {self._tasks_dir}")
        return tasks

    def _load_single_task(self, task_dir: Path) -> Task | None:
        instruction_path = task_dir / "instruction.md"
        if not instruction_path.exists():
            return None

        instruction = instruction_path.read_text(encoding="utf-8").strip()
        if not instruction:
            return None

        skills_dir = task_dir / "environment" / "skills"
        has_skills = skills_dir.is_dir()
        skills = [d.name for d in skills_dir.iterdir() if d.is_dir()] if has_skills else []

        return Task(
            task_id=task_dir.name,
            instruction=instruction,
            environment_spec={
                "type": "docker",
                "dockerfile": str(task_dir / "environment" / "Dockerfile"),
                "build_context": str(task_dir / "environment"),
                "tests_dir": str(task_dir / "tests"),
                "solution_dir": str(task_dir / "solution"),
                "skills_dir": str(skills_dir) if has_skills else "",
                "task_dir": str(task_dir),
                "cpus": self._config.get("cpus", 1),
                "memory_mb": self._config.get("memory_mb", 2048),
                "timeout": self._config.get("timeout", 900),
                "test_command": self._config.get(
                    "test_command", f"cd {self._workspace_dir} && bash tests/test.sh"
                ),
                "test_timeout": self._config.get("test_timeout", 300),
            },
            has_skills=has_skills,
            skills=skills,
            metadata={"task_dir": str(task_dir)},
        )

    async def prepare_environment(self, task: Task, env: DockerEnvironment) -> None:
        workspace = self._workspace_dir
        await env.exec(f"mkdir -p {workspace}", timeout=10)
        
        # Create symlinks to match test.sh expectations
        # test.sh expects /tests/ and /logs/ paths but files are in workspace
        await env.exec(f"ln -sf {workspace}/tests /tests 2>/dev/null || true", timeout=10)
        await env.exec(f"ln -sf {workspace}/logs /logs 2>/dev/null || true", timeout=10)

        env_spec = task.environment_spec
        task_dir = Path(env_spec.get("task_dir", ""))

        tests_dir = Path(env_spec.get("tests_dir", ""))
        if tests_dir.exists() and tests_dir.is_dir():
            await env.exec(f"mkdir -p {workspace}/tests", timeout=10)
            for item in tests_dir.iterdir():
                if item.is_file():
                    await env.copy_to(item, f"{workspace}/tests/{item.name}")
                elif item.is_dir():
                    await env.exec(f"mkdir -p {workspace}/tests/{item.name}", timeout=10)
                    for f in item.iterdir():
                        if f.is_file():
                            await env.copy_to(f, f"{workspace}/tests/{item.name}/{f.name}")
            logger.info(f"    ✓ Tests copied to {workspace}/tests")

        workspace_src = task_dir / "workspace"
        if workspace_src.exists() and workspace_src.is_dir():
            for item in workspace_src.iterdir():
                if item.is_file():
                    await env.copy_to(item, f"{workspace}/{item.name}")
                elif item.is_dir():
                    await env.exec(f"mkdir -p {workspace}/{item.name}", timeout=10)
                    for f in item.iterdir():
                        if f.is_file():
                            await env.copy_to(f, f"{workspace}/{item.name}/{f.name}")
            logger.info(f"    ✓ Workspace files copied")

        solution_dir = Path(env_spec.get("solution_dir", ""))
        if solution_dir.exists() and solution_dir.is_dir():
            for item in solution_dir.iterdir():
                if item.is_file():
                    await env.copy_to(item, f"{workspace}/{item.name}")
            logger.info(f"    ✓ Solution files copied")

        instruction_src = task_dir / "instruction.md"
        if instruction_src.exists():
            await env.copy_to(instruction_src, f"{workspace}/instruction.md")
            logger.info(f"    ✓ Instruction copied")

    async def evaluate(self, env: DockerEnvironment, task: Task) -> EvalResult:
        test_command = task.environment_spec.get(
            "test_command", "python -m pytest tests/test_outputs.py -v"
        )
        # Priority: read verifier.timeout_sec from task.toml, then test_timeout from environment_spec
        verifier_timeout = task.metadata.get("verifier", {}).get("timeout_sec", 300)
        test_timeout = task.environment_spec.get("test_timeout", verifier_timeout)

        # Fix: Convert absolute path /tests/test_outputs.py to relative path
        # Test files are copied to workspace/tests/ directory
        test_command = test_command.replace("/tests/test_outputs.py", "tests/test_outputs.py")

        result = await env.exec(test_command, timeout=test_timeout, workdir=self._workspace_dir)

        output = result.stdout + result.stderr
        pass_rate = self._calculate_pass_rate(output)
        passed = result.success and pass_rate >= 1.0
        failed_tests = self._extract_failed_tests(output)

        return EvalResult(
            passed=passed,
            pass_rate=pass_rate,
            test_output=output,
            returncode=result.returncode,
            failed_tests=failed_tests,
            test_details={
                "returncode": result.returncode,
                "output": output,
                "pass_rate": pass_rate,
                "failed_tests": failed_tests,
            },
        )

    @staticmethod
    def _calculate_pass_rate(output: str) -> float:
        passed_match = re.search(r"(\d+)\s+passed", output)
        failed_match = re.search(r"(\d+)\s+failed", output)
        error_match = re.search(r"(\d+)\s+error", output)

        passed = int(passed_match.group(1)) if passed_match else 0
        failed = int(failed_match.group(1)) if failed_match else 0
        errors = int(error_match.group(1)) if error_match else 0

        total = passed + failed + errors
        if total == 0:
            return 0.0

        return passed / total

    @staticmethod
    def _extract_failed_tests(output: str) -> list[str]:
        failed_tests: list[str] = []

        failed_pattern = re.compile(r"FAILED\s+(.+?)\s+-")
        for match in failed_pattern.finditer(output):
            test_name = match.group(1).strip()
            if test_name not in failed_tests:
                failed_tests.append(test_name)

        error_pattern = re.compile(r"ERROR\s+(.+?)\s+-")
        for match in error_pattern.finditer(output):
            test_name = match.group(1).strip()
            if test_name not in failed_tests:
                failed_tests.append(test_name)

        return failed_tests
