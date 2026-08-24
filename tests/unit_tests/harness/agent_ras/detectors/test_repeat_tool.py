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
