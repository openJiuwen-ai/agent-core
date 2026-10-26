# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from types import SimpleNamespace

from openjiuwen.core.foundation.tool.mcp.base import extract_mcp_tool_result_content

PAYLOAD = '{"summary":"endpoint document","parameters":[1,2,3],"marker":"PAYLOAD_END"}'
TRAILER = '{"doc_version":"5.7"}'


def _text_result(*texts: str) -> SimpleNamespace:
    return SimpleNamespace(content=[SimpleNamespace(text=text) for text in texts])


def test_merges_all_text_blocks() -> None:
    result = extract_mcp_tool_result_content(_text_result(PAYLOAD, TRAILER))

    assert result == PAYLOAD + "\n" + TRAILER


def test_single_text_block_is_unchanged() -> None:
    assert extract_mcp_tool_result_content(_text_result(PAYLOAD)) == PAYLOAD


def test_empty_content_returns_none() -> None:
    assert extract_mcp_tool_result_content(None) is None
    assert extract_mcp_tool_result_content(SimpleNamespace(content=[])) is None


def test_all_empty_text_blocks_return_last_block() -> None:
    assert extract_mcp_tool_result_content(_text_result("", "")) == ""


def test_mixed_text_and_image_keeps_both_blocks() -> None:
    result = SimpleNamespace(
        content=[
            SimpleNamespace(text=PAYLOAD),
            SimpleNamespace(mimeType="image/png", data="abc123"),
        ]
    )

    assert extract_mcp_tool_result_content(result) == (PAYLOAD + "\n[image content: image/png, 6 base64 chars]")


def test_mixed_image_and_text_preserves_order() -> None:
    result = SimpleNamespace(
        content=[
            SimpleNamespace(mimeType="image/png", data="abc123"),
            SimpleNamespace(text=PAYLOAD),
        ]
    )

    assert extract_mcp_tool_result_content(result) == ("[image content: image/png, 6 base64 chars]\n" + PAYLOAD)


def test_multiple_text_blocks_and_image_keep_all_text() -> None:
    result = SimpleNamespace(
        content=[
            SimpleNamespace(text=PAYLOAD),
            SimpleNamespace(text=TRAILER),
            SimpleNamespace(mimeType="image/png", data="abc123"),
        ]
    )

    assert extract_mcp_tool_result_content(result) == (
        PAYLOAD + "\n" + TRAILER + "\n[image content: image/png, 6 base64 chars]"
    )


def test_pure_image_returns_placeholder() -> None:
    result = SimpleNamespace(content=[SimpleNamespace(mimeType="image/png", data="abc123")])

    assert extract_mcp_tool_result_content(result) == ("[image content: image/png, 6 base64 chars]")
