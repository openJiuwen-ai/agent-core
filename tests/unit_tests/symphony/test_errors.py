# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ExecutionError, FrameworkError, ValidationError, build_error


def test_symphony_status_codes_have_expected_control_semantics() -> None:
    assert isinstance(
        build_error(StatusCode.COMPONENT_SYMPHONY_INVENTORY_INVALID, reason="duplicate capability"),
        ValidationError,
    )
    assert isinstance(
        build_error(StatusCode.COMPONENT_SYMPHONY_ARTIFACT_WRITE_CALL_FAILED, reason="read only"),
        FrameworkError,
    )
    assert isinstance(
        build_error(StatusCode.COMPONENT_SYMPHONY_BUILD_INTERRUPTED, reason="cancelled"),
        ExecutionError,
    )
    assert isinstance(
        build_error(StatusCode.COMPONENT_SYMPHONY_ARTIFACT_NOT_FOUND, reason="missing"),
        ValidationError,
    )


def test_symphony_status_codes_are_unique() -> None:
    symphony_codes = [status.code for status in StatusCode if status.name.startswith("COMPONENT_SYMPHONY_")]
    assert len(symphony_codes) == len(set(symphony_codes))
