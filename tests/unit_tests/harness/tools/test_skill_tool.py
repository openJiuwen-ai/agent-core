# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import os
from pathlib import Path
import tempfile
import shutil
from typing import List

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.skills.skill_manager import Skill
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig
from openjiuwen.harness.tools import CodeTool
from openjiuwen.harness.tools.skill_tool import SkillTool


@pytest.fixture
def temp_dir():
    dir_path = tempfile.mkdtemp()
    yield dir_path
    shutil.rmtree(dir_path)

@pytest_asyncio.fixture(name="sys_op")
async def sys_op_fixture():
    await Runner.start()
    card_id = "test_skill_tool_op"
    card = SysOperationCard(id=card_id, mode=OperationMode.LOCAL, work_config=LocalWorkConfig(
        shell_allowlist=[]
    ))
    Runner.resource_mgr.add_sys_operation(card)
    op = Runner.resource_mgr.get_sys_operation(card_id)
    yield op
    Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
    await Runner.stop()

def _write_skill(
    root: Path,
    name: str,
    description: str,
    body: str,
) -> Path:
    """Create a minimal skill directory with SKILL.md."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\n"
        f"description: {description}\n"
        "---\n\n"
        f"# {name}\n{body}",
        encoding="utf-8",
    )
    return Skill(name=name, description=description, directory=skill_dir)

def _write_skill_reference_file(
    root: Path,
    skill_name: str,
    relative_directory: str,
    body: str,
) -> Path:
    """Create a minimal skill directory with SKILL.md."""
    file_dir = root / skill_name / relative_directory
    file_dir.parent.mkdir(parents=True, exist_ok=True)
    file_dir.write_text(
        f"{body}",
        encoding="utf-8",
    )

def _data_contains_str(data: dict, query_str: str):
    for val in data.values():
        if query_str in val:
            return True
    return False

@pytest.mark.asyncio
async def test_skill_tool(sys_op, temp_dir):
    skills_root = Path(temp_dir) / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    skill_list = []
    skill = _write_skill(skills_root, "test_skill_1", "skill description 1", "skill body 1")
    skill_list.append(skill)

    def get_skill_list():
        return skill_list
    
    skill_tool = SkillTool(sys_op, get_skill_list)

    skill_res = await skill_tool.invoke({"skill_name": "test_skill_1", "relative_file_path": ""})
    assert skill_res.success is True
    assert _data_contains_str(skill_res.data, str(skill.directory))
    assert _data_contains_str(skill_res.data, "skill body 1")

@pytest.mark.asyncio
async def test_skill_tool_invalid_skill(sys_op, temp_dir):
    skills_root = Path(temp_dir) / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    skill_list = []
    skill = _write_skill(skills_root, "test_skill_1", "skill description 1", "skill body 1")
    skill_list.append(skill)

    def get_skill_list():
        return skill_list
    
    skill_tool = SkillTool(sys_op, get_skill_list)

    skill_res = await skill_tool.invoke({"skill_name": "test_skill_2", "relative_file_path": ""})
    assert skill_res.success is False
    assert skill_res.error is not None

@pytest.mark.asyncio
async def test_skill_tool_reference_file(sys_op, temp_dir):
    skills_root = Path(temp_dir) / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    skill_list = []
    skill = _write_skill(skills_root, "test_skill_1", "skill description 1", "skill body 1")
    _write_skill_reference_file(skills_root, "test_skill_1", "reference/temp_file.md", "test_skill_1 temp file content")
    skill_list.append(skill)

    def get_skill_list():
        return skill_list
    
    skill_tool = SkillTool(sys_op, get_skill_list)

    skill_res = await skill_tool.invoke({"skill_name": "test_skill_1", "relative_file_path": "reference/temp_file.md"})
    assert skill_res.success is True
    assert _data_contains_str(skill_res.data, "test_skill_1 temp file content")

@pytest.mark.asyncio
async def test_skill_tool_invalid_reference_file(sys_op, temp_dir):
    skills_root = Path(temp_dir) / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    skill_list = []
    skill = _write_skill(skills_root, "test_skill_1", "skill description 1", "skill body 1")
    _write_skill_reference_file(skills_root, "test_skill_1", "reference/temp_file.md", "test_skill_1 temp file content")
    skill_list.append(skill)

    def get_skill_list():
        return skill_list
    
    skill_tool = SkillTool(sys_op, get_skill_list)

    skill_res = await skill_tool.invoke({"skill_name": "test_skill_1", "relative_file_path": "reference/unknown_file.md"})
    assert skill_res.success is False
    assert skill_res.error is not None
