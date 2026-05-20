# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared static-analysis utilities for runtime extensions.

Used by both the verify stage (ExtendVerifyStage) and the merge stage
(MergeActivationBlock) to validate a RuntimeExtensionArtifact
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

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

    # Step 1: auto-fix formatting and lint issues
    for fix_cmd in (
        ["ruff", "format", root_str],
        ["ruff", "check", "--fix", root_str],
    ):
        try:
            proc = await asyncio.create_subprocess_exec(
                *fix_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
        except FileNotFoundError:
            logger.debug("ruff not available, skipping auto-fix")
            return errors

    # Step 2: check remaining lint errors
    try:
        proc = await asyncio.create_subprocess_exec(
            "ruff",
            "check",
            root_str,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
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


async def run_static_checks_against_runtime(
    *,
    runtime_ext: RuntimeExtensionArtifact,
    session_id_prefix: str,
) -> ExtStaticCheckResult:
    """Manifest schema + load_runtime_rails/tools instantiation + skill_dirs + ruff."""
    result = ExtStaticCheckResult()
    # Layer 1: Structure check — manifest + class instantiation
    try:
        config_path = Path(runtime_ext.config_path)
        if not config_path.is_file():
            raise FileNotFoundError(f"Missing extension manifest: {config_path}")

        rails = load_runtime_rails(
            runtime_ext,
            session_id=session_id_prefix,
        )
        tools = load_runtime_tools(
            runtime_ext,
            session_id=session_id_prefix,
        )
        for rail_cls in rails:
            rail_cls()
        for tool_cls in tools:
            tool_cls()
        result.rails_count = len(rails)
        result.tools_count = len(tools)
        skill_dirs = load_runtime_skill_dirs(
            runtime_ext,
        )
        result.skill_dirs_count = len(skill_dirs)
        for sd in skill_dirs:
            sd_path = Path(sd)
            skill_mds = list(sd_path.rglob("SKILL.md"))
            result.skills_count += len(skill_mds)
            if not skill_mds:
                result.errors.append(f"Skill dir has no SKILL.md: {sd}")
    except Exception as exc:
        result.errors.append(f"Structure check failed: {exc}")

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
