#  !/usr/bin/env python
#  -*- coding: UTF-8 -*-
#  Copyright c) Huawei Technologies Co. Ltd. 2025-2025

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import (
    build_error,
    raise_error,
    STATUS_TO_EXCEPTION,
    _format_template,
    BaseError,
)


def test_build_error_returns_instance():
    e = build_error(StatusCode.TOOL_EXECUTION_ERROR, msg="failed", details={"tool": "xyz"})
    assert isinstance(e, BaseError)
    assert e.code == StatusCode.TOOL_EXECUTION_ERROR.code
    assert e.details == {"tool": "xyz"}


def test_raise_error_raises_correct_type():
    expected = STATUS_TO_EXCEPTION[StatusCode.TOOL_EXECUTION_ERROR]
    with pytest.raises(expected):
        raise_error(StatusCode.TOOL_EXECUTION_ERROR, msg="fail")


def test_build_error_maps_to_manual_override():
    # Some legacy manual override keys may not exist in the current StatusCode enum.
    # Verify that building an error for an existing "tool not found" code returns the
    # expected exception class from the STATUS_TO_EXCEPTION map.
    key = StatusCode.TOOL_NOT_FOUND if hasattr(StatusCode, "TOOL_NOT_FOUND") else StatusCode.TOOL_NOT_FOUND
    exc_cls = STATUS_TO_EXCEPTION[key]
    e2 = build_error(key)
    assert isinstance(e2, exc_cls)


def test_format_template_missing_key_safe():
    # Use the current enum member name
    tmpl = StatusCode.COMPONENT_EXECUTION_ERROR.errmsg
    rendered = _format_template(tmpl, params=None)
    assert isinstance(rendered, str)
    assert "<missing:" in rendered
