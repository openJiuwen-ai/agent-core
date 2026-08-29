#!/usr/bin/env python
# coding: utf-8
"""Provider error responses must enter the rail retry path."""

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import FrameworkError
from openjiuwen.core.foundation.llm import AssistantMessage, UsageMetadata
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent


def test_model_response_error_finish_reason_raises() -> None:
    message = AssistantMessage(
        content="",
        finish_reason="error",
        usage_metadata=UsageMetadata(code=504, err_msg="upstream timeout"),
    )

    with pytest.raises(FrameworkError, match="code=504") as exc_info:
        ReActAgent._raise_for_model_response_error(message)

    assert exc_info.value.status is StatusCode.MODEL_CALL_FAILED
    assert exc_info.value.details == {
        "provider_code": 504,
        "finish_reason": "error",
        "provider_message": "upstream timeout",
    }


def test_successful_model_response_does_not_raise() -> None:
    message = AssistantMessage(
        content="done",
        finish_reason="stop",
        usage_metadata=UsageMetadata(code=0),
    )

    ReActAgent._raise_for_model_response_error(message)
