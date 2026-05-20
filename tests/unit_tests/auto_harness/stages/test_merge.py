# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for stages/merge.py: MergeActivationBlock."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from openjiuwen.auto_harness.infra.runtime_extension_merger import (
    MergedExtensionError,
    MergeRuntimeExtensionsResult,
)
from openjiuwen.auto_harness.schema import (
    RuntimeExtensionArtifact,
)
from openjiuwen.auto_harness.stages.merge import (
    MergeActivationBlock,
    _build_merge_fix_prompt,
    _merge_event,
)
from openjiuwen.auto_harness.infra.runtime_extension_static_checks import (
    ExtStaticCheckResult,
)
from openjiuwen.core.session.stream.base import (
    OutputSchema,
)


def _make_artifact(
    name: str = "merged_extensions",
    runtime_path: str = "/fake/runtime",
    config_path: str = "/fake/config",
) -> RuntimeExtensionArtifact:
    return RuntimeExtensionArtifact(
        extension_name=name,
        runtime_path=runtime_path,
        config_path=config_path,
    )


def _make_merge_result(
    runtime_path: str = "/fake/runtime",
    config_path: str = "/fake/config",
) -> MergeRuntimeExtensionsResult:
    art = _make_artifact(
        runtime_path=runtime_path, config_path=config_path
    )
    return MergeRuntimeExtensionsResult(
        runtime_ext=art,
        rename_map={},
        skill_rename_map={},
        source_exts_summary=[
            {"name": "ext_a"},
            {"name": "ext_b"},
        ],
    )


class MockVerifiedTask:
    """Minimal mock for VerifiedExtensionTask."""

    def __init__(self) -> None:
        self.ctx = mock.MagicMock()
        self.ctx.require_artifact.return_value = _make_artifact()
        self.design = mock.MagicMock()
        self.design.extension_name = "test"
        self.task = mock.MagicMock()


class MockOrchestrator:
    """Minimal mock for AutoHarnessOrchestrator."""

    def __init__(self) -> None:
        self.config = mock.MagicMock()
        self.runtime = mock.MagicMock()
        self.runtime.session_id = "sess123"
        self.stream_rails = None
        self._session_runtime_dir = Path("/fake/session")
        self._recorded: list = []

    def ensure_session_runtime_dir(self) -> Path:
        return self._session_runtime_dir

    def record_cycle_result(self, result: object) -> None:
        self._recorded.append(result)


class TestMergeEvent:
    """Tests for _merge_event output schema."""

    def test_running_event(self):
        event = _merge_event("running", repair_rounds=0)
        assert event.type == "stage_result"
        assert event.payload["extension_stage"] == "merge_ext"
        assert event.payload["extension_name"] == "merged_extensions"
        assert event.payload["status"] == "running"
        assert event.payload["repair_rounds"] == 0

    def test_failed_event(self):
        event = _merge_event(
            "failed", error="boom", repair_rounds=3
        )
        assert event.payload["status"] == "failed"
        assert event.payload["error"] == "boom"
        assert event.payload["repair_rounds"] == 3

    def test_success_event(self):
        event = _merge_event("success")
        assert event.payload["status"] == "success"


class TestBuildMergeFixPrompt:
    """Tests for _build_merge_fix_prompt."""

    def test_prompt_contains_merge_summary(self):
        result = _make_merge_result()
        result.rename_map = {
            ("ext_a", "tools/helper.py"): "tools/helper__ext_a.py"
        }
        prompt = _build_merge_fix_prompt(
            merged=_make_artifact(),
            merge_result=result,
            static_errors=["ruff error"],
            attempt=1,
            max_attempts=3,
        )
        assert "helper__ext_a" in prompt
        assert "merged_extensions" in prompt

    def test_prompt_contains_errors(self):
        result = _make_merge_result()
        prompt = _build_merge_fix_prompt(
            merged=_make_artifact(),
            merge_result=result,
            static_errors=["error A", "error B"],
            attempt=2,
            max_attempts=3,
        )
        assert "error A" in prompt
        assert "error B" in prompt
        assert "2/3" in prompt


class TestMergeActivationBlock:
    """Tests for MergeActivationBlock.stream()."""

    @pytest.mark.asyncio
    async def test_static_pass_no_agent_created(self):
        """Merge succeeds on first check — no merge_agent created."""
        orchestrator = MockOrchestrator()
        task = MockVerifiedTask()

        block = MergeActivationBlock()

        with (
            mock.patch(
                "openjiuwen.auto_harness.stages.merge."
                "merge_runtime_extensions",
                return_value=_make_merge_result(),
            ),
            mock.patch(
                "openjiuwen.auto_harness.stages.merge."
                "run_static_checks_against_runtime",
                return_value=ExtStaticCheckResult(
                    errors=[], rails_count=1, tools_count=1
                ),
            ),
        ):
            events = []
            async for chunk in block.stream(
                orchestrator, [task]
            ):
                events.append(chunk)

        # Verify event sequence: running -> success
        statuses = [
            e.payload["status"]
            for e in events
            if isinstance(e, OutputSchema)
        ]
        assert "running" in statuses
        assert "success" in statuses
        # MergeSuccessResult was yielded
        from openjiuwen.auto_harness.stages.merge import MergeSuccessResult
        assert any(isinstance(e, MergeSuccessResult) for e in events)

    @pytest.mark.asyncio
    async def test_one_round_repair(self):
        """First check fails, second passes — agent created once."""
        orchestrator = MockOrchestrator()
        task = MockVerifiedTask()

        call_count = {"static": 0, "agent": 0}

        async def fake_static(*args, **kwargs):
            call_count["static"] += 1
            if call_count["static"] == 1:
                return ExtStaticCheckResult(
                    errors=["import error"]
                )
            return ExtStaticCheckResult(
                errors=[], rails_count=1
            )

        async def fake_stream(*args, **kwargs):
            call_count["agent"] += 1
            if False:
                yield None

        mock_agent = mock.MagicMock()
        mock_agent.stream = fake_stream

        block = MergeActivationBlock()

        with (
            mock.patch(
                "openjiuwen.auto_harness.stages.merge."
                "merge_runtime_extensions",
                return_value=_make_merge_result(),
            ),
            mock.patch(
                "openjiuwen.auto_harness.stages.merge."
                "run_static_checks_against_runtime",
                side_effect=fake_static,
            ),
            mock.patch(
                "openjiuwen.auto_harness.agents.factory."
                "create_merge_ext_agent",
                return_value=mock_agent,
            ),
            mock.patch(
                "openjiuwen.auto_harness.stages.merge."
                "_stream_merge_agent_turn",
                side_effect=fake_stream,
            ),
        ):
            events = []
            async for chunk in block.stream(
                orchestrator, [task]
            ):
                events.append(chunk)

        assert call_count["static"] == 2
        assert call_count["agent"] == 1
        statuses = [
            e.payload["status"]
            for e in events
            if isinstance(e, OutputSchema)
        ]
        assert "success" in statuses

    @pytest.mark.asyncio
    async def test_exhausted_rounds(self):
        """Static checks always fail — raises after max attempts."""
        orchestrator = MockOrchestrator()
        task = MockVerifiedTask()

        agent_call_count = {"create": 0, "stream": 0}

        async def fake_static(*args, **kwargs):
            return ExtStaticCheckResult(
                errors=["always fails"]
            )

        async def fake_stream(*args, **kwargs):
            agent_call_count["stream"] += 1
            if False:
                yield None

        mock_agent = mock.MagicMock()
        mock_agent.stream = fake_stream

        def create_agent_mock(*args, **kwargs):
            agent_call_count["create"] += 1
            return mock_agent

        block = MergeActivationBlock()

        with (
            mock.patch(
                "openjiuwen.auto_harness.stages.merge."
                "merge_runtime_extensions",
                return_value=_make_merge_result(),
            ),
            mock.patch(
                "openjiuwen.auto_harness.stages.merge."
                "run_static_checks_against_runtime",
                side_effect=fake_static,
            ),
            mock.patch(
                "openjiuwen.auto_harness.agents.factory."
                "create_merge_ext_agent",
                side_effect=create_agent_mock,
            ),
            mock.patch(
                "openjiuwen.auto_harness.stages.merge."
                "_stream_merge_agent_turn",
                side_effect=fake_stream,
            ),
        ):
            with pytest.raises(MergedExtensionError):
                async for _ in block.stream(
                    orchestrator, [task]
                ):
                    pass

        # create_merge_ext_agent should be called exactly once
        assert agent_call_count["create"] == 1
        # stream should be called max_attempts times
        assert agent_call_count["stream"] == 3

    @pytest.mark.asyncio
    async def test_merge_hard_error_fails_fast(self):
        """merge_runtime_extensions raises — fail immediately."""
        orchestrator = MockOrchestrator()
        task = MockVerifiedTask()

        block = MergeActivationBlock()

        with mock.patch(
            "openjiuwen.auto_harness.stages.merge."
            "merge_runtime_extensions",
            side_effect=MergedExtensionError("bad manifest"),
        ):
            events = []
            with pytest.raises(MergedExtensionError):
                async for chunk in block.stream(
                    orchestrator, [task]
                ):
                    events.append(chunk)
            # Should have emitted a 'failed' event before raising
            assert any(
                isinstance(e, OutputSchema)
                and e.payload["status"] == "failed"
                for e in events
            )


class TestSchemaGate:
    """Verify OutputSchema payload fields for merge events."""

    def test_payload_has_required_fields(self):
        event = _merge_event(
            "failed", error="test", repair_rounds=2
        )
        payload = event.payload
        assert payload["extension_stage"] == "merge_ext"
        assert payload["extension_name"] == "merged_extensions"
        assert "status" in payload
        assert "repair_rounds" in payload
        assert "error" in payload
