# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import pytest

from openjiuwen.core.runner.callback import AbortError
from openjiuwen.core.single_agent.interrupt.exception import ToolInterruptException
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.harness.rails import BaseInterruptRail


def test_tool_interrupt_is_an_expected_abort():
    """Pausing a tool for the user is control flow: the abort is marked as no error."""
    rail = BaseInterruptRail(tool_names=["read_deck"])
    with pytest.raises(AbortError) as raised:
        rail._raise_interrupt("read_deck", None, InterruptRequest(message="run read_deck"))
    assert raised.value.is_error is False
    assert isinstance(raised.value.cause, ToolInterruptException)
