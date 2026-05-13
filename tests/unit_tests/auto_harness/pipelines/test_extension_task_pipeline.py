# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Extension task pipeline tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.auto_harness.contexts import (
    TaskContext,
    TaskRuntime,
    task_key,
)
from openjiuwen.auto_harness.orchestrator import (
    AutoHarnessOrchestrator,
)
from openjiuwen.auto_harness.pipelines.extended_evolve_pipeline.extension_task_pipeline import (
    ExtensionTaskPipeline,
    build_extension_task,
)
from openjiuwen.auto_harness.schema import (
    AutoHarnessConfig,
    ExtensionDesign,
    OptimizationTask,
    RuntimeExtensionArtifact,
)
from openjiuwen.auto_harness.stages.activate import (
    ExtendActivateStage,
)


async def _collect(agen):
    items = []
    async for item in agen:
        items.append(item)
    return items


def _design(name: str = "demo_ext") -> ExtensionDesign:
    module_base = (
        "openjiuwen.extensions.harness."
        f"{name}"
    )
    return ExtensionDesign(
        gap_id="gap_1",
        extension_name=name,
        components=["rail", "tool"],
        file_plan={
            "root": f"openjiuwen/extensions/harness/{name}",
            "manifest": (
                "openjiuwen/extensions/harness/"
                f"{name}/harness_config.yaml"
            ),
        },
        harness_config_patch={
            "resources": {
                "rails": [
                    {
                        "type": "package",
                        "module": (
                            f"{module_base}.rails.extension_rail"
                        ),
                        "class": "ExtensionRail",
                    }
                ],
                "tools": [
                    {
                        "type": "package",
                        "module": (
                            f"{module_base}.tools.extension_tool"
                        ),
                        "class": "ExtensionTool",
                    }
                ],
            }
        },
    )


def _write_extension_scaffold(
    wt_path: Path,
    name: str = "demo_ext",
) -> None:
    """Create the extension files that the agent would generate."""
    ext_root = (
        wt_path
        / "openjiuwen"
        / "extensions"
        / "harness"
        / name
    )
    rails_dir = ext_root / "rails"
    tools_dir = ext_root / "tools"
    for d in (ext_root, rails_dir, tools_dir):
        d.mkdir(parents=True, exist_ok=True)
        (d / "__init__.py").write_text(
            "", encoding="utf-8"
        )

    (rails_dir / "extension_rail.py").write_text(
        "from openjiuwen.harness.rails.base "
        "import DeepAgentRail\n\n\n"
        "class ExtensionRail(DeepAgentRail):\n"
        '    """Runtime-generated rail scaffold."""\n\n'
        "    pass\n",
        encoding="utf-8",
    )

    (tools_dir / "helper.py").write_text(
        f"EXTENSION_NAME = '{name}'\n",
        encoding="utf-8",
    )

    (tools_dir / "extension_tool.py").write_text(
        "from __future__ import annotations\n\n"
        "from typing import Any, AsyncIterator, Dict\n\n"
        "from .helper import EXTENSION_NAME\n"
        "from openjiuwen.core.foundation.tool "
        "import Tool, ToolCard\n\n\n"
        f"class ExtensionTool(Tool):\n"
        "    def __init__(self) -> None:\n"
        "        super().__init__(\n"
        "            ToolCard(\n"
        f"                id='{name}_tool',\n"
        f"                name='{name}_tool',\n"
        "                description=(\n"
        '                    "Runtime extension tool for "\n'
        "                    + EXTENSION_NAME\n"
        "                ),\n"
        "            )\n"
        "        )\n\n"
        "    async def invoke(\n"
        "        self,\n"
        "        inputs: Dict[str, Any],\n"
        "        **kwargs: Any,\n"
        "    ) -> Dict[str, Any]:\n"
        "        _ = kwargs\n"
        "        return {\n"
        '            "extension": EXTENSION_NAME,\n'
        '            "inputs": inputs,\n'
        "        }\n\n"
        "    async def stream(\n"
        "        self,\n"
        "        inputs: Dict[str, Any],\n"
        "        **kwargs: Any,\n"
        "    ) -> AsyncIterator[Dict[str, Any]]:\n"
        "        yield await self.invoke("
        "inputs, **kwargs)\n",
        encoding="utf-8",
    )

    manifest = ext_root / "harness_config.yaml"
    module_base = (
        f"openjiuwen.extensions.harness.{name}"
    )
    manifest.write_text(
        "schema_version: harness_config.v0.1\n"
        f"name: {name}\n"
        "resources:\n"
        "  rails:\n"
        "    - type: package\n"
        f"      module: {module_base}.rails.extension_rail\n"
        "      class: ExtensionRail\n"
        "  tools:\n"
        "    - type: package\n"
        f"      module: {module_base}.tools.extension_tool\n"
        "      class: ExtensionTool\n",
        encoding="utf-8",
    )


def _make_mock_agent(wt_path: Path, name: str):
    """Build a mock agent whose stream() writes scaffold files."""
    mock_agent = AsyncMock()

    async def _fake_stream(inputs, **kwargs):
        query = (inputs or {}).get("query", "")
        marker = "测试文件必须写入: "
        if marker in query:
            raw_path = query.split(marker, 1)[1].splitlines()[0]
            test_file = Path(raw_path.strip())
            test_file.parent.mkdir(parents=True, exist_ok=True)
            test_file.write_text(
                "def test_runtime_extension_acceptance():\n"
                "    assert True\n",
                encoding="utf-8",
            )
            return
        _write_extension_scaffold(wt_path, name)
        return
        yield  # noqa: unreachable — makes this an async generator

    mock_agent.stream = _fake_stream
    return mock_agent


@pytest.mark.asyncio
async def test_extension_task_pipeline_runs_end_to_end(
    tmp_path: Path,
):
    wt_path = tmp_path / "wt"
    wt_path.mkdir()
    orch = AutoHarnessOrchestrator(
        AutoHarnessConfig(data_dir=str(tmp_path)),
        agent=None,
    )
    orch.worktree_mgr.prepare = AsyncMock(
        return_value=str(wt_path)
    )
    orch.worktree_mgr.cleanup = AsyncMock()

    design = _design()
    mock_agent = _make_mock_agent(wt_path, "demo_ext")

    with patch(
        "openjiuwen.auto_harness.pipelines."
        "extended_evolve_pipeline."
        "extension_task_pipeline."
        "create_auto_harness_agent",
        return_value=mock_agent,
    ), patch(
        "openjiuwen.auto_harness.stages.verify."
        "_check_ruff",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "openjiuwen.auto_harness.stages.commit."
        "_collect_commit_facts",
        new_callable=AsyncMock,
    ) as mock_facts, patch(
        "openjiuwen.auto_harness.stages.commit."
        "_run_commit_round_stream",
    ) as mock_commit_round, patch(
        "openjiuwen.auto_harness.stages.publish_pr."
        "PublishPRStage.stream",
    ) as mock_publish:
        # CommitStage: simulate successful commit
        from openjiuwen.auto_harness.stages.commit import (
            CommitFacts,
            CommitRoundResult,
        )
        from openjiuwen.auto_harness.schema import (
            StageResult,
            VerifyReportArtifact,
        )

        mock_facts.return_value = CommitFacts(
            branch_name="auto-harness/demo",
            task_declared_files=[],
            preexisting_dirty_files=[],
            current_dirty_files=[],
            tracked_modified_files=[],
            untracked_files=[],
            edited_files=[],
            derived_test_files=[],
            legacy_related_test_files=[],
            verify_related_files=[],
            diff_stat="",
        )

        async def _fake_commit_round(**kwargs):
            yield CommitRoundResult(
                ok=True,
                reason="",
                status_text="",
                last_commit_stat="1 file changed",
            )

        mock_commit_round.side_effect = (
            _fake_commit_round
        )

        # PublishPRStage: simulate successful PR
        async def _fake_publish(self_or_ctx, *args):
            ctx_arg = (
                self_or_ctx
                if hasattr(self_or_ctx, "put_artifact")
                else args[0]
            )
            from openjiuwen.auto_harness.schema import (
                PullRequestArtifact,
            )

            ctx_arg.put_artifact(
                "pull_request",
                PullRequestArtifact(
                    pr_url="https://example.com/pr/1",
                    summary="test",
                ),
            )
            yield StageResult(
                artifacts={},
                messages=["PR created"],
            )

        mock_publish.side_effect = _fake_publish

        chunks = []
        async for item in ExtensionTaskPipeline.run_isolated_stream(
            orch,
            design,
        ):
            chunks.append(item)
            if getattr(item, "type", "") == "__interaction__":
                await _collect(
                    orch.run_session_stream(
                        message={
                            "interaction_id": item.payload[
                                "interaction_id"
                            ],
                            "action": "accept",
                        }
                    )
                )

    messages = [
        item.payload["content"]
        for item in chunks
        if getattr(item, "type", "") == "message"
    ]
    assert "Implemented extension: demo_ext" in messages
    assert (
        "Verified extension scaffold: demo_ext"
        in messages
    )
    assert orch.results[-1].success is True
    orch.worktree_mgr.cleanup.assert_awaited_once_with(
        str(wt_path)
    )


@pytest.mark.asyncio
async def test_extension_ready_payload_points_to_session_runtime_root(
    tmp_path: Path,
):
    orch = AutoHarnessOrchestrator(
        AutoHarnessConfig(data_dir=str(tmp_path)),
        agent=None,
    )
    session_root = orch.ensure_session_runtime_dir()
    for name in ("first_ext", "second_ext"):
        ext_dir = session_root / name
        ext_dir.mkdir(parents=True)
        (ext_dir / "harness_config.yaml").write_text(
            "schema_version: harness_config.v0.1\n"
            f"name: {name}\n",
            encoding="utf-8",
        )

    task = OptimizationTask(
        topic="runtime-extension:first_ext"
    )
    ctx = TaskContext(
        orchestrator=orch,
        task=task,
        runtime=TaskRuntime(
            related=[],
            wt_path=str(tmp_path / "wt"),
            edit_safety_rail=None,
            preexisting_dirty_files=[],
            task_agent=None,
            commit_agent=None,
        ),
    )
    ctx.put_artifact(
        "runtime_extension",
        RuntimeExtensionArtifact(
            extension_name="first_ext",
            runtime_path=str(session_root / "first_ext"),
            config_path=str(
                session_root
                / "first_ext"
                / "harness_config.yaml"
            ),
        ),
    )

    stream = ExtendActivateStage().stream(ctx)
    try:
        ready = await stream.__anext__()
    finally:
        await stream.aclose()

    assert ready.type == "extension_ready"
    assert ready.payload["runtime_path"] == str(session_root)
    assert ready.payload["session_runtime_path"] == str(
        session_root
    )
    assert ready.payload["extension_runtime_path"] == str(
        session_root / "first_ext"
    )
    assert [
        item["extension_name"]
        for item in ready.payload["runtime_extensions"]
    ] == ["first_ext", "second_ext"]
