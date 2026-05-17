# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Implement extension stage tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from openjiuwen.auto_harness.contexts import (
    TaskContext,
    TaskRuntime,
)
from openjiuwen.auto_harness.orchestrator import (
    AutoHarnessOrchestrator,
)
from openjiuwen.auto_harness.schema import (
    AutoHarnessConfig,
    ExtensionDesign,
    OptimizationTask,
    StageResult,
)
from openjiuwen.auto_harness.stages.implement import (
    ExtendImplementStage,
    _build_implement_ext_prompt,
)
from openjiuwen.core.session.stream.base import (
    OutputSchema,
)


async def _collect_last_result(stage, ctx):
    last = None
    async for item in stage.stream(ctx):
        if isinstance(item, StageResult):
            if item.artifacts:
                ctx.put_artifacts(item.artifacts)
            last = item
    return last


def _build_design() -> ExtensionDesign:
    return ExtensionDesign(
        gap_id="gap_1",
        extension_name="demo_ext",
        file_plan={
            "root": (
                "openjiuwen/extensions/harness/demo_ext"
            ),
            "manifest": (
                "openjiuwen/extensions/harness/demo_ext/"
                "harness_config.yaml"
            ),
        },
        harness_config_patch={
            "resources": {
                "rails": [
                    {
                        "type": "package",
                        "module": (
                            "openjiuwen.extensions."
                            "harness.demo_ext."
                            "rails.extension_rail"
                        ),
                        "class": "ExtensionRail",
                    }
                ],
                "tools": [
                    {
                        "type": "package",
                        "module": (
                            "openjiuwen.extensions."
                            "harness.demo_ext."
                            "tools.extension_tool"
                        ),
                        "class": "ExtensionTool",
                    }
                ],
            }
        },
    )


def test_implement_ext_prompt_respects_declared_components(
    tmp_path: Path,
):
    design = _build_design()
    design.components = ["rail"]

    prompt = _build_implement_ext_prompt(
        design,
        extension_root=tmp_path / "demo_ext",
        config_path=tmp_path / "demo_ext" / "harness_config.yaml",
    )

    assert "严格按 ExtensionDesign.components 实现组件" in prompt
    assert "实现 rail 组件" in prompt
    assert "实现 tool 组件" not in prompt
    assert "自动补充未声明" in prompt


def test_implement_ext_prompt_guides_tool_skill_ppt_extension(
    tmp_path: Path,
):
    design = _build_design()
    design.extension_name = "huawei_ppt_generator"
    design.components = ["tool", "skill"]
    design.file_plan = {
        "root": (
            "openjiuwen/extensions/harness/"
            "huawei_ppt_generator"
        ),
        "manifest": (
            "openjiuwen/extensions/harness/"
            "huawei_ppt_generator/harness_config.yaml"
        ),
    }

    prompt = _build_implement_ext_prompt(
        design,
        extension_root=(
            tmp_path / "huawei_ppt_generator"
        ),
        config_path=(
            tmp_path
            / "huawei_ppt_generator"
            / "harness_config.yaml"
        ),
    )

    assert "huawei_ppt_generator" in prompt
    assert "实现 tool 组件" in prompt
    assert "创建 skills/<skill_name>/SKILL.md" in prompt
    assert "实现 rail 组件" not in prompt
    assert "需求收集或结构化需求报告" in prompt
    assert "skill-creator" in prompt
    assert "assets/" in prompt
    assert "references/" in prompt
    assert "真实 .pptx 文件" in prompt
    assert "zipfile" in prompt
    assert "ppt/presentation.xml" in prompt
    assert "不得用 JSON、Markdown" in prompt
    assert "从 `harness_config.yaml` 中读取实际声明的 `module` 和 `class`" in prompt
    assert "openjiuwen.extensions.harness.<extension_name>." in prompt


def _write_scaffold(wt_path: Path) -> None:
    """Create extension files that the agent would generate."""
    ext = (
        wt_path
        / "openjiuwen"
        / "extensions"
        / "harness"
        / "demo_ext"
    )
    for d in (ext, ext / "rails", ext / "tools"):
        d.mkdir(parents=True, exist_ok=True)
        (d / "__init__.py").write_text(
            "", encoding="utf-8"
        )
    (ext / "rails" / "extension_rail.py").write_text(
        "class ExtensionRail: pass\n",
        encoding="utf-8",
    )
    (ext / "tools" / "extension_tool.py").write_text(
        "class ExtensionTool: pass\n",
        encoding="utf-8",
    )
    (ext / "tools" / "helper.py").write_text(
        "EXTENSION_NAME = 'demo_ext'\n",
        encoding="utf-8",
    )
    (ext / "harness_config.yaml").write_text(
        "schema_version: harness_config.v0.1\n"
        "name: demo_ext\n",
        encoding="utf-8",
    )


def _make_mock_agent(wt_path: Path):
    """Build a mock agent whose stream() writes scaffold."""
    mock_agent = AsyncMock()

    async def _fake_stream(inputs, **kwargs):
        _write_scaffold(wt_path)
        return
        yield

    mock_agent.stream = _fake_stream
    return mock_agent


def _make_task_context(
    tmp_path: Path,
    *,
    with_agent: bool = True,
) -> TaskContext:
    wt_path = tmp_path / "wt"
    wt_path.mkdir()
    orch = AutoHarnessOrchestrator(
        AutoHarnessConfig(data_dir=str(tmp_path)),
        agent=None,
    )
    agent = (
        _make_mock_agent(wt_path)
        if with_agent
        else None
    )
    runtime = TaskRuntime(
        related=[],
        wt_path=str(wt_path),
        edit_safety_rail=None,
        preexisting_dirty_files=[],
        task_agent=agent,
        commit_agent=None,
    )
    return TaskContext(
        orchestrator=orch,
        task=OptimizationTask(topic="implement ext"),
        runtime=runtime,
    )


@pytest.mark.asyncio
async def test_implement_ext_writes_extension_scaffold(
    tmp_path: Path,
):
    ctx = _make_task_context(tmp_path, with_agent=True)
    ctx.put_artifact(
        "extension_target",
        _build_design(),
    )

    result = await _collect_last_result(
        ExtendImplementStage(),
        ctx,
    )

    assert result is not None
    assert not result.error
    build = ctx.require_artifact("extension_build")
    root = Path(build.extension_root)
    assert root.is_dir()
    assert Path(build.config_path).is_file()


@pytest.mark.asyncio
async def test_implement_ext_fails_without_agent(
    tmp_path: Path,
):
    ctx = _make_task_context(
        tmp_path, with_agent=False
    )
    ctx.put_artifact(
        "extension_target",
        _build_design(),
    )

    result = await _collect_last_result(
        ExtendImplementStage(),
        ctx,
    )

    assert result is not None
    assert result.error is not None
    assert "No task_agent" in result.error


@pytest.mark.asyncio
async def test_implement_ext_scopes_nested_agent_stage_events(
    tmp_path: Path,
):
    wt_path = tmp_path / "wt"
    wt_path.mkdir()

    class FakeAgent:
        async def stream(self, inputs, **kwargs):
            _ = inputs, kwargs
            _write_scaffold(wt_path)
            yield OutputSchema(
                type="stage_result",
                index=0,
                payload={
                    "stage": "implement",
                    "status": "success",
                },
            )

    orch = AutoHarnessOrchestrator(
        AutoHarnessConfig(data_dir=str(tmp_path)),
        agent=None,
    )
    ctx = TaskContext(
        orchestrator=orch,
        task=OptimizationTask(topic="implement ext"),
        runtime=TaskRuntime(
            related=[],
            wt_path=str(wt_path),
            edit_safety_rail=None,
            preexisting_dirty_files=[],
            task_agent=FakeAgent(),
            commit_agent=None,
        ),
    )
    ctx.put_artifact(
        "extension_target",
        _build_design(),
    )

    chunks = [
        item
        async for item in ExtendImplementStage().stream(ctx)
    ]

    scoped = next(
        item
        for item in chunks
        if isinstance(item, OutputSchema)
        and item.type == "stage_result"
    )
    assert scoped.payload["stage"] == "implement_ext"
