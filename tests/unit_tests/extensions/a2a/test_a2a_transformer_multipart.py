# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A2A inbound messages may carry several text/file parts."""

from a2a.types.a2a_pb2 import Message, Part

from openjiuwen.extensions.a2a.a2a_transformer import A2ATransformer


def test_from_a2a_message_keeps_every_text_part():
    message = Message(
        message_id="msg-multi",
        context_id="conv-multi",
        task_id="task-multi",
        parts=[Part(text="first line"), Part(text="second line")],
    )

    payload = A2ATransformer.from_a2a_request(message)

    assert payload["query"] == "first line\nsecond line"


def test_from_a2a_message_keeps_file_parts_as_files():
    message = Message(
        message_id="msg-file",
        context_id="conv-file",
        task_id="task-file",
        parts=[
            Part(text="please analyze"),
            Part(url="https://example.com/data.csv", filename="data.csv", media_type="text/csv"),
        ],
    )

    payload = A2ATransformer.from_a2a_request(message)

    assert payload["query"] == "please analyze"
    assert payload["files"] == [
        {"url": "https://example.com/data.csv", "filename": "data.csv", "media_type": "text/csv"}
    ]
