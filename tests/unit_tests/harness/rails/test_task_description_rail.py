# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for TaskDescriptionRail.

Covers the rail's prompt-section injection contract:
* ``PromptSection`` requires a ``{language: text}`` mapping, not a plain string.
* An empty task file must not be marked injected, so the rail keeps retrying
  until the file is populated.
* ``uninit`` removes the injected section.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from openjiuwen.harness.prompts import PromptSection, SystemPromptBuilder
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.rails.task_description_rail import TaskDescriptionRail


def _run(coro):
    return asyncio.run(coro)


def _make_rail(builder: SystemPromptBuilder, task_path: str) -> TaskDescriptionRail:
    rail = TaskDescriptionRail(task_path)
    rail.init(SimpleNamespace(system_prompt_builder=builder))
    return rail


def test_injects_section_with_dict_content(tmp_path: Path) -> None:
    """The injected PromptSection carries a language-keyed mapping."""
    task_file = tmp_path / "task.md"
    task_file.write_text("Build the thing.", encoding="utf-8")
    builder = SystemPromptBuilder(language="en")
    rail = _make_rail(builder, str(task_file))

    _run(rail.before_invoke(SimpleNamespace()))

    section = builder.get_section(SectionName.TASK_DESCRIPTION)
    assert section is not None
    assert isinstance(section, PromptSection)
    assert isinstance(section.content, dict)
    assert "en" in section.content
    assert "cn" in section.content
    assert "Build the thing." in section.render("en")


def test_empty_file_is_not_marked_injected(tmp_path: Path) -> None:
    """An empty task file injects nothing and is retried on the next model call."""
    task_file = tmp_path / "task.md"
    task_file.write_text("   ", encoding="utf-8")
    builder = SystemPromptBuilder(language="en")
    rail = _make_rail(builder, str(task_file))

    _run(rail.before_invoke(SimpleNamespace()))

    assert builder.get_section(SectionName.TASK_DESCRIPTION) is None
    assert rail._injected is False

    # Populate the file; the next model call must now inject it.
    task_file.write_text("Now there is a task.", encoding="utf-8")
    _run(rail.before_model_call(SimpleNamespace()))

    section = builder.get_section(SectionName.TASK_DESCRIPTION)
    assert section is not None
    assert "Now there is a task." in section.render("en")


def test_missing_file_is_not_marked_injected(tmp_path: Path) -> None:
    """A missing file injects nothing and is not marked injected."""
    builder = SystemPromptBuilder(language="en")
    rail = _make_rail(builder, str(tmp_path / "does-not-exist.md"))

    _run(rail.before_invoke(SimpleNamespace()))

    assert builder.get_section(SectionName.TASK_DESCRIPTION) is None
    assert rail._injected is False


def test_uninit_removes_section(tmp_path: Path) -> None:
    """uninit removes the injected section so a reloaded rail starts clean."""
    task_file = tmp_path / "task.md"
    task_file.write_text("Task body.", encoding="utf-8")
    builder = SystemPromptBuilder(language="en")
    rail = _make_rail(builder, str(task_file))

    _run(rail.before_invoke(SimpleNamespace()))
    assert builder.get_section(SectionName.TASK_DESCRIPTION) is not None

    rail.uninit(SimpleNamespace())
    assert builder.get_section(SectionName.TASK_DESCRIPTION) is None
    assert rail.system_prompt_builder is None
