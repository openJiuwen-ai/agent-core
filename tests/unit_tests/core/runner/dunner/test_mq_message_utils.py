# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""MQ server error replies must carry BaseError's code/message, not crash."""

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.runner.drunner.dmessage_queue.message import (
    DmqRequestMessage,
    DMessageType,
    ResultType,
)
from openjiuwen.core.runner.drunner.server_adapter.mq_message_utils import build_error_response


def _request() -> DmqRequestMessage:
    return DmqRequestMessage(
        type=DMessageType.INPUT,
        message_id="mid-1",
        request_id="rid-1",
        sender_id="client-1",
        reply_topic="reply-1",
        payload={},
    )


def test_error_response_uses_base_error_code_and_message() -> None:
    error = build_error(
        StatusCode.MESSAGE_QUEUE_MESSAGE_PROCESS_EXECUTION_ERROR,
        reason="boom",
    )

    response = build_error_response(_request(), "server-1", error)

    assert response.result_type == ResultType.ERROR
    assert response.error_code == error.code
    assert response.error_msg == error.message
    assert response.last_chunk is True
