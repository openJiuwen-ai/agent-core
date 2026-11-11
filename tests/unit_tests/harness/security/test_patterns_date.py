# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Date format arguments must match without bypassing command approvals.

匹配器与兼容策略判定链路均由 core 本地实现。
"""

import pytest

from openjiuwen.harness.security.patterns import match_wildcard

_DATE_COMMAND = 'date "+%Y-%m-%d %H:%M:%S"'


@pytest.mark.parametrize("command", ["date +X", "date %Y", "date +%s", _DATE_COMMAND])
def test_wildcard_matches_formatted_date(command: str) -> None:
    assert match_wildcard(command, "date *")


def test_question_mark_matches_date_format_character() -> None:
    assert match_wildcard("date +%", "date +?")
    assert not match_wildcard("date +%Y", "date +?")
