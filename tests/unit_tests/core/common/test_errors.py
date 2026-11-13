#  !/usr/bin/env python
#  -*- coding: UTF-8 -*-
#  Copyright c) Huawei Technologies Co. Ltd. 2025-2025

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import (
    STATUS_TO_EXCEPTION,
    BaseError,
    ToolError,
    _format_template,
    build_error,
    raise_error,
)
from openjiuwen.core.common.schema.card import BaseCard


def test_build_error_returns_instance():
    e = build_error(StatusCode.AGENT_TOOL_EXECUTION_ERROR, msg="failed", details={"tool": "xyz"})
    assert isinstance(e, BaseError)
    assert e.code == StatusCode.AGENT_TOOL_EXECUTION_ERROR.code
    assert e.details == {"tool": "xyz"}


def test_raise_error_raises_correct_type():
    expected = STATUS_TO_EXCEPTION[StatusCode.AGENT_TOOL_EXECUTION_ERROR]
    with pytest.raises(expected):
        raise_error(StatusCode.AGENT_TOOL_EXECUTION_ERROR, msg="fail")


def test_build_error_maps_to_manual_override():
    # Some legacy manual override keys may not exist in the current StatusCode enum.
    # Verify that building an error for an existing "tool not found" code returns the
    # expected exception class from the STATUS_TO_EXCEPTION map.
    key = StatusCode.AGENT_TOOL_NOT_FOUND if hasattr(StatusCode, "AGENT_TOOL_NOT_FOUND") \
        else StatusCode.AGENT_TOOL_NOT_FOUND
    exc_cls = STATUS_TO_EXCEPTION[key]
    e2 = build_error(key)
    assert isinstance(e2, exc_cls)


def test_format_template_missing_key_safe():
    # Use the current enum member name
    tmpl = StatusCode.WORKFLOW_EXECUTION_ERROR.errmsg
    rendered = _format_template(tmpl, params=None)
    assert isinstance(rendered, str)
    assert "<missing:" in rendered


def test_tool_error_merges_card_into_existing_details():
    card = BaseCard(name="demo")
    e = ToolError(StatusCode.TOOL_EXECUTION_ERROR, details={"tool": "xyz"}, card=card)
    assert e.details["tool"] == "xyz"
    assert e.details["card"] == card
    assert e.card() == card


def test_tool_error_without_card_keeps_details():
    e = ToolError(StatusCode.TOOL_EXECUTION_ERROR, details={"tool": "xyz"})
    assert e.details == {"tool": "xyz"}
    assert e.card() is None
