# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for payload_summary: bounded log summaries replacing full
messages/tools serialization."""

from openjiuwen.core.foundation.llm.payload_summary import (
    summarize_content_for_log,
    summarize_llm_payload,
    summarize_messages_for_log,
)


def test_summarize_messages_counts_and_last_user_preview() -> None:
    messages = [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "最早那轮的问题"},
        {"role": "assistant", "content": "好的"},
        {"role": "user", "content": "当前轮的问题"},
    ]
    summary = summarize_messages_for_log(messages)
    assert summary["messages_count"] == 4
    assert summary["messages_chars"] == sum(
        len(m["content"]) for m in messages
    )
    # 预览取最后一条 user 消息（当前轮）
    assert summary["last_user_preview"] == "当前轮的问…(len=6)"


def test_summarize_messages_preview_truncated_at_5() -> None:
    messages = [{"role": "user", "content": "x" * 500}]
    summary = summarize_messages_for_log(messages)
    assert summary["last_user_preview"] == "xxxxx…(len=500)"


def test_summarize_messages_list_content_form() -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "看图"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}},
                {"type": "text", "text": "什么颜色"},
            ],
        }
    ]
    summary = summarize_messages_for_log(messages)
    assert summary["last_user_preview"] == "看图 什么…(len=7)"
    assert summary["messages_chars"] == len("看图 什么颜色")


def test_summarize_messages_non_list_input() -> None:
    summary = summarize_messages_for_log(None)
    assert summary == {"messages_count": 0, "messages_chars": 0, "last_user_preview": ""}


def test_summarize_llm_payload_includes_tools_count() -> None:
    params = {
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function"}, {"type": "function"}],
    }
    summary = summarize_llm_payload(params)
    assert summary["tools_count"] == 2
    assert summary["messages_count"] == 1


def test_summarize_llm_payload_no_tools() -> None:
    summary = summarize_llm_payload({"messages": []})
    assert summary["tools_count"] == 0


def test_summarize_content_for_log() -> None:
    summary = summarize_content_for_log("y" * 250)
    assert summary["content_chars"] == 250
    assert summary["content_preview"] == "yyyyy…(len=250)"


def test_summarize_content_for_log_none() -> None:
    assert summarize_content_for_log(None) == {"content_chars": 0, "content_preview": ""}


def test_preview_masks_pii_at_source() -> None:
    messages = [{"role": "user", "content": "我手机号13800138000，身份证11010519491231002X"}]
    summary = summarize_messages_for_log(messages)
    assert "13800138000" not in summary["last_user_preview"]
    assert "11010519491231002X" not in summary["last_user_preview"]


def test_preview_masks_bank_card_and_address() -> None:
    messages = [{"role": "user", "content": "卡号4242424242424242，住广东省深圳市南山区科技路100号"}]
    summary = summarize_messages_for_log(messages)
    assert "4242424242424242" not in summary["last_user_preview"]
    assert "科技路" not in summary["last_user_preview"]


def test_content_preview_masked_too() -> None:
    summary = summarize_content_for_log("请联系13800138000")
    assert "13800138000" not in summary["content_preview"]


def test_summarize_messages_basemessage_objects() -> None:
    """检视修复：openai_account 等 client 传 BaseMessage 对象而非 dict。"""
    from openjiuwen.core.foundation.llm.schema.message import UserMessage

    messages = [UserMessage(content="当前轮问题 13800138000")]
    summary = summarize_messages_for_log(messages)
    assert summary["messages_count"] == 1
    assert summary["messages_chars"] > 0
    assert "13800138000" not in summary["last_user_preview"]
    assert "当前轮问题" in summary["last_user_preview"]


def test_safe_len_type_safety() -> None:
    """P1：无 __len__ 的解析结果不抛 TypeError。"""
    from dataclasses import dataclass

    from openjiuwen.core.foundation.llm.payload_summary import safe_len

    @dataclass
    class FakeParsed:
        raw_content: str = "abc"

    assert safe_len(FakeParsed()) == 3
    assert safe_len("hello") == 5
    assert safe_len(None) == 0
    assert safe_len([1, 2]) == 2
    assert safe_len(object()) == 0
