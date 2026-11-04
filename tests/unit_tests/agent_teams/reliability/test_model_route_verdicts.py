# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Reliability handling of model-group routing verdicts (issue #5489)."""
import pytest

from openjiuwen.agent_teams.i18n import get_language, set_language
from openjiuwen.agent_teams.reliability.anomaly import Anomaly, AnomalyKind, Severity
from openjiuwen.agent_teams.reliability.detectors.model_error import ModelStreamErrorDetector
from openjiuwen.agent_teams.reliability.detectors.pingpong import PingPongDetector
from openjiuwen.agent_teams.reliability.handler import ReliabilityHandler
from openjiuwen.agent_teams.reliability.rail import _model_failure
from openjiuwen.agent_teams.reliability.remediation.local import LocalAutoRemediator
from openjiuwen.agent_teams.reliability.remediation.policy import RemediationPolicy
from openjiuwen.agent_teams.reliability.signals import Signal, SignalKind
from openjiuwen.agent_teams.schema.events import AnomalyDetectedEvent, EventMessage
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error

_FAILURE = {
    "deployment_id": "dpl_c",
    "model_name": "C",
    "provider": "openai",
    "endpoint": "api.a.com",
    "position": 1,
    "category": "deterministic",
    "status_code": 401,
    "error_type": "DeploymentAuthError",
    "message": "rejected the API key (HTTP 401)",
}


@pytest.fixture(autouse=True)
def _chinese():
    previous = get_language()
    set_language("cn")
    yield
    set_language(previous)


def _verdict(route_case: str, *, reason: str = "all_deterministic", user_action: bool = False,
             failures: list | None = None) -> dict:
    return {
        "route_case": route_case,
        "reason": reason,
        "pinned_model": "C",
        "failures": [_FAILURE] if failures is None else failures,
        "user_action_required": user_action,
    }


def _exception_signal(verdict: dict | None) -> Signal:
    return Signal(kind=SignalKind.MODEL_EXCEPTION, member_name="dev-1", error="boom", model_failure=verdict)


class TestDetector:
    @pytest.mark.level0
    def test_user_action_escalates_once_until_success(self):
        detector = ModelStreamErrorDetector()
        verdict = _verdict("exhausted_user_action", user_action=True)

        first = detector.observe(_exception_signal(verdict))
        second = detector.observe(_exception_signal(verdict))
        detector.observe(Signal(kind=SignalKind.AFTER_MODEL_CALL, member_name="dev-1"))
        third = detector.observe(_exception_signal(verdict))

        assert first is not None
        assert first.kind == AnomalyKind.MODEL_UNAVAILABLE
        assert first.severity == Severity.CRITICAL
        assert "ask_user" in first.summary
        assert "第 1 顺位 C（openai, api.a.com）API Key 校验未通过（HTTP 401）" in first.summary
        assert "没有可以替代它的同名模型" in first.summary
        assert second is None
        assert third is not None

    @pytest.mark.level0
    def test_switch_notice_once_per_deployment_and_reason(self):
        detector = ModelStreamErrorDetector()
        verdict = _verdict("switchable", reason="deterministic")

        first = detector.observe(_exception_signal(verdict))
        second = detector.observe(_exception_signal(verdict))

        assert first is not None
        assert first.kind == AnomalyKind.MODEL_ROUTE_NOTICE
        assert first.severity == Severity.MEDIUM
        assert second is None

    @pytest.mark.level1
    def test_notice_does_not_suppress_later_escalation(self):
        detector = ModelStreamErrorDetector()
        detector.observe(_exception_signal(_verdict("switchable", reason="deterministic")))

        escalation = detector.observe(_exception_signal(_verdict("exhausted_user_action", user_action=True)))

        assert escalation is not None
        assert escalation.severity == Severity.CRITICAL

    @pytest.mark.level1
    def test_transient_switch_needs_no_notice(self):
        detector = ModelStreamErrorDetector()

        anomaly = detector.observe(_exception_signal(_verdict("switchable", reason="transient")))

        assert anomaly is None

    @pytest.mark.level1
    def test_notice_reports_the_triggering_deployment(self):
        detector = ModelStreamErrorDetector()
        earlier = {**_FAILURE, "deployment_id": "dpl_a", "model_name": "A", "position": 2, "status_code": 502,
                   "error_type": "DeploymentServerError", "category": "transient", "message": "server error"}
        verdict = _verdict("switchable", reason="deterministic", failures=[_FAILURE, earlier])

        anomaly = detector.observe(_exception_signal(verdict))

        assert anomaly is not None
        assert "第 1 顺位 C（" in anomaly.summary
        assert "A（" not in anomaly.summary

    @pytest.mark.level1
    def test_pending_retry_is_silent_and_not_counted(self):
        detector = ModelStreamErrorDetector(consecutive_threshold=1)
        verdict = _verdict("exhausted_retryable", reason="has_transient")

        results = [detector.observe(_exception_signal(verdict)) for _ in range(3)]

        assert results == [None, None, None]

    @pytest.mark.level1
    def test_plain_model_errors_keep_burst_counting(self):
        detector = ModelStreamErrorDetector(consecutive_threshold=2)

        detector.observe(_exception_signal(None))
        anomaly = detector.observe(_exception_signal(None))

        assert anomaly is not None
        assert anomaly.kind == AnomalyKind.MODEL_ERROR

    @pytest.mark.level1
    def test_rejected_request_counts_like_a_plain_model_error(self):
        detector = ModelStreamErrorDetector(consecutive_threshold=2)
        verdict = _verdict("exhausted_request_rejected", reason="request_rejected")

        first = detector.observe(_exception_signal(verdict))
        second = detector.observe(_exception_signal(verdict))

        assert first is None
        assert second is not None
        assert second.kind == AnomalyKind.MODEL_ERROR

    @pytest.mark.level1
    def test_missing_model_reports_not_in_group(self):
        detector = ModelStreamErrorDetector()
        verdict = _verdict("exhausted_user_action", reason="tag_not_found", user_action=True, failures=[])

        anomaly = detector.observe(_exception_signal(verdict))

        assert anomaly is not None
        assert "模型 C 不可用" in anomaly.summary
        assert "不在当前模型组中" in anomaly.summary


class _Host:
    def __init__(self) -> None:
        self.delivered: list[str] = []

    async def deliver_input(self, content: str) -> None:
        self.delivered.append(content)


class _Blueprint:
    def __init__(self) -> None:
        self.role = TeamRole.LEADER
        self.member_name = "team_leader"


def _handler() -> tuple[ReliabilityHandler, _Host]:
    host = _Host()
    handler = ReliabilityHandler(
        host, _Blueprint(), infra=None, poll_ctrl=None,
        policy=RemediationPolicy(), pingpong=PingPongDetector(min_volleys=6),
    )
    return handler, host


def _anomaly(kind: AnomalyKind, severity: Severity, evidence: dict | None = None) -> Anomaly:
    return Anomaly(
        detector="model_error", kind=kind, severity=severity, member_name="team_leader", summary="x",
        evidence=evidence or {},
    )


def _notice_verdict(failure: dict | None = None, pinned: str | None = None) -> dict:
    verdict = _verdict("switchable", reason="deterministic", failures=[failure or _FAILURE])
    verdict["pinned_model"] = pinned
    return verdict


def _notice_event(member: str, failure: dict | None = None, pinned: str | None = None) -> EventMessage:
    return EventMessage.from_event(AnomalyDetectedEvent(
        team_name="t", member_name=member, detector="model_error",
        kind=AnomalyKind.MODEL_ROUTE_NOTICE.value, severity=Severity.MEDIUM.value,
        summary="switched", evidence={"model_failure": _notice_verdict(failure, pinned)},
    ))


class TestHandler:
    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_member_route_notice_is_delivered_as_notice(self):
        handler, host = _handler()

        await handler.on_anomaly_detected(_notice_event("dev-1"))

        assert len(host.delivered) == 1
        assert "switched" in host.delivered[0]
        # The switch belongs to the shared router, not to the reporting member.
        assert "dev-1" not in host.delivered[0]
        assert "没有指定模型的成员（包括你）都已改用新模型" in host.delivered[0]
        assert "[可靠性告警]" not in host.delivered[0]
        assert "[reliability alert]" not in host.delivered[0]

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_pinned_route_notice_is_scoped_to_pinned_members(self):
        handler, host = _handler()

        await handler.on_anomaly_detected(_notice_event("dev-1", pinned="C"))

        assert len(host.delivered) == 1
        assert "只影响指定使用 C 的成员" in host.delivered[0]
        assert "包括你" not in host.delivered[0]

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_same_route_failure_from_other_members_is_delivered_once(self):
        handler, host = _handler()

        await handler.on_anomaly_detected(_notice_event("dev-1"))
        await handler.on_anomaly_detected(_notice_event("dev-2"))
        await handler.handle_local_anomaly(_anomaly(
            AnomalyKind.MODEL_ROUTE_NOTICE, Severity.MEDIUM, evidence={"model_failure": _notice_verdict()},
        ))

        assert len(host.delivered) == 1

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_same_failure_on_shared_and_pinned_router_is_delivered_for_each(self):
        handler, host = _handler()

        await handler.on_anomaly_detected(_notice_event("dev-1", pinned="C"))
        await handler.on_anomaly_detected(_notice_event("dev-2"))
        await handler.on_anomaly_detected(_notice_event("dev-3", pinned="C"))

        assert len(host.delivered) == 2

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_other_deployment_or_status_is_delivered_again(self):
        handler, host = _handler()

        await handler.on_anomaly_detected(_notice_event("dev-1"))
        await handler.on_anomaly_detected(_notice_event("dev-1", {**_FAILURE, "deployment_id": "dpl_d"}))
        await handler.on_anomaly_detected(_notice_event("dev-1", {**_FAILURE, "status_code": 404}))

        assert len(host.delivered) == 3

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_leader_own_route_notice_is_delivered(self):
        handler, host = _handler()

        await handler.handle_local_anomaly(_anomaly(
            AnomalyKind.MODEL_ROUTE_NOTICE, Severity.MEDIUM, evidence={"model_failure": _notice_verdict()},
        ))

        assert len(host.delivered) == 1
        assert "[模型路由通知]" in host.delivered[0]

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_leader_own_model_unavailable_is_not_delivered(self):
        handler, host = _handler()

        await handler.handle_local_anomaly(_anomaly(AnomalyKind.MODEL_UNAVAILABLE, Severity.CRITICAL))

        assert host.delivered == []

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_leader_other_anomalies_still_delivered(self):
        handler, host = _handler()

        await handler.handle_local_anomaly(_anomaly(AnomalyKind.MODEL_ERROR, Severity.MEDIUM))

        assert len(host.delivered) == 1


class TestLocalSteering:
    @pytest.mark.level1
    def test_model_verdicts_are_not_steered(self):
        remediator = LocalAutoRemediator(RemediationPolicy())

        assert remediator.steer_message(_anomaly(AnomalyKind.MODEL_UNAVAILABLE, Severity.CRITICAL)) is None
        assert remediator.steer_message(_anomaly(AnomalyKind.MODEL_ROUTE_NOTICE, Severity.HIGH)) is None
        assert remediator.steer_message(_anomaly(AnomalyKind.MODEL_ERROR, Severity.HIGH)) is not None


class TestRailExtraction:
    @pytest.mark.level1
    def test_extracts_verdict_from_cause_chain(self):
        inner = build_error(
            StatusCode.MODEL_GROUP_NO_AVAILABLE_ROUTE, error_msg="x",
            details={**_verdict("exhausted_user_action", user_action=True), "should_retry": False},
        )
        outer = RuntimeError("wrapped")
        outer.__cause__ = inner

        verdict = _model_failure(outer)

        assert verdict is not None
        assert verdict["route_case"] == "exhausted_user_action"
        assert verdict["user_action_required"] is True
        assert "should_retry" not in verdict

    @pytest.mark.level1
    def test_errors_without_verdict(self):
        assert _model_failure(ValueError("x")) is None
        assert _model_failure(None) is None


class TestNoticeContent:
    @pytest.mark.level1
    def test_notice_names_both_models_by_group_position(self):
        detector = ModelStreamErrorDetector()
        usable = [
            {"model_name": "D", "provider": "deepseek", "endpoint": "api.d.com", "position": 2},
            {"model_name": "E", "provider": "openai", "endpoint": "api.e.com", "position": 3},
        ]
        verdict = {**_verdict("switchable", reason="deterministic"), "pinned_model": None, "usable": usable}

        anomaly = detector.observe(_exception_signal(verdict))

        assert anomaly is not None
        assert anomaly.summary.startswith(
            "模型组中第 1 顺位 C（openai, api.a.com）API Key 校验未通过（HTTP 401），"
            "已自动改用第 2 顺位 D（deepseek, api.d.com）"
        )
        assert "E（" not in anomaly.summary

    @pytest.mark.level1
    def test_notice_ends_at_the_replacement_model(self):
        detector = ModelStreamErrorDetector()
        failure = {**_FAILURE, "model_name": "GLM-5.3-flash"}
        usable = [{"model_name": "GLM-5.3-Flash", "provider": "openai", "endpoint": "api.a.com", "position": 2}]
        verdict = {**_verdict("switchable", reason="deterministic", failures=[failure]), "usable": usable}

        anomaly = detector.observe(_exception_signal(verdict))

        assert anomaly is not None
        assert anomaly.summary == (
            "模型组中第 1 顺位 GLM-5.3-flash（openai, api.a.com）API Key 校验未通过（HTTP 401），"
            "已自动改用第 2 顺位 GLM-5.3-Flash（openai, api.a.com）"
        )

    @pytest.mark.level1
    def test_interrupted_answer_reason_is_localized(self):
        detector = ModelStreamErrorDetector()
        failure = {**_FAILURE, "category": "stream_interrupted", "status_code": None,
                   "error_type": "DeploymentNetworkError"}
        verdict = _verdict("switchable", reason="stream_interrupted", failures=[failure])

        anomaly = detector.observe(_exception_signal(verdict))

        assert anomaly is not None
        assert "回答到一半中断（无法连接）" in anomaly.summary
        assert "本轮已中断" in anomaly.summary

    @pytest.mark.level1
    def test_whole_group_unavailable_is_not_called_a_same_name_gap(self):
        detector = ModelStreamErrorDetector()
        verdict = {**_verdict("exhausted_user_action", user_action=True), "pinned_model": None}

        anomaly = detector.observe(_exception_signal(verdict))

        assert anomaly is not None
        assert "模型组中的其他模型也都不可用" in anomaly.summary
        assert "同名" not in anomaly.summary

    @pytest.mark.level1
    def test_rejected_request_keeps_the_provider_detail(self):
        detector = ModelStreamErrorDetector()
        failure = {**_FAILURE, "category": "request_rejected", "status_code": 400, "error_type": "DeploymentError",
                   "message": "rejected the request (HTTP 400): max tokens (32768) exceeded"}
        verdict = _verdict("exhausted_user_action", user_action=True, failures=[failure])

        anomaly = detector.observe(_exception_signal(verdict))

        assert anomaly is not None
        assert "拒绝了本次请求（HTTP 400）：max tokens (32768) exceeded" in anomaly.summary

    @pytest.mark.level1
    def test_english_notice(self):
        set_language("en")
        detector = ModelStreamErrorDetector()
        usable = [{"model_name": "D", "provider": "deepseek", "endpoint": "api.d.com", "position": 2}]
        verdict = {**_verdict("switchable", reason="deterministic"), "usable": usable}

        anomaly = detector.observe(_exception_signal(verdict))

        assert anomaly is not None
        assert anomaly.summary.startswith(
            "#1 C (openai, api.a.com) in the model group rejected the API key (HTTP 401); "
            "switched to #2 D (deepseek, api.d.com) automatically"
        )

    @pytest.mark.level1
    def test_rail_passes_usable_deployments_through(self):
        inner = build_error(
            StatusCode.MODEL_CALL_FAILED, error_msg="x",
            details={**_verdict("switchable", reason="deterministic"), "usable": [{"model_name": "D"}]},
        )

        assert _model_failure(inner)["usable"] == [{"model_name": "D"}]
