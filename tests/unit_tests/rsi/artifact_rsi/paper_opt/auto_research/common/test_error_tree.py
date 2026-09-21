"""Python traceback extraction for failed smoke/execution runs."""

from __future__ import annotations

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.error_tree import (
    exception_banner,
    python_error_tree,
)

_INNER = (
    "Traceback (most recent call last):\n"
    '  File "run.py", line 10, in <module>\n'
    "    main()\n"
    '  File "run.py", line 142, in main\n'
    '    raise ValueError("paired_discordance")\n'
    "ValueError: paired_discordance\n"
)

_CHAINED = (
    "Traceback (most recent call last):\n"
    '  File "run.py", line 1, in <module>\n'
    '    raise KeyError("x")\n'
    "KeyError: 'x'\n"
    "\n"
    "The above exception was the direct cause of the following exception:\n"
    "\n"
    "Traceback (most recent call last):\n"
    '  File "run.py", line 3, in <module>\n'
    '    raise ValueError("wrapped") from e\n'
    "ValueError: wrapped\n"
)


def test_python_error_tree_keeps_traceback_and_drops_sdk_noise():
    blob = (
        '{"event_type": "llm_call_end", "module_type": "llm", '
        "\"metadata\": {\"response\": \"ChatCompletion(id='x')\"}}\n"
        + _INNER
    )
    tree = python_error_tree(blob)
    assert tree.startswith("Traceback (most recent call last):")
    assert "ValueError: paired_discordance" in tree
    assert 'File "run.py", line 142' in tree
    assert "ChatCompletion" not in tree
    assert "event_type" not in tree


def test_python_error_tree_keeps_last_chained_block():
    tree = python_error_tree(_CHAINED)
    assert "ValueError: wrapped" in tree
    assert "KeyError" not in tree
    assert "line 3" in tree


def test_python_error_tree_returns_empty_when_no_traceback():
    assert python_error_tree("metrics contract failed: n_questions") == ""
    assert python_error_tree("") == ""


def test_exception_banner_is_type_message_and_last_frame():
    banner = exception_banner(_INNER)
    assert banner == "ValueError: paired_discordance in run.py:142"
    assert "Traceback" not in banner
    assert "main()" not in banner
