# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Exception log events must keep the string error_code contract."""

from openjiuwen.core.common.logging.events import SystemEvent


def test_exception_derived_error_code_is_a_string() -> None:
    data = SystemEvent(exception=ValueError("boom")).to_dict()

    assert data["error_code"] == "-1"
    assert isinstance(data["error_code"], str)
    assert data["error_message"] == "boom"
