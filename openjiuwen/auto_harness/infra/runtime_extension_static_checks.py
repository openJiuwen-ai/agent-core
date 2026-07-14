# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared static-analysis utilities for runtime extensions.

Used by both the verify stage (ExtendVerifyStage) and the merge stage
(MergeActivationBlock) to validate a RuntimeExtensionArtifact
"""

from __future__ import annotations

import os
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from openjiuwen.auto_harness.infra.runtime_extension_loader import (
    load_runtime_rails,
    load_runtime_skill_dirs,
    load_runtime_tools,
)
from openjiuwen.auto_harness.schema import (
    RuntimeExtensionArtifact,
)
from openjiuwen.core.common.logging import logger


@dataclass
class ExtStaticCheckResult:
    """Static verification counts and errors for an extension."""

    errors: list[str] | None = None
    rails_count: int = 0
    tools_count: int = 0
    skills_count: int = 0
    skill_dirs_count: int = 0

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


def _validate_skill_frontmatter(skill_md_path: Path) -> list[str]:
    """Validate SKILL.md has required frontmatter fields.

    Returns a list of error messages, empty if valid.
    """
    errors: list[str] = []
    if not skill_md_path.is_file():
        errors.append(f"SKILL.md not found: {skill_md_path}")
        return errors

    try:
        text = skill_md_path.read_text(encoding="utf-8")
    except Exception as e:
        errors.append(f"Cannot read SKILL.md: {skill_md_path}: {e}")
        return errors

    if not text.startswith("---"):
        errors.append(
            f"SKILL.md missing frontmatter: {skill_md_path}"
        )
        return errors

    parts = text.split("---", 2)
    if len(parts) < 3:
        errors.append(
            f"SKILL.md malformed frontmatter: {skill_md_path}"
        )
        return errors

    _, yaml_block, _ = parts
    try:
        data = yaml.safe_load(yaml_block) or {}
    except yaml.YAMLError as e:
        errors.append(
            f"SKILL.md frontmatter YAML error: {skill_md_path}: {e}"
        )
        return errors

    if not isinstance(data, dict):
        errors.append(
            f"SKILL.md frontmatter not a dict: {skill_md_path}"
        )
        return errors

    name = data.get("name")
    if not name or not isinstance(name, str) or not name.strip():
        errors.append(
            f"SKILL.md missing 'name' field: {skill_md_path}"
        )

    description = data.get("description")
    if not description or not isinstance(description, str) or not description.strip():
        errors.append(
            f"SKILL.md missing 'description' field: {skill_md_path}"
        )

    return errors


async def check_ruff(
    extension_root: Path,
) -> list[str]:
    """Auto-fix formatting, then lint-check on extension_root.

    Runs ``ruff format`` (auto-fix) and ``ruff check --fix``
    first so that agent-generated code gets cleaned up before
    we report real errors.

    Returns:
        List of lint error descriptions.
    """
    errors: list[str] = []
    root_str = str(extension_root)
    env = _build_ruff_env()

    # Step 1: auto-fix formatting and lint issues
    python_executable = sys.executable
    for fix_cmd in (
        [python_executable, "-m", "ruff", "format", root_str],
        [python_executable, "-m", "ruff", "check", "--fix", root_str],
    ):
        try:
            proc = await asyncio.create_subprocess_exec(
                *fix_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            await proc.communicate()
        except FileNotFoundError:
            logger.debug("ruff not available, skipping auto-fix")
            return errors

    # Step 2: check remaining lint errors
    try:
        proc = await asyncio.create_subprocess_exec(
            python_executable,
            "-m",
            "ruff",
            "check",
            root_str,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            output = stdout.decode("utf-8", errors="replace").strip()
            if not output:
                output = stderr.decode("utf-8", errors="replace").strip()
            errors.append(f"ruff check failed: {output[:500]}")
    except FileNotFoundError:
        logger.debug("ruff not available, skipping lint")

    return errors


def _build_ruff_env() -> dict[str, str]:
    """Build subprocess env that ensures ruff can be found."""
    env = dict(os.environ)
    env["CI"] = "1"
    venv = os.environ.get("VIRTUAL_ENV")
    if not venv:
        return env
    venv_path = Path(venv)
    if sys.platform == "win32":
        bin_dir = str(venv_path / "Scripts")
    else:
        bin_dir = str(venv_path / "bin")
    pathsep = os.pathsep
    existing = env.get("PATH", "")
    env["PATH"] = (
        f"{bin_dir}{pathsep}{existing}"
        if existing
        else bin_dir
    )
    return env


async def run_static_checks_against_runtime(
    *,
    runtime_ext: RuntimeExtensionArtifact,
    session_id_prefix: str,
) -> ExtStaticCheckResult:
    """Manifest schema + load_runtime_rails/tools instantiation + skill_dirs + ruff."""
    result = ExtStaticCheckResult()
    # Layer 1: Structure check — manifest + class instantiation
    config_path = Path(runtime_ext.config_path)
    if not config_path.is_file():
        raise FileNotFoundError(f"Missing extension manifest: {config_path}")
    # Rails loading
    try:
        rails = load_runtime_rails(
            runtime_ext,
            session_id=session_id_prefix,
        )
        for rail_cls in rails:
            rail_cls()
        result.rails_count = len(rails)
    except Exception as exc:
        result.errors.append(f"Rails load failed: {exc}")

    # Tools loading
    try:
        tools = load_runtime_tools(
            runtime_ext,
            session_id=session_id_prefix,
        )
        for tool_cls in tools:
            tool_cls()
        result.tools_count = len(tools)
    except Exception as exc:
        result.errors.append(f"Tools load failed: {exc}")

    # Skill dirs loading
    try:
        skill_dirs = load_runtime_skill_dirs(
            runtime_ext,
        )
        result.skill_dirs_count = len(skill_dirs)
    except Exception as exc:
        result.errors.append(f"Skill dirs load failed: {exc}")
        skill_dirs = []

    # SKILL.md frontmatter validation
    for sd in skill_dirs:
        sd_path = Path(sd)
        try:
            skill_mds = list(sd_path.rglob("SKILL.md"))
            result.skills_count += len(skill_mds)
            if not skill_mds:
                result.errors.append(f"Skill dir has no SKILL.md: {sd}")
            else:
                for skill_md in skill_mds:
                    fm_errors = _validate_skill_frontmatter(skill_md)
                    result.errors.extend(fm_errors)
        except Exception as exc:
            result.errors.append(f"Skill validation failed for {sd}: {exc}")

    # Layer 2: Import check — skipped for now.
    # Generated code uses absolute imports like
    # ``from openjiuwen.extensions.harness.<ext>…`` which
    # cannot resolve in the worktree environment.  The
    # runtime_extension_loader handles this at load time.
    extension_root = Path(runtime_ext.runtime_path)

    # Layer 3: Lint check — ruff on extension root
    if extension_root.is_dir():
        lint_errors = await check_ruff(
            extension_root,
        )
        result.errors.extend(lint_errors)

    return result


__all__ = [
    "ExtStaticCheckResult",
    "check_ruff",
    "run_static_checks_against_runtime",
]
