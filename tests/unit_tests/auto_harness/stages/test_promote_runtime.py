# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Promote runtime function tests."""

from __future__ import annotations

from pathlib import Path

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
    ExtensionBuildArtifact,
    OptimizationTask,
)
from openjiuwen.auto_harness.stages.implement import (
    promote_runtime,
)


def _make_task_context(tmp_path: Path) -> TaskContext:
    orch = AutoHarnessOrchestrator(
        AutoHarnessConfig(data_dir=str(tmp_path)),
        agent=None,
    )
    runtime = TaskRuntime(
        related=[],
        wt_path=str(tmp_path / "wt"),
        edit_safety_rail=None,
        preexisting_dirty_files=[],
        task_agent=None,
        commit_agent=None,
    )
    return TaskContext(
        orchestrator=orch,
        task=OptimizationTask(topic="promote runtime"),
        runtime=runtime,
    )


@pytest.mark.asyncio
async def test_promote_runtime_copies_extension_tree(
    tmp_path: Path,
):
    ctx = _make_task_context(tmp_path)
    source_root = tmp_path / "source_ext"
    source_root.mkdir()
    (source_root / "__init__.py").write_text(
        "",
        encoding="utf-8",
    )
    (source_root / "harness_config.yaml").write_text(
        "schema_version: harness_config.v0.1\n"
        "name: source_ext\n",
        encoding="utf-8",
    )
    ctx.put_artifact(
        "extension_build",
        ExtensionBuildArtifact(
            extension_name="source_ext",
            extension_root=str(source_root),
            config_path=str(
                source_root / "harness_config.yaml"
            ),
        ),
    )

    result = await promote_runtime(ctx)

    assert result is not None
    assert Path(result.runtime_path).is_dir()
    assert Path(result.config_path).is_file()
