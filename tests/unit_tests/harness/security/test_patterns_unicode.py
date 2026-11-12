# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unicode command rules must preserve approval and path guard boundaries.

匹配器与兼容策略判定链路均由 core 本地实现。
"""

import pytest

from openjiuwen.harness.security.patterns import match_wildcard

_CUSTOMER_COMMANDS = [
    'python3 scripts/build_modify_request.py --original-meet-title "测试会议"',
    ('python3 "/workspace/skills/会议预定/build_meeting_payload.py" --meet-title "AAA" --meet-host-name "张三"'),
]


@pytest.mark.parametrize(
    "command",
    _CUSTOMER_COMMANDS
    + [
        "python3 /workspace/test.py",
        "python3 /workspace/𠀀.py",
    ],
)
def test_python_rule_matches_unicode_paths_and_arguments(command: str) -> None:
    assert match_wildcard(command, "python3 *")


def test_question_mark_matches_one_chinese_character() -> None:
    assert match_wildcard("python3 /workspace/会.py", "python3 /workspace/?.py")
    assert not match_wildcard("python3 /workspace/会议.py", "python3 /workspace/?.py")


@pytest.mark.parametrize(
    "suffix",
    [
        "; id",
        " | id",
        " && id",
        " || id",
        " & id",
        " `id`",
        " $(id)",
        " > /tmp/result",
        " < /tmp/input",
        "\nid",
        "\r",
        "\t",
        "\x00",
        "‮",
    ],
)
def test_unicode_wildcard_still_rejects_shell_syntax_and_controls(suffix: str) -> None:
    assert not match_wildcard('python3 "/workspace/会议.py"' + suffix, "python3 *")
