import json

from openjiuwen.core.context_engine.processor.offloader.rule_compression import (
    ContentType,
    RuleContentRouter,
    RuleContext,
)
from tests.unit_tests.core.context_engine.rule_compression_fixtures import SCENARIOS


def test_probe_scenarios_exercise_each_rule_compressor():
    router = RuleContentRouter()
    context = RuleContext(
        max_tokens=1600,
        count_tokens=lambda text: max(len(text) // 3, 1),
    )

    for scenario in SCENARIOS:
        content = scenario.build_content()
        result = router.compress(content, context)

        assert scenario.expect_modified, scenario.name
        assert result.content_type == scenario.content_type, scenario.name
        assert result.modified, scenario.name
        assert len(result.content) < len(content), scenario.name
        if scenario.expect_marker:
            assert scenario.expect_marker in result.content, scenario.name


def test_json_probe_preserves_low_cardinality_value_counts():
    scenario = next(item for item in SCENARIOS if item.name == "json_array")
    result = RuleContentRouter().compress(
        scenario.build_content(),
        RuleContext(
            max_tokens=1600,
            count_tokens=lambda text: max(len(text) // 3, 1),
        ),
    )

    summary = json.loads(result.content)[-1]["_summary"]

    assert summary["total_rows"] == 120
    assert summary["value_counts"]["status"] == {"active": 96, "error": 24}


def test_numbered_json_array_is_routed_to_json_array_compressor():
    scenario = next(item for item in SCENARIOS if item.name == "json_array")
    content = "\n".join(
        f"{line_number:6}\t{line}"
        for line_number, line in enumerate(scenario.build_content().splitlines(), 1)
    )

    result = RuleContentRouter().compress(
        content,
        RuleContext(
            max_tokens=1600,
            count_tokens=lambda text: max(len(text) // 3, 1),
        ),
    )

    assert result.content_type == ContentType.JSON_ARRAY
    assert result.modified is True
    assert "_omitted" in result.content
    assert "     1\t[" not in result.content


def test_space_numbered_json_array_is_routed_to_json_array_compressor():
    scenario = next(item for item in SCENARIOS if item.name == "json_array")
    content = "\n".join(
        f"{line_number} {line}"
        for line_number, line in enumerate(scenario.build_content().splitlines(), 1)
    )

    result = RuleContentRouter().compress(
        content,
        RuleContext(
            max_tokens=1600,
            count_tokens=lambda text: max(len(text) // 3, 1),
        ),
    )

    assert result.content_type == ContentType.JSON_ARRAY
    assert result.modified is True
    assert "_omitted" in result.content
    assert "1 [" not in result.content


def test_truncated_space_numbered_json_array_is_routed_to_json_array_compressor():
    scenario = next(item for item in SCENARIOS if item.name == "json_array")
    pretty_content = json.dumps(json.loads(scenario.build_content()), ensure_ascii=False, indent=2)
    lines = pretty_content.splitlines()[:-3]
    content = "\n".join(f"{line_number} {line}" for line_number, line in enumerate(lines, 1))

    result = RuleContentRouter().compress(
        content,
        RuleContext(
            max_tokens=1600,
            count_tokens=lambda text: max(len(text) // 3, 1),
        ),
    )

    assert result.content_type == ContentType.JSON_ARRAY
    assert result.modified is True
    assert "_omitted" in result.content


def test_article_text_with_failure_words_is_not_routed_as_log():
    content = "\n".join(
        [
            "URL: https://example.test/news/world",
            "Middle East leaders warned that negotiations could fail without a durable ceasefire.",
            "Analysts described the failure of previous talks as a warning sign for civilians.",
            "The report includes background, regional reactions, and humanitarian updates.",
        ]
        * 20
    )

    assert RuleContentRouter().detect(content) == ContentType.PLAIN_TEXT


def test_pytest_output_is_routed_as_log():
    content = "\n".join(
        [
            "============================= test session starts =============================",
            "platform win32 -- Python 3.11.13, pytest-9.0.2",
            "collected 3 items",
            "tests/unit/test_demo.py::test_ok PASSED",
            "tests/unit/test_demo.py::test_bad FAILED",
            "================================== FAILURES ===================================",
            "FAILED tests/unit/test_demo.py::test_bad - AssertionError: boom",
        ]
    )

    assert RuleContentRouter().detect(content) == ContentType.LOG


def test_prefixed_pytest_output_is_routed_as_log():
    content = "\n".join(
        f"{line_number:6}\t{line}"
        for line_number, line in enumerate(
            [
                "============================= test session starts =============================",
                "platform win32 -- Python 3.11.13, pytest-9.0.2",
                "collected 3 items",
                "tests/unit/test_demo.py::test_ok PASSED",
                "tests/unit/test_demo.py::test_bad FAILED",
                "================================== FAILURES ===================================",
                "FAILED tests/unit/test_demo.py::test_bad - AssertionError: boom",
            ],
            1,
        )
    )

    assert RuleContentRouter().detect(content) == ContentType.LOG


def test_prefixed_pytest_output_with_source_snippets_prefers_log():
    content = "\n".join(
        f"{line_number:6}\t{line}"
        for line_number, line in enumerate(
            [
                "============================= test session starts =============================",
                "platform win32 -- Python 3.11.13, pytest-9.0.2",
                "collected 1 item",
                "tests/unit/test_demo.py::test_bad FAILED",
                "================================== FAILURES ===================================",
                "FAILED tests/unit/test_demo.py::test_bad - AssertionError: boom",
                "from package.module import function_under_test",
                "def helper():",
                "    return function_under_test()",
                "=========================== short test summary info ===========================",
                "1 failed in 0.01s",
            ],
            1,
        )
    )

    result = RuleContentRouter().compress(
        "\n".join([content] * 20),
        RuleContext(
            max_tokens=1600,
            count_tokens=lambda text: max(len(text) // 3, 1),
        ),
    )

    assert result.content_type == ContentType.LOG
    assert result.modified is True
    assert result.content.startswith("[LOG compressed: format=pytest")
    assert "[LOG omitted:" in result.content


def test_decorated_prefixed_pytest_output_is_routed_as_log():
    content = "\n".join(
        f"| {line_number} | {line}"
        for line_number, line in enumerate(
            [
                "============================= test session starts =============================",
                "platform win32 -- Python 3.11.13, pytest-9.0.2",
                "collected 1 item",
                "tests/unit/test_demo.py::test_bad FAILED",
                "================================== FAILURES ===================================",
                "FAILED tests/unit/test_demo.py::test_bad - AssertionError: boom",
            ],
            1,
        )
    )

    assert RuleContentRouter().detect(content) == ContentType.LOG


def test_pytest_output_with_embedded_diff_is_routed_as_log():
    content = "\n".join(
        [
            "============================= test session starts =============================",
            "platform win32 -- Python 3.11.13, pytest-9.0.2",
            "collected 1 item",
            "tests/unit/test_demo.py::test_bad FAILED",
            "================================== FAILURES ===================================",
            "FAILED tests/unit/test_demo.py::test_bad - AssertionError: boom",
            "Captured stdout call",
            "diff --git a/pkg/demo.py b/pkg/demo.py",
            "index 1111111..2222222 100644",
            "--- a/pkg/demo.py",
            "+++ b/pkg/demo.py",
            "@@ -1,2 +1,2 @@",
            "-old",
            "+new",
            "=========================== short test summary info ===========================",
            "1 failed in 0.01s",
        ]
    )

    assert RuleContentRouter().detect(content) == ContentType.LOG


def test_structured_application_log_is_routed_as_log():
    content = "\n".join(
        [
            "2026-06-19 00:00:01 INFO service started",
            "2026-06-19 00:00:02 WARNING retrying connector after timeout",
            "2026-06-19 00:00:03 ERROR request failed with status 500",
            "2026-06-19 00:00:04 INFO service recovered",
        ]
        * 20
    )

    assert RuleContentRouter().detect(content) == ContentType.LOG


def test_agent_trace_output_is_routed_as_log():
    content = "\n".join(
        [
            "Planning next action for user request: analyze context offloader behavior. iteration=0",
            "Tool call requested: search repository for relevant symbols. iteration=0",
            "Tool call completed: search repository for relevant symbols. durationMs=123",
            "Tool result received: 20 matches",
        ]
        * 40
    )

    result = RuleContentRouter().compress(
        content,
        RuleContext(
            max_tokens=1600,
            count_tokens=lambda text: max(len(text) // 3, 1),
        ),
    )

    assert result.content_type == ContentType.LOG
    assert result.modified is True
    assert result.content.startswith("[LOG compressed: format=generic")
    assert "[LOG omitted:" in result.content


def test_prefixed_git_diff_is_routed_and_compressed_as_git_diff():
    hunk = [
        "diff --git a/pkg/demo.py b/pkg/demo.py",
        "index 1111111..2222222 100644",
        "--- a/pkg/demo.py",
        "+++ b/pkg/demo.py",
        "@@ -1,80 +1,80 @@",
        " def unchanged_before():",
        "     return 1",
        *[f" context line {index}" for index in range(40)],
        "-old_value = 1",
        "+new_value = 2",
        *[f" more context {index}" for index in range(40)],
    ]
    content = "\n".join(f"{line_number:6}\t{line}" for line_number, line in enumerate(hunk, 1))

    result = RuleContentRouter().compress(
        content,
        RuleContext(
            max_tokens=1600,
            diff_min_lines=1,
            diff_max_context_lines=1,
            count_tokens=lambda text: max(len(text) // 3, 1),
        ),
    )

    assert result.content_type == ContentType.GIT_DIFF
    assert result.modified is True
    assert "diff --git a/pkg/demo.py b/pkg/demo.py" in result.content
    assert "     1\t" not in result.content
    assert "unchanged/context diff lines omitted" in result.content
