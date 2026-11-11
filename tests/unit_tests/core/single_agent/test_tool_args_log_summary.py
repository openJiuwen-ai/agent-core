# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tool-args log summarization tests.

``ReActAgent._summarize_tool_args_for_log`` used to return raw args for
non-browser tools, so the "Executing tool ... with args" INFO log persisted
tool arguments verbatim (observed in production: celia memory_store content
carrying user phone/address). All tools now get the summarized form.
"""

from openjiuwen.core.single_agent.agents.react_agent import ReActAgent


def test_non_browser_tool_dict_args_summarized() -> None:
    result = ReActAgent._summarize_tool_args_for_log(
        "mcp_celia-memory_celia_memory_store",
        {"content": "用户手机号：13800138000。停车位置：某小区 A 区 101 号。"},
    )
    assert result["kind"] == "dict"
    text = str(result)
    assert "13800138000" not in text
    assert "101 号" not in text
    # content 属 text_keys：只留形态
    assert result["redacted_values"]["content"]["redacted"] is True


def test_non_browser_tool_masked_values_preview() -> None:
    result = ReActAgent._summarize_tool_args_for_log(
        "bash",
        {"command": "rm -rf C:\\Users\\Lenovo\\tmp && echo 13800138000", "timeout": 30},
    )
    cmd = result["masked_values"]["command"]
    assert "Lenovo" not in cmd
    assert "13800138000" not in cmd
    assert cmd.startswith("rm -r") and "len=46" in cmd
    assert result["safe_values"]["timeout"] == 30


def test_non_browser_tool_string_args_redacted() -> None:
    result = ReActAgent._summarize_tool_args_for_log("bash", "echo 13800138000")
    assert result["redacted"] is True
    assert "13800138000" not in str(result)


def test_password_key_still_redacted() -> None:
    result = ReActAgent._summarize_tool_args_for_log(
        "any_tool", {"api_password": "secret123"}
    )
    assert result["redacted_values"]["api_password"]["redacted"] is True
    assert "secret123" not in str(result)
