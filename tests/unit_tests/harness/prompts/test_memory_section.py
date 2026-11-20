# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Memory prompt rules: tool failures must not become persisted "facts".

Regression: an agent recorded "open_pptx is stably unavailable, deliver HTML
from now on" into memory after a transient desktop failure. Daily memory is
auto-loaded into later sessions, so every later PPT task skipped the native
flow and delivered HTML instead of PPTX.
"""
from __future__ import annotations

import pytest

from openjiuwen.harness.prompts.sections.memory import build_memory_section


def _text(language: str, is_proactive: bool) -> str:
    section = build_memory_section(language=language, is_proactive=is_proactive)
    return section.content[language]


@pytest.mark.parametrize("is_proactive", [True, False])
def test_cn_forbids_recording_tool_failures_and_acting_on_them(is_proactive):
    text = _text("cn", is_proactive)
    assert "不要把工具调用失败、超时、服务或运行环境暂时不可用当作稳定事实写入记忆" in text
    assert "不能作为跳过工具调用、跳过技能标准流程或改变交付格式的依据" in text


@pytest.mark.parametrize("is_proactive", [True, False])
def test_en_forbids_recording_tool_failures_and_acting_on_them(is_proactive):
    text = _text("en", is_proactive)
    assert "Do not record tool-call failures, timeouts, or temporarily unavailable services" in text
    assert "never use them as a reason to skip a tool call, skip a skill's standard flow" in text


# The ban must not contradict "an explicit user request to remember wins": such a
# request is still honored, but stored as the user's dated instruction rather than
# the agent's own conclusion, and it never silently overrides the current request.
@pytest.mark.parametrize("is_proactive", [True, False])
def test_cn_explicit_user_request_is_an_exception(is_proactive):
    text = _text("cn", is_proactive)
    assert "例外：用户明确要求记住这类内容时照常写入，但要写成带日期的用户指示" in text
    assert "不要写成你自己得出的结论" in text
    assert "它与本次请求不一致时以本次请求为准，需要因此改变工具或交付格式时先向用户确认" in text


@pytest.mark.parametrize("is_proactive", [True, False])
def test_en_explicit_user_request_is_an_exception(is_proactive):
    text = _text("en", is_proactive)
    assert "Exception: if the user explicitly asks you to remember such content, record it" in text
    assert "not as your own conclusion" in text
    assert "the current request wins, and confirm with the user before it changes the tools" in text
