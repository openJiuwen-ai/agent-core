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


class TestRenderToolContentErrorPath:
    """Error output must be truncated/persisted like the data path.

    Failed and timed-out commands can emit unbounded stderr+stdout; the old
    error branch returned the merged stream whole, so every failed result
    entered the context window untruncated (2026-10-09 incident amplifier).
    """

    def _output(self, stdout: str, stderr: str, max_chars: int = 20000):
        from openjiuwen.harness.tools.shell.bash._output import CommandOutput

        return CommandOutput(stdout=stdout, stderr=stderr, exit_code=1, warning=None, max_output_chars=max_chars)

    def test_error_branch_persists_and_truncates(self, tmp_path, monkeypatch) -> None:
        from openjiuwen.harness.tools.shell.bash import _output as output_mod

        monkeypatch.setattr(output_mod, "_OUTPUT_DIR", tmp_path)
        big = "E" * 50_000
        content, is_error = output_mod.render_tool_content(self._output("out" * 100, big), True)
        assert is_error is True
        assert content.startswith("Exit code 1")
        assert "<persisted-output>" in content
        assert len(content) < 5_000
        assert list(tmp_path.iterdir()), "oversized output should be persisted to disk"

    def test_error_branch_small_output_unchanged(self) -> None:
        from openjiuwen.harness.tools.shell.bash._output import render_tool_content

        content, is_error = render_tool_content(self._output("done", "boom"), True)
        assert is_error is True
        assert content == "Exit code 1\nboom\ndone"

    def test_error_branch_zero_disables_truncation(self) -> None:
        from openjiuwen.harness.tools.shell.bash._output import render_tool_content

        big = "E" * 30_000
        content, _ = render_tool_content(self._output("", big, max_chars=0), True)
        assert content == f"Exit code 1\n{big}"

    def test_render_partial_on_failure_truncates_oversized_output(self, tmp_path, monkeypatch) -> None:
        from openjiuwen.harness.tools.shell.bash import _output as output_mod

        monkeypatch.setattr(output_mod, "_OUTPUT_DIR", tmp_path)
        content = output_mod.render_partial_on_failure(
            self._output("x" * 40_000, ""), "Tool 'bash' timed out after 5.0s"
        )
        assert content.startswith("Tool 'bash' timed out after 5.0s\nExit code 1")
        assert "<persisted-output>" in content
        assert len(content) < 5_000

    def test_warning_prepended_outside_persisted_block(self, tmp_path, monkeypatch) -> None:
        from openjiuwen.harness.tools.shell.bash import _output as output_mod

        monkeypatch.setattr(output_mod, "_OUTPUT_DIR", tmp_path)
        output = self._output("", "E" * 40_000)
        output = output.__class__(
            stdout=output.stdout,
            stderr=output.stderr,
            exit_code=output.exit_code,
            warning="destructive command executed",
            max_output_chars=output.max_output_chars,
        )
        content, _ = output_mod.render_tool_content(output, True)
        assert content.startswith("destructive command executed")
        assert "<persisted-output>" in content
