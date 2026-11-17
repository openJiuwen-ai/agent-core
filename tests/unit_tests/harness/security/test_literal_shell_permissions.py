# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Literal shell parsing must not accept disallowed shell structures.

判定引擎已下沉到 agent-security；本文件只保留对 ``parse_literal_shell`` 结构识别
的回归覆盖，策略判定（allow/ask/deny）由 security 侧测试覆盖。
"""

import os

import pytest
from agent_shield.tool_permission.fileguard import unix_bash_literal as literal_shell

pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="literal shell parsing is disabled on Windows",
)


@pytest.mark.parametrize("command", [
    "cd ./ || python3 probe.py <<'EOF'\n{}\nEOF",
    "cd ./ && python3 - <<'EOF'\nprint(1)\nEOF",
    "cd ./ && python3 -c 'print(1)' <<'EOF'\n{}\nEOF",
    "cd ./ && python3 probe.py <<EOF\n$(id)\nEOF",
    "cd ./ && python3 probe.py <<'EOF'\n{}\nEOF\ntouch marker",
    "cd ./ && python3 probe.py > marker <<'EOF'\n{}\nEOF",
    "cd ./ && python3 probe.py <<'EOF'\n{}\nEOF\n| cat",
    "cd ./ && cd ../ && python3 probe.py <<'EOF'\n{}\nEOF",
    "echo prefix && python3 probe.py <<'EOF'\n{}\nEOF",
    'cd "${DEST}" && python3 probe.py <<\'EOF\'\n{}\nEOF',
])
def test_cd_heredoc_does_not_accept_other_shell_structures(command):
    assert literal_shell.parse_literal_shell(command) is None


@pytest.mark.parametrize("command", [
    'python3.12 -c "print(1)"', 'nodejs -e "console.log(1)"', 'dash -c "echo ok"',
])
def test_interpreter_aliases_do_not_gain_literal_evidence(command):
    assert literal_shell.parse_literal_shell(command) is None
