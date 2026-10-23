# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""RepeatToolCallDetector: schema-whitelisted args hash + user-turn history."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.core.single_agent.interrupt.exception import ToolInterruptException
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.harness.agent_ras.config import RepeatToolConfig
from openjiuwen.harness.agent_ras.detectors.repeat_tool import (
    RepeatToolCallDetector,
    ToolCallRecord,
    ToolResultErrorDetector,
    _INTERRUPTION_KEY,
    _SESSION_HISTORIES_KEY,
)
from openjiuwen.harness.agent_ras.models import (
    AnomalyKind,
    Severity,
    Signal,
    SignalInterruptKind,
    SignalKind,
)
from openjiuwen.harness.agent_ras.monitor import AgentRASMonitor
from openjiuwen.harness.agent_ras.recovery.engine import RecoveryPolicy
from openjiuwen.harness.agent_ras.signal_builder import (
    build_after_tool_call_signal,
    build_tool_exception_signal,
)


def _after_tool(
    tool_name: str,
    tool_args: dict,
    *,
    tool_result: dict | None = None,
    member_name: str = "main",
) -> Signal:
    return Signal(
        kind=SignalKind.AFTER_TOOL_CALL,
        member_name=member_name,
        tool_name=tool_name,
        tool_args=tool_args,
        tool_result=tool_result if tool_result is not None else {"content": "ok"},
    )


def _tool_exc(
    tool_name: str,
    tool_args: dict | None = None,
    *,
    error: str = "interrupt",
    member_name: str = "main",
) -> Signal:
    return Signal(
        kind=SignalKind.TOOL_EXCEPTION,
        member_name=member_name,
        tool_name=tool_name,
        tool_args=tool_args,
        error=error,
    )


def _mock_session(*, pending_interrupt: bool = False) -> MagicMock:
    store: dict = {}
    if pending_interrupt:
        # Sentinel: non-None under interruption key (typed check falls open).
        store[_INTERRUPTION_KEY] = object()

    session = MagicMock()

    def get_state(key, default=None):
        return store.get(key, default)

    def update_state(mapping):
        store.update(mapping)

    session.get_state.side_effect = get_state
    session.update_state.side_effect = update_state
    session._store = store
    return session


def _file_path_card() -> SimpleNamespace:
    return SimpleNamespace(
        input_params={
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
            },
        },
    )


class TestSchemaArgHash:
    def test_hash_whitelist_ignores_call_goal(self):
        det = RepeatToolCallDetector()
        with patch.object(det, "_schema_keys", return_value=("path",)):
            h1 = det._hash_args(
                "read_file",
                {"path": "/a.txt", "call_goal": "第1次读取"},
            )
            h2 = det._hash_args(
                "read_file",
                {"path": "/a.txt", "call_goal": "第2次读取"},
            )
        assert h1 == h2

    def test_hash_without_schema_uses_full_args(self):
        det = RepeatToolCallDetector()
        with patch.object(det, "_schema_keys", return_value=None):
            h1 = det._hash_args(
                "read_file",
                {"path": "/a.txt", "call_goal": "第1次读取"},
            )
            h2 = det._hash_args(
                "read_file",
                {"path": "/a.txt", "call_goal": "第2次读取"},
            )
        assert h1 != h2

    def test_hash_with_schema_and_invalid_args_uses_full_args(self):
        det = RepeatToolCallDetector()
        with patch.object(det, "_schema_keys", return_value=("search", "fetch")):
            h1 = det._hash_args(
                "web_flash",
                {"query": "大纲研究一", "max_results": 8},
            )
            h2 = det._hash_args(
                "web_flash",
                {"query": "大纲研究二", "max_results": 8},
            )
        assert h1 != h2

    def test_hash_with_schema_and_same_invalid_args_is_stable(self):
        det = RepeatToolCallDetector()
        args = {"query": "大纲研究一", "max_results": 8}
        with patch.object(det, "_schema_keys", return_value=("search", "fetch")):
            h1 = det._hash_args("web_flash", args)
            h2 = det._hash_args("web_flash", dict(args))
        assert h1 == h2

    def test_hash_with_schema_and_partial_valid_args_keeps_whitelist(self):
        det = RepeatToolCallDetector()
        with patch.object(det, "_schema_keys", return_value=("path",)):
            h1 = det._hash_args(
                "read_file",
                {"path": "/a.txt", "call_goal": "第1次读取", "unknown": "one"},
            )
            h2 = det._hash_args(
                "read_file",
                {"path": "/a.txt", "call_goal": "第2次读取", "unknown": "two"},
            )
        assert h1 == h2

    def test_keys_from_parameters_dict(self):
        keys = RepeatToolCallDetector._keys_from_parameters(
            {"type": "object", "properties": {"path": {"type": "string"}}},
        )
        assert keys == ("path",)

    def test_keys_from_parameters_unknown(self):
        assert RepeatToolCallDetector._keys_from_parameters(None) is None
        assert RepeatToolCallDetector._keys_from_parameters({"type": "object"}) is None

    def test_ability_manager_schema_ignores_call_goal(self):
        am = MagicMock()
        am.get.return_value = _file_path_card()
        det = RepeatToolCallDetector()
        det.set_ability_manager(am)
        with patch(
            "openjiuwen.core.runner.Runner.resource_mgr.get_tool",
            return_value=None,
        ):
            h1 = det._hash_args(
                "read_file",
                {"file_path": "/a.txt", "call_goal": "第1次"},
            )
            h2 = det._hash_args(
                "read_file",
                {"file_path": "/a.txt", "call_goal": "第2次"},
            )
        assert h1 == h2
        am.get.assert_called_with("read_file")
        assert det._schema_key_cache.get("read_file") == ("file_path",)


class TestIgnoredToolCallRules:
    """Rules match the outer tool and selected top-level argument values."""

    @pytest.mark.asyncio
    async def test_multiple_tools_skip_matching_successes_and_exceptions(self):
        config = RepeatToolConfig.model_validate({
            "warning_threshold": 2,
            "ignored_tool_calls": [
                {
                    "tool_name": "invoke_tool",
                    "argument_equals": {"tool_name": "image_get_run"},
                },
                {
                    "tool_name": "poll_job",
                    "argument_equals": {"operation": "status"},
                },
            ],
        })
        detector = RepeatToolCallDetector(config)

        for outer_name, args in (
            ("invoke_tool", {"tool_name": "image_get_run", "arguments": {}}),
            ("poll_job", {"operation": "status", "job_id": "image-1"}),
        ):
            for _ in range(12):
                assert await detector.observe(
                    _after_tool(outer_name, args, tool_result={"status": "running"})
                ) is None
                assert await detector.observe(
                    _tool_exc(outer_name, args, error="temporary timeout")
                ) is None

        assert detector._histories == {}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("outer_name", "args"),
        [
            ("invoke_tool", {"tool_name": "image_create_run", "arguments": {}}),
            ("invoke_tool", {"arguments": {"tool_name": "image_get_run"}}),
            ("image_get_run", {"run_id": "image-1"}),
            ("poll_job", {"operation": "cancel", "job_id": "image-1"}),
        ],
    )
    async def test_nonmatching_calls_still_warn(self, outer_name, args):
        detector = RepeatToolCallDetector(
            RepeatToolConfig.model_validate({
                "warning_threshold": 2,
                "ignored_tool_calls": [
                    {
                        "tool_name": "invoke_tool",
                        "argument_equals": {"tool_name": "image_get_run"},
                    },
                    {
                        "tool_name": "poll_job",
                        "argument_equals": {"operation": "status"},
                    },
                ],
            })
        )

        assert await detector.observe(_after_tool(outer_name, args)) is None
        anomaly = await detector.observe(_after_tool(outer_name, args))

        assert anomaly is not None
        assert anomaly.evidence["detector_kind"] == "generic_repeat"

    @pytest.mark.asyncio
    async def test_unconfigured_call_still_warns(self):
        detector = RepeatToolCallDetector(RepeatToolConfig(warning_threshold=2))
        args = {"tool_name": "image_get_run", "arguments": {"run_id": "image-1"}}

        assert await detector.observe(_after_tool("invoke_tool", args)) is None
        assert await detector.observe(_after_tool("invoke_tool", args)) is not None

    def test_exception_builder_preserves_target_tool_name(self):
        inputs = SimpleNamespace(
            tool_name="invoke_tool",
            tool_args='{"tool_name":"image_get_run","arguments":{"run_id":"image-1"}}',
            tool_msg=None,
        )

        signal = build_tool_exception_signal("main", inputs, RuntimeError("timeout"))

        assert signal.tool_args == {
            "tool_name": "image_get_run",
            "arguments": {"run_id": "image-1"},
        }


class TestRepeatWithinInvoke:
    @pytest.mark.asyncio
    async def test_generic_repeat_with_varying_call_goal(self):
        det = RepeatToolCallDetector(
            RepeatToolConfig(warning_threshold=3, global_breaker_threshold=100),
        )
        with patch.object(det, "_schema_keys", return_value=("path",)):
            anomaly = None
            for i in range(3):
                anomaly = await det.observe(
                    _after_tool(
                        "read_file",
                        {"path": "/a.txt", "call_goal": f"第{i + 1}次"},
                    )
                )
        assert anomaly is not None
        assert anomaly.kind == AnomalyKind.REPEAT_TOOL_CALL
        assert anomaly.evidence.get("detector_kind") == "generic_repeat"
        assert anomaly.evidence.get("tool_name") == "read_file"
        assert anomaly.member_name == "main"
        assert anomaly.severity == Severity.LOW

    @pytest.mark.asyncio
    async def test_generic_repeat_via_ability_manager(self):
        am = MagicMock()
        am.get.return_value = _file_path_card()
        det = RepeatToolCallDetector(
            RepeatToolConfig(warning_threshold=3, global_breaker_threshold=100),
        )
        det.set_ability_manager(am)
        with patch(
            "openjiuwen.core.runner.Runner.resource_mgr.get_tool",
            return_value=None,
        ):
            anomaly = None
            for i in range(3):
                anomaly = await det.observe(
                    _after_tool(
                        "read_file",
                        {
                            "file_path": "/a.txt",
                            "call_goal": f"第{i + 1}次读取",
                        },
                    )
                )
        assert anomaly is not None
        assert anomaly.evidence.get("detector_kind") == "generic_repeat"
        assert det._schema_key_cache.get("read_file") == ("file_path",)

    @pytest.mark.asyncio
    async def test_reset_clears_streak(self):
        det = RepeatToolCallDetector(
            RepeatToolConfig(warning_threshold=3, global_breaker_threshold=100),
        )
        with patch.object(det, "_schema_keys", return_value=("path",)):
            for i in range(2):
                await det.observe(
                    _after_tool(
                        "read_file",
                        {"path": "/a.txt", "call_goal": f"n{i}"},
                    )
                )
            det.reset()
            anomaly = await det.observe(
                _after_tool(
                    "read_file",
                    {"path": "/a.txt", "call_goal": "after-reset"},
                )
            )
        assert anomaly is None

    @pytest.mark.asyncio
    async def test_generic_repeat_window_sum_with_intervening_tools(self):
        """Non-contiguous: intervening todo_list / bash do not break the count."""
        det = RepeatToolCallDetector(
            RepeatToolConfig(warning_threshold=5, global_breaker_threshold=100),
        )
        with patch.object(det, "_schema_keys", return_value=("path",)):
            anomaly = None
            for i in range(5):
                anomaly = await det.observe(
                    _after_tool(
                        "read_file",
                        {"path": "/a.txt", "call_goal": f"n{i}"},
                    )
                )
                if i < 4:
                    # 夹杂异类工具：累加不被中断
                    intrude = await det.observe(
                        _after_tool(
                            "todo_list",
                            {"dummy": "x"},
                        )
                    )
                    if i == 0:
                        # 第 1 次夹杂时 read_file 计数 1 < threshold
                        assert intrude is None
        assert anomaly is not None
        assert anomaly.kind == AnomalyKind.REPEAT_TOOL_CALL
        assert anomaly.evidence.get("detector_kind") == "generic_repeat"
        assert anomaly.evidence.get("count") == 5

    @pytest.mark.asyncio
    async def test_generic_repeat_contiguous_sum_unchanged(self):
        """Pure contiguous 5 still triggers (legacy contiguous path retained)."""
        det = RepeatToolCallDetector(
            RepeatToolConfig(warning_threshold=5, global_breaker_threshold=100),
        )
        with patch.object(det, "_schema_keys", return_value=("path",)):
            anomaly = None
            for i in range(5):
                anomaly = await det.observe(
                    _after_tool(
                        "read_file",
                        {"path": "/a.txt", "call_goal": f"n{i}"},
                    )
                )
        assert anomaly is not None
        assert anomaly.evidence.get("detector_kind") == "generic_repeat"


class TestMonitorBindsAbilityManager:
    def test_bind_ctx_injects_ability_manager(self):
        det = RepeatToolCallDetector()
        am = MagicMock(name="ability_manager")
        agent = SimpleNamespace(ability_manager=am)
        ctx = SimpleNamespace(agent=agent, session=None, extra={})
        monitor = AgentRASMonitor(
            detectors=[det],
            reporter=None,
            policy=RecoveryPolicy(),
        )
        monitor.bind_ctx(ctx)
        assert det._ability_manager is am
        monitor.bind_ctx(None)
        assert det._ability_manager is None


class TestGlobalBreakerNonContiguous:
    """global_circuit_breaker counts within the window, skipping unrelated rows."""

    @pytest.mark.asyncio
    async def test_global_breaker_non_contiguous(self):
        cfg = RepeatToolConfig(
            warning_threshold=100,
            critical_threshold=100,
            global_breaker_threshold=3,
            unknown_tool_threshold=100,
        )
        det = RepeatToolCallDetector(cfg)
        with patch.object(det, "_schema_keys", return_value=("file_path",)):
            anomaly = None
            for _ in range(3):
                # Same call+result, but each cycle is split by an unrelated
                # empty-args tool_exception (the ask-path pseudo record).
                anomaly = await det.observe(
                    _after_tool(
                        "read_file",
                        {"file_path": "/a.txt"},
                        tool_result={"content": "test"},
                    )
                )
                if anomaly is not None:
                    break
                anomaly = await det.observe(
                    _tool_exc("read_file", None, error="permission ask"),
                )
                if anomaly is not None:
                    break
        assert anomaly is not None
        assert anomaly.kind == AnomalyKind.TOOL_CALL_LOOP
        assert anomaly.severity == Severity.CRITICAL
        assert anomaly.evidence.get("detector_kind") == "global_circuit_breaker"
        assert anomaly.evidence.get("count") == 3

    @pytest.mark.asyncio
    async def test_global_breaker_stops_on_progress(self):
        cfg = RepeatToolConfig(
            warning_threshold=100,
            critical_threshold=100,
            global_breaker_threshold=3,
            unknown_tool_threshold=100,
        )
        det = RepeatToolCallDetector(cfg)
        with patch.object(det, "_schema_keys", return_value=("file_path",)):
            anomaly = None
            for i in range(4):
                anomaly = await det.observe(
                    _after_tool(
                        "read_file",
                        {"file_path": "/a.txt"},
                        tool_result={"content": f"changed {i}"},
                    )
                )
                if anomaly is not None:
                    break
        assert anomaly is None

    @pytest.mark.asyncio
    async def test_global_breaker_skips_none_result(self):
        cfg = RepeatToolConfig(
            warning_threshold=100,
            critical_threshold=100,
            global_breaker_threshold=3,
            unknown_tool_threshold=100,
        )
        det = RepeatToolCallDetector(cfg)
        with patch.object(det, "_schema_keys", return_value=("file_path",)):
            anomaly = None
            for _ in range(3):
                anomaly = await det.observe(
                    _after_tool(
                        "read_file",
                        {"file_path": "/a.txt"},
                        tool_result={"content": "test"},
                    )
                )
                if anomaly is not None:
                    break
                # tool_exception carries result_hash=None -> skipped, not a break
                anomaly = await det.observe(
                    _tool_exc("todo_list", {"x": 1}, error="boom"),
                )
                if anomaly is not None:
                    break
        assert anomaly is not None
        assert anomaly.evidence.get("detector_kind") == "global_circuit_breaker"


class TestUserTurnScopeAcrossHitl:
    @pytest.mark.asyncio
    async def test_ask_resume_preserves_streak_across_detectors(self):
        session = _mock_session(pending_interrupt=True)
        cfg = RepeatToolConfig(warning_threshold=3, global_breaker_threshold=100)
        with patch.object(
            RepeatToolCallDetector,
            "_schema_keys",
            return_value=("path",),
        ):
            first = RepeatToolCallDetector(cfg)
            first.reset(session)
            for i in range(2):
                await first.observe(
                    _after_tool(
                        "read_file",
                        {"path": "/a.txt", "call_goal": f"a{i}"},
                    )
                )
            # Physical invoke end after ask: keep stash.
            first.reset(session)
            assert session._store.get(_SESSION_HISTORIES_KEY) is not None

            second = RepeatToolCallDetector(cfg)
            second.reset(session)  # resume start: hydrate
            anomaly = await second.observe(
                _after_tool(
                    "read_file",
                    {"path": "/a.txt", "call_goal": "a2"},
                )
            )
        assert anomaly is not None
        assert anomaly.evidence.get("detector_kind") == "generic_repeat"

    @pytest.mark.asyncio
    async def test_new_user_turn_clears_stash(self):
        session = _mock_session(pending_interrupt=False)
        cfg = RepeatToolConfig(warning_threshold=3, global_breaker_threshold=100)
        with patch.object(
            RepeatToolCallDetector,
            "_schema_keys",
            return_value=("path",),
        ):
            first = RepeatToolCallDetector(cfg)
            first.reset(session)
            for i in range(2):
                await first.observe(
                    _after_tool(
                        "read_file",
                        {"path": "/a.txt", "call_goal": f"b{i}"},
                    )
                )
            first.reset(session)  # user-turn boundary
            assert session._store.get(_SESSION_HISTORIES_KEY) is None

            second = RepeatToolCallDetector(cfg)
            second.reset(session)
            anomaly = await second.observe(
                _after_tool(
                    "read_file",
                    {"path": "/a.txt", "call_goal": "b2"},
                )
            )
        assert anomaly is None

    @pytest.mark.asyncio
    async def test_resume_continuation_preserves_stash(self):
        session = _mock_session(pending_interrupt=False)
        cfg = RepeatToolConfig(warning_threshold=3, global_breaker_threshold=100)
        with patch.object(
            RepeatToolCallDetector,
            "_schema_keys",
            return_value=("path",),
        ):
            first = RepeatToolCallDetector(cfg)
            first.reset(session)
            for i in range(2):
                await first.observe(
                    _after_tool(
                        "read_file",
                        {"path": "/a.txt", "call_goal": f"c{i}"},
                    )
                )
            first.reset(session, resume_continuation=True)
            assert session._store.get(_SESSION_HISTORIES_KEY) is not None

            second = RepeatToolCallDetector(cfg)
            second.reset(session, resume_continuation=True)
            anomaly = await second.observe(
                _after_tool(
                    "read_file",
                    {"path": "/a.txt", "call_goal": "c2"},
                )
            )
        assert anomaly is not None
        assert anomaly.evidence.get("detector_kind") == "generic_repeat"


class TestInterruptKindFilter:
    """Signal.interrupt_kind drives pseudo-record filtering at the detector."""

    def _permission_ask_exc(self) -> BaseException:
        # Match the real exception chain (AbortError(cause=ToolInterruptException))
        # raised by BaseSecurityRail._raise_tool_interrupt.
        class _AbortLike(Exception):
            pass

        abort = _AbortLike("tool interrupted")
        abort.cause = ToolInterruptException(request=InterruptRequest(message="ask"))
        return abort

    def test_builder_tags_tool_exception_with_permission_ask_signal(self):
        exc = self._permission_ask_exc()
        inputs = SimpleNamespace(tool_name="read_file", tool_args={"file_path": "/a"}, tool_msg=None)
        sig = build_tool_exception_signal("main", inputs, exc)
        assert sig.interrupt_kind is SignalInterruptKind.PERMISSION_ASK_SIGNAL

    def test_builder_leaves_real_exception_untagged(self):
        inputs = SimpleNamespace(tool_name="web_search", tool_args={"q": "x"}, tool_msg=None)
        sig = build_tool_exception_signal(
            "main", inputs, RuntimeError("503 timeout"),
        )
        assert sig.interrupt_kind is None

    def test_builder_tags_after_tool_redo_when_ctx_has_interrupt(self):
        inputs = SimpleNamespace(
            tool_name="read_file", tool_args={"file_path": "/a"}, tool_result={"content": "x"},
        )
        ctx = SimpleNamespace(exception=self._permission_ask_exc())
        sig = build_after_tool_call_signal("main", inputs, ctx)
        assert sig.interrupt_kind is SignalInterruptKind.PERMISSION_ASK_SIGNAL

    def test_builder_leaves_after_tool_untagged_without_ctx_exception(self):
        inputs = SimpleNamespace(
            tool_name="read_file", tool_args={"file_path": "/a"}, tool_result={"content": "x"},
        )
        sig = build_after_tool_call_signal("main", inputs)
        assert sig.interrupt_kind is None

    @pytest.mark.asyncio
    async def test_detector_drops_pseudo_tool_exception(self):
        det = RepeatToolCallDetector(RepeatToolConfig(warning_threshold=2))
        with patch.object(det, "_schema_keys", return_value=("file_path",)):
            sig = Signal(
                kind=SignalKind.TOOL_EXCEPTION,
                member_name="main",
                tool_name="read_file",
                tool_args={"file_path": "/a"},
                error="[aborted]",
                interrupt_kind=SignalInterruptKind.PERMISSION_ASK_SIGNAL,
            )
            assert await det.observe(sig) is None
            # Real failure with same args still records normally:
            real = await det.observe(
                _after_tool(
                    "read_file",
                    {"file_path": "/a"},
                    tool_result={"content": "test"},
                )
            )
            assert real is None  # only 1 record → below threshold
            history = det._history("main")
            assert len(history) == 1
            assert history[-1].args_hash is not None

    @pytest.mark.asyncio
    async def test_detector_drops_pseudo_after_tool_redo(self):
        det = RepeatToolCallDetector(RepeatToolConfig(warning_threshold=2))
        sig = Signal(
            kind=SignalKind.AFTER_TOOL_CALL,
            member_name="main",
            tool_name="read_file",
            tool_args={"file_path": "/a"},
            tool_result={"content": "interrupt-error-template"},
            interrupt_kind=SignalInterruptKind.PERMISSION_ASK_SIGNAL,
        )
        assert await det.observe(sig) is None
        assert "main" not in det._histories or not det._histories["main"]

    def test_interrupt_kind_serialization_round_trip(self):
        from openjiuwen.harness.agent_ras.models import Signal

        sig = Signal(
            kind=SignalKind.TOOL_EXCEPTION,
            member_name="main",
            tool_name="x",
            interrupt_kind=SignalInterruptKind.PERMISSION_ASK_SIGNAL,
        )
        payload = sig.to_dict()
        assert payload["interrupt_kind"] == "permission_ask_signal"
        restored = Signal.from_dict(payload)
        assert restored.interrupt_kind is SignalInterruptKind.PERMISSION_ASK_SIGNAL


def _before_model(member_name: str = "main") -> Signal:
    return Signal(kind=SignalKind.BEFORE_MODEL_CALL, member_name=member_name)


def _failing(
    tool_name: str,
    tool_args: dict,
    *,
    error: str = "boom",
    member_name: str = "main",
) -> Signal:
    return Signal(
        kind=SignalKind.AFTER_TOOL_CALL,
        member_name=member_name,
        tool_name=tool_name,
        tool_args=tool_args,
        tool_result={"success": False, "data": None, "error": error},
    )


_READ_BEFORE_EDIT = (
    "File must be read before editing. Call read_file on '/a.html' first."
)
_MODIFIED_EXTERNALLY = (
    "'/a.html' has been modified externally since it was last read. "
    "Re-read the file before editing."
)


class TestBatchCollapse:
    """Parallel calls of one LLM turn collapse into a single streak unit."""

    @pytest.mark.asyncio
    async def test_before_model_call_starts_new_batch(self):
        det = RepeatToolCallDetector()
        with patch.object(det, "_schema_keys", return_value=("file_path",)):
            assert await det.observe(_before_model()) is None
            await det.observe(
                _failing("edit_file", {"file_path": "/a"}, error=_READ_BEFORE_EDIT),
            )
        assert det._history("main")[-1].batch_seq == 1

    @pytest.mark.asyncio
    async def test_same_turn_recoverable_batch_does_not_trip(self):
        """The production incident: 10 parallel read-before-edit failures."""
        cfg = RepeatToolConfig(
            warning_threshold=100,
            critical_threshold=100,
            global_breaker_threshold=100,
            unknown_tool_threshold=10,
            recoverable_error_threshold=20,
        )
        det = RepeatToolCallDetector(cfg)
        with patch.object(det, "_schema_keys", return_value=("file_path",)):
            assert await det.observe(_before_model()) is None
            for i in range(10):
                error = _MODIFIED_EXTERNALLY if i == 0 else _READ_BEFORE_EDIT
                anomaly = await det.observe(
                    _failing(
                        "edit_file",
                        {"file_path": "/a.html", "old_string": f"s{i}"},
                        error=error,
                    ),
                )
                assert anomaly is None
            history = list(det._history("main"))
            assert len(history) == 10
            assert all(record.batch_seq == 1 for record in history)
            assert all(record.recoverable for record in history)
            assert det._get_failing_tool_streaks(history, "edit_file") == (0, 1, 10)

    @pytest.mark.asyncio
    async def test_hard_failures_accumulate_per_turn(self):
        cfg = RepeatToolConfig(
            warning_threshold=100,
            critical_threshold=100,
            global_breaker_threshold=100,
            unknown_tool_threshold=4,
        )
        det = RepeatToolCallDetector(cfg)
        with patch.object(det, "_schema_keys", return_value=("file_path",)):
            assert await det.observe(_before_model()) is None
            await det.observe(_failing("edit_file", {"file_path": "/1"}))
            await det.observe(_failing("edit_file", {"file_path": "/2"}))

            assert await det.observe(_before_model()) is None
            low = await det.observe(_failing("edit_file", {"file_path": "/3"}))
            assert low is not None
            assert low.severity == Severity.LOW
            assert low.evidence["msg_key"] == "unknown_tool_repeat_warning"
            assert low.evidence["count"] == 3

            assert await det.observe(_before_model()) is None
            # Same (kind, severity, detector_kind) signature → deduped.
            assert await det.observe(_failing("edit_file", {"file_path": "/4"})) is None

            assert await det.observe(_before_model()) is None
            critical = await det.observe(_failing("edit_file", {"file_path": "/5"}))
            assert critical is not None
            assert critical.severity == Severity.CRITICAL
            assert critical.evidence["count"] == 5

    @pytest.mark.asyncio
    async def test_global_breaker_same_turn_identical_calls_collapse(self):
        cfg = RepeatToolConfig(
            warning_threshold=100,
            critical_threshold=100,
            global_breaker_threshold=3,
            unknown_tool_threshold=100,
        )
        det = RepeatToolCallDetector(cfg)
        with patch.object(det, "_schema_keys", return_value=("file_path",)):
            assert await det.observe(_before_model()) is None
            for _ in range(3):
                assert await det.observe(
                    _failing("read_file", {"file_path": "/a"}, error="locked"),
                ) is None

            assert await det.observe(_before_model()) is None
            assert await det.observe(
                _failing("read_file", {"file_path": "/a"}, error="locked"),
            ) is None

            assert await det.observe(_before_model()) is None
            anomaly = await det.observe(
                _failing("read_file", {"file_path": "/a"}, error="locked"),
            )
        assert anomaly is not None
        assert anomaly.evidence["detector_kind"] == "global_circuit_breaker"
        assert anomaly.evidence["count"] == 3


class TestRecoverableErrorStreak:
    @pytest.mark.asyncio
    async def test_recoverable_failures_excluded_from_hard_streak(self):
        cfg = RepeatToolConfig(
            warning_threshold=100,
            critical_threshold=100,
            global_breaker_threshold=100,
            unknown_tool_threshold=4,
            recoverable_error_threshold=100,
        )
        det = RepeatToolCallDetector(cfg)
        with patch.object(det, "_schema_keys", return_value=("file_path",)):
            for turn in range(3):
                assert await det.observe(_before_model()) is None
                assert await det.observe(
                    _failing(
                        "edit_file",
                        {"file_path": f"/r{turn}"},
                        error=_READ_BEFORE_EDIT,
                    ),
                ) is None

            # Hard streak only counts hard failures: 2 hard turns reach the
            # LOW threshold even though 3 recoverable failures preceded.
            assert await det.observe(_before_model()) is None
            assert await det.observe(
                _failing("edit_file", {"file_path": "/h1"}),
            ) is None
            assert await det.observe(_before_model()) is None
            low = await det.observe(
                _failing("edit_file", {"file_path": "/h2"}),
            )
        assert low is not None
        assert low.severity == Severity.LOW
        assert low.evidence["msg_key"] == "unknown_tool_repeat_warning"
        assert low.evidence["count"] == 5

    @pytest.mark.asyncio
    async def test_recoverable_soft_cap_still_terminates(self):
        cfg = RepeatToolConfig(
            warning_threshold=100,
            critical_threshold=100,
            global_breaker_threshold=100,
            unknown_tool_threshold=100,
            recoverable_error_threshold=4,
        )
        det = RepeatToolCallDetector(cfg)
        with patch.object(det, "_schema_keys", return_value=("file_path",)):
            assert await det.observe(_before_model()) is None
            assert await det.observe(
                _failing("edit_file", {"file_path": "/1"}, error=_READ_BEFORE_EDIT),
            ) is None

            assert await det.observe(_before_model()) is None
            low = await det.observe(
                _failing("edit_file", {"file_path": "/2"}, error=_READ_BEFORE_EDIT),
            )
            assert low is not None
            assert low.severity == Severity.LOW
            assert low.evidence["msg_key"] == "recoverable_error_repeat_warning"

            assert await det.observe(_before_model()) is None
            assert await det.observe(
                _failing("edit_file", {"file_path": "/3"}, error=_READ_BEFORE_EDIT),
            ) is None

            assert await det.observe(_before_model()) is None
            critical = await det.observe(
                _failing("edit_file", {"file_path": "/4"}, error=_READ_BEFORE_EDIT),
            )
        assert critical is not None
        assert critical.severity == Severity.CRITICAL
        assert critical.evidence["msg_key"] == "recoverable_error_repeat"
        assert critical.evidence["count"] == 4


class TestRegisteredVsUnknownMessage:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("card", "source", "expected_msg_key"),
        [
            (object(), "ability_manager", "tool_failure_repeat"),
            (None, "resource_mgr", "tool_failure_repeat"),
            (None, "none", "unknown_tool_repeat"),
        ],
    )
    async def test_critical_msg_key_follows_tool_resolution(
        self, card, source, expected_msg_key,
    ):
        cfg = RepeatToolConfig(
            warning_threshold=100,
            critical_threshold=100,
            global_breaker_threshold=100,
            unknown_tool_threshold=2,
        )
        det = RepeatToolCallDetector(cfg)
        with patch.object(det, "_schema_keys", return_value=("file_path",)), \
             patch.object(det, "_resolve_tool_card", return_value=(card, source)):
            for turn in range(2):
                assert await det.observe(_before_model()) is None
                anomaly = await det.observe(
                    _failing("edit_file", {"file_path": f"/{turn}"}),
                )
        assert anomaly is not None
        assert anomaly.severity == Severity.CRITICAL
        assert anomaly.evidence["detector_kind"] == expected_msg_key
        assert anomaly.evidence["msg_key"] == expected_msg_key

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("card", "source", "expected_detector_kind", "expected_msg_key"),
        [
            (
                object(), "ability_manager",
                "tool_failure_repeat", "tool_failure_repeat_warning",
            ),
            (
                None, "resource_mgr",
                "tool_failure_repeat", "tool_failure_repeat_warning",
            ),
            (
                None, "none",
                "unknown_tool_repeat", "unknown_tool_repeat_warning",
            ),
        ],
    )
    async def test_warning_msg_key_follows_tool_resolution(
        self, card, source, expected_detector_kind, expected_msg_key,
    ):
        cfg = RepeatToolConfig(
            warning_threshold=100,
            critical_threshold=100,
            global_breaker_threshold=100,
            unknown_tool_threshold=4,
        )
        det = RepeatToolCallDetector(cfg)
        with patch.object(det, "_schema_keys", return_value=("file_path",)), \
             patch.object(det, "_resolve_tool_card", return_value=(card, source)):
            anomaly = None
            for turn in range(2):
                assert await det.observe(_before_model()) is None
                anomaly = await det.observe(
                    _failing("edit_file", {"file_path": f"/{turn}"}),
                )
        assert anomaly is not None
        assert anomaly.severity == Severity.LOW
        assert anomaly.evidence["detector_kind"] == expected_detector_kind
        assert anomaly.evidence["msg_key"] == expected_msg_key


class TestHydrationBatchCounter:
    @pytest.mark.asyncio
    async def test_hydration_continues_batch_counter(self):
        session = _mock_session(pending_interrupt=True)
        cfg = RepeatToolConfig()
        with patch.object(
            RepeatToolCallDetector,
            "_schema_keys",
            return_value=("file_path",),
        ):
            first = RepeatToolCallDetector(cfg)
            first.reset(session)
            assert await first.observe(_before_model()) is None
            await first.observe(_failing("edit_file", {"file_path": "/a"}))
            first.reset(session)  # stash preserved

            second = RepeatToolCallDetector(cfg)
            second.reset(session)  # hydrate
            assert await second.observe(_before_model()) is None
            await second.observe(_failing("edit_file", {"file_path": "/b"}))
            assert second._history("main")[-1].batch_seq == 2


class TestRecordSerialization:
    def test_round_trip_with_new_fields(self):
        record = ToolCallRecord(
            tool_name="edit_file",
            args_hash="h",
            result_hash="r",
            timestamp=1.0,
            has_error=True,
            recoverable=True,
            batch_seq=7,
        )
        restored = ToolCallRecord.from_dict(record.to_dict())
        assert restored == record

    def test_from_dict_legacy_payload_defaults(self):
        record = ToolCallRecord.from_dict({
            "tool_name": "edit_file",
            "args_hash": "h",
            "has_error": True,
        })
        assert record is not None
        assert record.recoverable is False
        assert record.batch_seq == 0


class TestRecoverableClassification:
    def test_structured_recoverable_flag(self):
        assert ToolResultErrorDetector.is_recoverable({
            "success": False, "recoverable": True,
        }) is True

    def test_error_code_marker(self):
        assert ToolResultErrorDetector.is_recoverable({
            "success": False, "error_code": "read_before_edit",
        }) is True
        assert ToolResultErrorDetector.is_recoverable({
            "success": False, "errorCode": "FILE_MODIFIED_EXTERNALLY",
        }) is True

    def test_error_text_markers(self):
        assert ToolResultErrorDetector.is_recoverable({
            "success": False, "error": _READ_BEFORE_EDIT,
        }) is True
        assert ToolResultErrorDetector.is_recoverable({
            "success": False, "error": _MODIFIED_EXTERNALLY,
        }) is True

    def test_repr_string_with_error_text(self):
        result = (
            "success=False data=None "
            "error=\"File must be read before editing. Call read_file on 'x' first.\""
        )
        assert ToolResultErrorDetector.has_error(result) is True
        assert ToolResultErrorDetector.is_recoverable(result) is True

    def test_repr_string_without_error_text_not_recoverable(self):
        assert ToolResultErrorDetector.has_error("success=False data=None") is True
        assert ToolResultErrorDetector.is_recoverable("success=False data=None") is False

    def test_success_result_not_recoverable(self):
        assert ToolResultErrorDetector.is_recoverable({"success": True}) is False

    def test_generic_error_not_recoverable(self):
        assert ToolResultErrorDetector.is_recoverable({
            "success": False, "error": "permission denied",
        }) is False

    def test_bare_error_text_marker(self):
        assert ToolResultErrorDetector.is_recoverable(_MODIFIED_EXTERNALLY) is True

    def test_register_extra_marker(self):
        try:
            ToolResultErrorDetector.register_recoverable_marker(
                "custom precondition marker",
            )
            assert ToolResultErrorDetector.is_recoverable({
                "success": False,
                "error": "failed: Custom Precondition Marker not met",
            }) is True
        finally:
            ToolResultErrorDetector._extra_recoverable_markers.clear()

    @pytest.mark.asyncio
    async def test_recoverable_via_signal_error_and_msg_content(self):
        """Classification also honors tool_msg_content and signal.error text."""
        cfg = RepeatToolConfig(
            warning_threshold=100,
            critical_threshold=100,
            global_breaker_threshold=100,
            unknown_tool_threshold=10,
            recoverable_error_threshold=100,
        )
        det = RepeatToolCallDetector(cfg)
        with patch.object(det, "_schema_keys", return_value=("file_path",)):
            assert await det.observe(_before_model()) is None
            await det.observe(Signal(
                kind=SignalKind.TOOL_EXCEPTION,
                member_name="main",
                tool_name="edit_file",
                tool_args={"file_path": "/a"},
                error=(
                    "RuntimeError: File must be read before editing. "
                    "Call read_file on '/a' first."
                ),
            ))
            await det.observe(Signal(
                kind=SignalKind.AFTER_TOOL_CALL,
                member_name="main",
                tool_name="edit_file",
                tool_args={"file_path": "/b"},
                tool_result={"success": False, "error": ""},
                tool_msg_content=(
                    "success=False error=\"'/b' has been modified externally "
                    "since it was last read. Re-read the file before editing.\""
                ),
            ))
            assert await det.observe(_before_model()) is None
            await det.observe(Signal(
                kind=SignalKind.TOOL_EXCEPTION,
                member_name="main",
                tool_name="edit_file",
                tool_args={"file_path": "/c"},
                error="connection reset",
            ))
        history = list(det._history("main"))
        assert [r.recoverable for r in history] == [True, True, False]
        assert det._get_failing_tool_streaks(history, "edit_file") == (1, 1, 3)
