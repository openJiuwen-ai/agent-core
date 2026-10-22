# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""``team_policy`` re-injection after a full compaction.

The leader's collaboration policy is ordinary history, so compaction would
summarize it away. It is recovered verbatim from either form it can take:
the ``build_team`` tool result (F_76) or the ``<team-policy>`` block folded
into a framework-built team's first input (F_122).
"""

from __future__ import annotations

import pytest

from openjiuwen.core.context_engine.processor.compressor.util import (
    STATE_REINJECTION_MARKER,
    build_team_policy_reinjected_messages,
)
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage, UserMessage
from tests.test_logger import logger

_POLICY_BLOCK = "<team-policy>\n## Role\nPlan, never execute.\n</team-policy>"
_FIRST_INPUT = (
    "<team-context>\nmember_name: leader1\n</team-context>\n\n"
    f"{_POLICY_BLOCK}\n\n"
    '<team-event kind="roster">\n(none)\n</team-event>\n\n'
    "Please research the market."
)


def _reinject(messages: list, keep: list) -> list[UserMessage]:
    return build_team_policy_reinjected_messages(None, context=None, messages=messages, messages_to_keep=keep)


@pytest.mark.level0
def test_policy_block_in_first_input_is_recovered_whole():
    messages = [
        UserMessage(content=_FIRST_INPUT),
        AssistantMessage(content="On it."),
        UserMessage(content="status?"),
    ]
    recovered = _reinject(messages, keep=[messages[-1]])
    logger.info("recovered: %s", recovered)
    assert len(recovered) == 1
    content = recovered[0].content
    assert content.startswith(STATE_REINJECTION_MARKER)
    assert content.endswith(_POLICY_BLOCK)
    # Only the element is restored, not the rest of the first input.
    assert "Please research the market." not in content
    assert "<team-context>" not in content


@pytest.mark.level1
def test_policy_block_still_kept_is_not_reinjected():
    first = UserMessage(content=_FIRST_INPUT)
    assert _reinject([first, AssistantMessage(content="ok")], keep=[first]) == []


@pytest.mark.level1
def test_build_team_result_wins_over_a_block():
    """The two forms never coexist in practice; the tool-call form is tried first."""
    call = ToolCall(id="c1", name="build_team", type="function", arguments="{}")
    messages = [
        AssistantMessage(content="", tool_calls=[call]),
        ToolMessage(content="Team created: policy-from-tool", tool_call_id="c1"),
        UserMessage(content=_FIRST_INPUT),
    ]
    recovered = _reinject(messages, keep=[])
    assert len(recovered) == 1
    assert recovered[0].content.endswith("Team created: policy-from-tool")


@pytest.mark.level1
def test_a_previous_reinjection_is_not_recovered_again():
    """A second compaction must not treat the first recovery as the source."""
    messages = [
        UserMessage(content=f"{STATE_REINJECTION_MARKER}\n[TEAM_POLICY]\nrecovered:\n{_POLICY_BLOCK}"),
        AssistantMessage(content="ok"),
    ]
    assert _reinject(messages, keep=[]) == []


@pytest.mark.level1
def test_unterminated_block_is_skipped():
    messages = [UserMessage(content="<team-policy>\nhalf a policy"), AssistantMessage(content="ok")]
    assert _reinject(messages, keep=[]) == []


@pytest.mark.level0
def test_no_policy_anywhere_reinjects_nothing():
    messages = [UserMessage(content="hello"), AssistantMessage(content="hi")]
    assert _reinject(messages, keep=[]) == []
