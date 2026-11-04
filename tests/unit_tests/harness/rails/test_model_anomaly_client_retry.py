# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ModelAnomalyDetectionRail honours retry decisions attached by model clients."""
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.harness.rails.model_anomaly_detection_rail import _classify_transient_failure


class _ProviderError(Exception):
    """Provider error carrying a status code, as raised below the model client."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def _client_error(should_retry: bool, retry_after: float | None, cause: BaseException | None = None):
    return build_error(
        StatusCode.MODEL_CALL_FAILED,
        error_msg="model 'C' failed",
        details={"should_retry": should_retry, "retry_after_seconds": retry_after, "status_code": None},
        cause=cause,
    )


def test_client_requested_retry_overrides_non_retryable_status():
    error = _client_error(True, 1.0, cause=_ProviderError("auth failed", 401))

    decision = _classify_transient_failure(error)

    assert decision.retryable is True
    assert decision.retry_after_seconds == 1.0


def test_client_declined_retry_overrides_retryable_status():
    error = _client_error(False, None, cause=_ProviderError("server error", 503))

    decision = _classify_transient_failure(error)

    assert decision.retryable is False


def test_client_decision_wins_over_text_markers():
    error = _client_error(True, 58.0, cause=_ProviderError("invalid api key", 401))

    decision = _classify_transient_failure(error)

    assert decision.retryable is True
    assert decision.retry_after_seconds == 58.0


def test_errors_without_client_decision_keep_status_based_rules():
    assert _classify_transient_failure(_ProviderError("server error", 503)).retryable is True
    assert _classify_transient_failure(_ProviderError("bad request", 400)).retryable is False
    plain = build_error(StatusCode.MODEL_CALL_FAILED, error_msg="x", details={"status_code": 400})
    assert _classify_transient_failure(plain).retryable is False
