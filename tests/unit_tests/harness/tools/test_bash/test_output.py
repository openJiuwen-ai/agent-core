# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.harness.tools.shell.bash._output import truncate_output


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
