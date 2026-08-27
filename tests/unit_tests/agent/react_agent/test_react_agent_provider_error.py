#!/usr/bin/env python
# coding: utf-8
"""Provider error responses must enter the rail retry path."""

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, UsageMetadata
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent


def test_model_response_error_finish_reason_raises() -> None:
    message = AssistantMessage(
        content="",
        finish_reason="error",
        usage_metadata=UsageMetadata(code=504, err_msg="upstream timeout"),
    )

    with pytest.raises(RuntimeError, match="code=504"):
        ReActAgent._raise_for_model_response_error(message)


def test_successful_model_response_does_not_raise() -> None:
    message = AssistantMessage(
        content="done",
        finish_reason="stop",
        usage_metadata=UsageMetadata(code=0),
    )

    ReActAgent._raise_for_model_response_error(message)
