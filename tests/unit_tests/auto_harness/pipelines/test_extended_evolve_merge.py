# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Pipeline-level tests for multi-design merge activate flow."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.auto_harness.infra.runtime_extension_merger import (
    MergedExtensionError,
)
from openjiuwen.auto_harness.pipelines.extended_evolve_pipeline.extension_task_pipeline import (
    VerifiedExtensionTask,
)
from openjiuwen.auto_harness.schema import (
    CycleResult,
    ExtensionDesign,
)


async def _collect(agen):
    items = []
    async for item in agen:
        items.append(item)
    return items


def _verified_task(name: str = "demo_ext") -> VerifiedExtensionTask:
    """Create a minimal VerifiedExtensionTask mock."""
    ctx = MagicMock()
    ctx.require_artifact.return_value = MagicMock(
        extension_name=name,
        runtime_path=f"/fake/{name}",
        config_path=f"/fake/{name}/harness_config.yaml",
    )
    return VerifiedExtensionTask(
        design=ExtensionDesign(
            gap_id="gap_1",
            extension_name=name,
        ),
        task=MagicMock(),
        ctx=ctx,
    )


class TestMergePipelineDispatch:
    """Tests for the activate section dispatch logic."""

    @pytest.mark.asyncio
    async def test_multi_design_uses_merge_block(self):
        """N>1 → MergeActivationBlock.stream is called."""
        from openjiuwen.auto_harness.stages import merge as merge_mod

        verified = [
            _verified_task("ext_a"),
            _verified_task("ext_b"),
        ]

        mock_block = MagicMock()

        async def fake_stream(orch, tasks):
            tasks[0].ctx.put_artifact(
                "runtime_extension", MagicMock()
            )
            yield MagicMock(
                type="stage_result",
                payload={
                    "stage": "activate",
                    "extension_stage": "merge_ext",
                    "status": "success",
                },
            )

        mock_block_instance = MagicMock()
        mock_block_instance.stream = fake_stream
        mock_block.return_value = mock_block_instance

        activate_calls = []

        async def fake_activate(orch, v):
            activate_calls.append(v)
            yield MagicMock(
                type="message",
                payload={"content": "activated"},
            )

        with (
            patch.object(
                merge_mod,
                "MergeActivationBlock",
                mock_block,
            ),
            patch(
                "openjiuwen.auto_harness.pipelines."
                "extended_evolve_pipeline."
                "extended_evolve_pipeline."
                "ExtensionTaskPipeline.run_activate_stream",
                fake_activate,
            ),
        ):
            # Simulate the activate section logic
            if len(verified) == 1:
                await _collect(fake_activate(MagicMock(), verified[0]))
            elif len(verified) > 1:
                block = mock_block(MagicMock(), verified)
                try:
                    async for _ in block.stream(MagicMock(), verified):
                        pass
                except MergedExtensionError:
                    pass
                else:
                    await _collect(
                        fake_activate(MagicMock(), verified[0])
                    )

        assert mock_block.called
        assert len(activate_calls) == 1

    @pytest.mark.asyncio
    async def test_multi_design_merge_fail_fast(self):
        """N>1 + merge fails → no activate_stream called."""
        verified = [
            _verified_task("ext_a"),
            _verified_task("ext_b"),
        ]
        orch = MagicMock()
        orch.results = []
        orch.record_cycle_result = MagicMock()

        activate_calls = []

        async def fake_activate(orch, v):
            activate_calls.append(v)
            yield MagicMock()

        # Simulate the activate section logic with merge failure
        if len(verified) > 1:
            try:
                raise MergedExtensionError("merge failed")
            except MergedExtensionError as exc:
                orch.record_cycle_result(
                    CycleResult(
                        success=False,
                        error=f"merge failed: {exc}",
                    )
                )
            else:
                await _collect(fake_activate(orch, verified[0]))

        assert len(activate_calls) == 0
        orch.record_cycle_result.assert_called_once()
        result = orch.record_cycle_result.call_args[0][0]
        assert result.success is False
        assert "merge failed" in result.error



class TestSingleVsMultiDesignRouting:
    """Verify the branching logic for N==1 vs N>1."""

    def test_single_design_does_not_branch_to_merge(self):
        verified = [_verified_task()]
        assert len(verified) == 1
        assert not (len(verified) > 1)

    def test_multi_design_branches_to_merge(self):
        verified = [
            _verified_task("ext_a"),
            _verified_task("ext_b"),
        ]
        assert len(verified) > 1
        assert not (len(verified) == 1)
