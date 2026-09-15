# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Skill listing must carry the absolute directory of each skill.

A ``SKILL.md`` refers to the files bundled next to it with relative paths
(``scripts/fetch.py``, ``references/report-format.md``).  Those paths are only
resolvable when the model also knows the absolute directory the skill was
installed in.  The skills tree is not necessarily under the search root the
filesystem tools default to, so a model that is not given the directory cannot
recover it by searching either.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode, SysOperationCard
from openjiuwen.core.sys_operation.cwd import init_cwd
from openjiuwen.harness.prompts.sections.skills import (
    build_all_mode_skill_prompt,
    build_skill_line,
    build_skill_lines,
)
from openjiuwen.harness.tools import BashTool, GlobTool, ReadFileTool

_SKILL_NAME = "repository-activity"
_BUNDLED_REFERENCE = "references/report-format.md"
_BUNDLED_SCRIPT = "scripts/fetch_repository_activity.py"
_REFERENCE_BODY = "# Report format\n\nOne section per repository.\n"


@pytest.fixture
def skills_and_project_dirs():
    """Build the standard workspace layout: ``skills/`` beside ``projects/``.

    A session runs rooted in ``projects/<name>``, so the skills tree is a
    sibling of the search root, not a descendant of it.
    """
    root = tempfile.mkdtemp()
    try:
        skills_dir = os.path.join(root, "skills")
        skill_dir = os.path.join(skills_dir, _SKILL_NAME)
        os.makedirs(os.path.join(skill_dir, "references"))
        os.makedirs(os.path.join(skill_dir, "scripts"))
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as handle:
            handle.write(
                "---\n"
                "description: Summarise repository activity\n"
                "---\n\n"
                f"Run `{_BUNDLED_SCRIPT}` and format the result as described in "
                f"`{_BUNDLED_REFERENCE}`.\n"
            )
        with open(
            os.path.join(skill_dir, _BUNDLED_REFERENCE), "w", encoding="utf-8"
        ) as handle:
            handle.write(_REFERENCE_BODY)
        with open(
            os.path.join(skill_dir, _BUNDLED_SCRIPT), "w", encoding="utf-8"
        ) as handle:
            handle.write("print('activity')\n")

        project_dir = os.path.join(root, "projects", "reporting")
        os.makedirs(project_dir)
        yield skill_dir, project_dir
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest_asyncio.fixture(name="sys_op")
async def sys_op_fixture():
    await Runner.start()
    card_id = "test_skills_section_op"
    Runner.resource_mgr.add_sys_operation(
        SysOperationCard(
            id=card_id,
            mode=OperationMode.LOCAL,
            work_config=LocalWorkConfig(shell_allowlist=["echo", "ls", "python", "python3"]),
        )
    )
    yield Runner.resource_mgr.get_sys_operation(card_id)
    Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
    await Runner.stop()


def _rendered_prompt(skill_directory: str, language: str) -> str:
    line = build_skill_line(
        index=0,
        skill_name=_SKILL_NAME,
        description="Summarise repository activity",
        skill_directory=skill_directory,
        language=language,
    )
    return build_all_mode_skill_prompt(build_skill_lines([line]), language=language)


def _directory_from_prompt(prompt: str) -> str:
    """Recover the skill directory from the prompt the way a model would."""
    match = re.search(r"^\s*Directory:\s*(.+?)\s*$", prompt, flags=re.MULTILINE)
    assert match is not None, prompt
    return match.group(1)


def test_skill_line_omits_directory_when_not_supplied():
    line = build_skill_line(index=0, skill_name=_SKILL_NAME, description="d")
    assert "Directory:" not in line


@pytest.mark.parametrize("language", ["cn", "en"])
def test_all_mode_prompt_carries_absolute_skill_directory(skills_and_project_dirs, language):
    skill_dir, _ = skills_and_project_dirs

    prompt = _rendered_prompt(skill_dir, language)

    assert f"Directory: {skill_dir}" in prompt
    assert _directory_from_prompt(prompt) == skill_dir
    # The header must explain what the directory is for, otherwise the path is
    # just noise the model has no rule for applying.
    assert ("绝对路径" in prompt) if language == "cn" else ("absolute path" in prompt)


@pytest.mark.asyncio
async def test_bundled_file_is_readable_through_the_advertised_directory(
    sys_op, skills_and_project_dirs
):
    """Walk the whole route: prompt -> directory -> read a bundled file."""
    skill_dir, project_dir = skills_and_project_dirs
    init_cwd(project_dir, project_dir, workspace=project_dir)

    advertised = _directory_from_prompt(_rendered_prompt(skill_dir, "en"))
    reference_path = os.path.join(advertised, _BUNDLED_REFERENCE)

    read_res = await ReadFileTool(sys_op).invoke({"file_path": reference_path})

    assert read_res.success is True, read_res.error
    assert "One section per repository." in read_res.data["content"]


@pytest.mark.asyncio
async def test_bundled_script_runs_through_the_advertised_directory(
    sys_op, skills_and_project_dirs
):
    """The advertised directory is usable for execution, not only for reading."""
    skill_dir, project_dir = skills_and_project_dirs
    init_cwd(project_dir, project_dir, workspace=project_dir)

    advertised = _directory_from_prompt(_rendered_prompt(skill_dir, "en"))
    script_path = os.path.join(advertised, _BUNDLED_SCRIPT)

    bash_res = await BashTool(sys_op).invoke({"command": f'python3 "{script_path}"'})

    assert bash_res.success is True, bash_res.error
    assert "activity" in bash_res.data["content"]


@pytest.mark.asyncio
async def test_bundled_file_is_not_reachable_by_searching_the_session_directory(
    sys_op, skills_and_project_dirs
):
    """Why the directory has to be advertised: search alone cannot find it.

    ``glob`` and ``grep`` root themselves at the session directory when no path
    is given, and the skills tree is a sibling of that root, so an unqualified
    recursive pattern can never reach a bundled file.
    """
    skill_dir, project_dir = skills_and_project_dirs
    init_cwd(project_dir, project_dir, workspace=project_dir)

    glob_res = await GlobTool(sys_op).invoke({"pattern": "**/*"})

    assert glob_res.success is True, glob_res.error
    assert glob_res.data["count"] == 0

    scoped = await GlobTool(sys_op).invoke({"pattern": "**/*.md", "path": skill_dir})
    assert scoped.success is True, scoped.error
    assert scoped.data["count"] > 0
