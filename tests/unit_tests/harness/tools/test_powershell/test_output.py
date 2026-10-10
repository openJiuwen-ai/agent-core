# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.harness.tools.shell.powershell._output import (
    CommandOutput,
    render_tool_content,
    truncate_output,
)


def _command_output(stdout: str, **overrides: object) -> CommandOutput:
    """Build a CommandOutput with test defaults for rendering assertions."""
    params = {
        "command": "cmd",
        "stdout": stdout,
        "stderr": "",
        "exit_code": 0,
        "warning": None,
        "max_output_chars": 1000,
    }
    params.update(overrides)
    return CommandOutput(**params)


class TestTruncateOutput:

    def test_long_text_has_gap_marker(self) -> None:
        assert "lines omitted" in truncate_output("x" * 500, 250)

    def test_head_and_tail_preserved(self) -> None:
        lines = [f"line-{i}" for i in range(100)]
        result = truncate_output("\n".join(lines), 200)
        assert result.startswith("line-0")
        assert "line-99" in result


class TestRenderToolContent:

    def test_oversized_output_uses_head_and_tail(self) -> None:
        text = "HEAD-MARKER" + "a" * 5000 + "b" * 5000 + "TAIL-MARKER"
        content, _ = render_tool_content(_command_output(text), False)
        assert "<persisted-output>" in content
        assert "Head+tail preview:" in content
        assert "HEAD-MARKER" in content
        assert "TAIL-MARKER" in content

    def test_small_output_is_inlined(self) -> None:
        content, _ = render_tool_content(_command_output("hello world"), False)
        assert "<persisted-output>" not in content
        assert "hello world" in content

    def test_head_ratio_controls_split(self) -> None:
        text = "A" * 1000 + "B" * 1000
        content, _ = render_tool_content(_command_output(text, max_output_chars=200, head_ratio=0.0), False)
        assert "B" * 100 in content
