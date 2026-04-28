# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Temporary lifecycle + read-only source workspace."""

from __future__ import annotations

import os
import shutil
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.memory.team.manager import TeamMemoryManager
from openjiuwen.core.memory.team.manager_params import TeamMemoryManagerParams


@pytest.fixture(autouse=True)
def _stub_memory_index_manager_get():
    """init_toolkit 内 MemberMemoryToolkit 不访问真实索引。"""
    mgr = MagicMock()
    mgr.closed = False
    with patch(
        "openjiuwen.core.memory.lite.manager.MemoryIndexManager.get",
        new_callable=AsyncMock,
        return_value=mgr,
    ):
        yield


class _MiniPromptBuilder:
    def __init__(self):
        self._sections = {}

    def add_section(self, section):
        self._sections[getattr(section, "name", None)] = section

    def remove_section(self, name):
        self._sections.pop(name, None)


class MockDeepAgentLite:
    def __init__(self):
        self.system_prompt_builder = _MiniPromptBuilder()


@pytest.fixture
def temp_dir():
    path = tempfile.mkdtemp()
    yield path
    shutil.rmtree(path, ignore_errors=True)


def test_read_only_manager_workspace_root_matches_source(temp_dir):
    params = TeamMemoryManagerParams(
        member_name="m1",
        team_name="t1",
        role="leader",
        lifecycle="temporary",
        scenario="general",
        embedding_config=None,
        workspace=None,
        sys_operation=None,
        team_memory_dir=None,
        language="en",
        prompt_mode="proactive",
        enable_auto_extract=False,
        read_only_source_workspace=temp_dir,
    )
    mgr = TeamMemoryManager(params)
    assert os.path.normpath(str(mgr._workspace.root_path)) == os.path.normpath(temp_dir)


@pytest.mark.asyncio
@patch("openjiuwen.core.memory.lite.config.is_memory_enabled", return_value=True)
async def test_init_toolkit_read_only_tools(mock_enabled, temp_dir):
    params = TeamMemoryManagerParams(
        member_name="m1",
        team_name="t1",
        role="teammate",
        lifecycle="temporary",
        scenario="general",
        embedding_config=None,
        workspace=None,
        sys_operation=None,
        team_memory_dir=None,
        language="en",
        prompt_mode="proactive",
        enable_auto_extract=False,
        read_only_source_workspace=temp_dir,
    )
    mgr = TeamMemoryManager(params)
    ok = await mgr.init_toolkit()
    assert ok is True
    assert mgr._toolkit is not None
    assert mgr._toolkit._read_only is True

    names = {t.card.name for t in mgr._toolkit.get_tools()}
    assert "memory_search" in names
    assert "write_memory" not in names
    assert "edit_memory" not in names

    await mgr.close()


@pytest.mark.asyncio
@patch("openjiuwen.core.memory.lite.config.is_memory_enabled", return_value=True)
@patch("openjiuwen.harness.prompts.sections.memory.build_memory_section")
async def test_load_and_inject_passes_read_only_to_build_memory_section(
    mock_build_section,
    mock_enabled,
    temp_dir,
):
    section_mock = MagicMock()
    section_mock.content = {"cn": "c", "en": "e"}
    section_mock.priority = 50
    mock_build_section.return_value = section_mock

    params = TeamMemoryManagerParams(
        member_name="m1",
        team_name="t1",
        role="teammate",
        lifecycle="temporary",
        scenario="general",
        embedding_config=None,
        workspace=None,
        sys_operation=None,
        team_memory_dir=None,
        language="en",
        prompt_mode="proactive",
        enable_auto_extract=False,
        read_only_source_workspace=temp_dir,
    )
    mgr = TeamMemoryManager(params)
    await mgr.init_toolkit()

    deep = MockDeepAgentLite()
    await mgr.load_and_inject(deep, query="")

    mock_build_section.assert_called_once()
    call_kw = mock_build_section.call_args.kwargs
    assert call_kw["read_only"] is True
    assert call_kw["language"] == "en"
    assert call_kw["is_proactive"] is True

    await mgr.close()


@pytest.mark.asyncio
@patch("openjiuwen.core.memory.lite.config.is_memory_enabled", return_value=True)
@patch("openjiuwen.harness.prompts.sections.coding_memory.build_coding_memory_section")
async def test_load_and_inject_coding_passes_read_only(
    mock_build_coding,
    mock_enabled,
    temp_dir,
):
    section_mock = MagicMock()
    section_mock.content = {"cn": "c", "en": "e"}
    section_mock.priority = 40
    mock_build_coding.return_value = section_mock

    params = TeamMemoryManagerParams(
        member_name="m1",
        team_name="t1",
        role="teammate",
        lifecycle="temporary",
        scenario="coding",
        embedding_config=None,
        workspace=None,
        sys_operation=None,
        team_memory_dir=None,
        language="en",
        prompt_mode="proactive",
        enable_auto_extract=False,
        read_only_source_workspace=temp_dir,
    )
    mgr = TeamMemoryManager(params)
    await mgr.init_toolkit()

    deep = MockDeepAgentLite()
    await mgr.load_and_inject(deep, query="")

    mock_build_coding.assert_called_once()
    assert mock_build_coding.call_args.kwargs["read_only"] is True

    await mgr.close()
