"""The command-line output helper: one line per call, on the stream it was given, exactly as written."""

import io
import sys

from openjiuwen.agent_evolving.reward.console import line_writer
from openjiuwen.agent_evolving.reward.evidence_agent import SECONDS, Limits


def test_line_writer_writes_the_message_and_a_newline_to_its_stream() -> None:
    stream = io.StringIO()
    write = line_writer(stream)
    write("first")
    write("second 100% {done} %s")   # no placeholder is ever filled in
    assert stream.getvalue() == "first\nsecond 100% {done} %s\n"


def test_line_writer_streams_are_independent_and_do_not_pile_up(capsys) -> None:
    for _ in range(3):   # a script that builds its writer again must not print each line several times
        line_writer(sys.stderr)("progress")
    line_writer(sys.stdout)("report")
    captured = capsys.readouterr()
    assert captured.err == "progress\n" * 3
    assert captured.out == "report\n"


def test_line_writer_keeps_non_ascii_text() -> None:
    stream = io.StringIO()
    line_writer(stream)("总评：第 2 页少了一只猫")
    assert stream.getvalue() == "总评：第 2 页少了一只猫\n"


def test_limits_seconds_is_a_float_by_default() -> None:
    assert isinstance(Limits().seconds, float)
    assert Limits().seconds == SECONDS
