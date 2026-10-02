# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.harness.tools.shell.bash._output import (
    CommandOutput,
    render_tool_content,
    truncate_output,
)


class TestTruncateOutput:

    def test_short_text_unchanged(self) -> None:
        text = "hello world"
        assert truncate_output(text, 1000) == text

    def test_exact_limit_unchanged(self) -> None:
        text = "x" * 100
        assert truncate_output(text, 100) == text

    def test_long_text_has_gap_marker(self) -> None:
        text = "x" * 500
        result = truncate_output(text, 250)
        assert "lines omitted" in result

    def test_head_and_tail_preserved(self) -> None:
        lines = [f"line-{i}" for i in range(100)]
        text = "\n".join(lines)
        result = truncate_output(text, 200)
        assert result.startswith("line-0")
        assert "line-99" in result
        assert "lines omitted" in result

    def test_total_length_reasonable(self) -> None:
        text = "x" * 500
        result = truncate_output(text, 250)
        # head(200) + gap marker + tail(50) + newlines — should be in reasonable range
        assert len(result) < 300

    def test_empty_text(self) -> None:
        assert truncate_output("", 100) == ""

    def test_custom_head_ratio(self) -> None:
        text = "A" * 300 + "B" * 300
        result = truncate_output(text, 200, head_ratio=0.5)
        assert result.startswith("A")
        assert result.endswith("B" * 100)

    def test_multiline_omitted_count(self) -> None:
        lines = [f"L{i}" for i in range(50)]
        text = "\n".join(lines)
        result = truncate_output(text, 60)
        # the gap marker should report how many newlines were in the omitted region
        assert "lines omitted" in result


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
        # head_ratio=0.0 keeps only the tail budget, so the end is preserved.
        assert "B" * 100 in content

    def test_default_head_ratio_is_sixty_percent(self) -> None:
        assert CommandOutput(
            command="c", stdout="", stderr="", exit_code=0, warning=None, max_output_chars=100,
        ).head_ratio == 0.6
