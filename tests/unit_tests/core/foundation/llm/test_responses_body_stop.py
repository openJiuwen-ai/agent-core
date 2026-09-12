# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from openjiuwen.core.foundation.llm.schema.message import UserMessage
from openjiuwen.core.foundation.llm.utils.responses_utils import build_request_body


def test_stop_is_not_sent_to_the_responses_api():
    """``/v1/responses`` has no ``stop`` parameter and rejects the request.

    The endpoint answers ``Unknown parameter: 'stop'. Did you mean 'store'?``,
    so a configured stop sequence turned every call into a 400.
    """
    body = build_request_body(
        model="gpt-5.6-luna",
        messages=[UserMessage(content="hi")],
        stop="\n\n",
    )

    assert "stop" not in body


def test_max_tokens_is_still_mapped_to_max_output_tokens():
    """Guards the neighbouring mapping, which is correct and easy to disturb."""
    body = build_request_body(
        model="gpt-5.6-luna",
        messages=[UserMessage(content="hi")],
        max_tokens=256,
    )

    assert body["max_output_tokens"] == 256
    assert "max_tokens" not in body
