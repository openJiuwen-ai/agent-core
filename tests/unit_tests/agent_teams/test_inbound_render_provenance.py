# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Which member inputs are somebody speaking and which are runtime context."""

from __future__ import annotations

import pytest

from openjiuwen.agent_teams.inbound_render import (
    INBOUND_TYPE_DIRECT,
    is_runtime_context_only,
    render_event,
    render_inbound,
    render_team_context,
)
from tests.test_logger import logger


def _inbound() -> str:
    return render_inbound(
        content="请介绍一下你自己",
        sender="team-leader",
        message_id="m-1",
        msg_type=INBOUND_TYPE_DIRECT,
        time_info="just now",
    )


@pytest.mark.level1
def test_runtime_context_is_not_somebody_speaking() -> None:
    board = render_event(kind="task-board", body="- [t-1] [pending] write the parser")
    context = render_team_context(body="# 成员身份\n你的 member_name: coder")
    logger.info("board: {}", board[:60])
    assert is_runtime_context_only(board)
    assert is_runtime_context_only(context)


@pytest.mark.level1
def test_a_delivered_message_is_somebody_speaking() -> None:
    assert not is_runtime_context_only(_inbound())
    # Context riding along with a message does not make the message context.
    assert not is_runtime_context_only(render_team_context(body="team state") + "\n\n" + _inbound())


@pytest.mark.level1
def test_an_unrecognized_input_counts_as_somebody_speaking() -> None:
    # Losing a user's turn is worse than showing context as one.
    assert not is_runtime_context_only("list the files")
    assert not is_runtime_context_only("")
