import json

from examples.context_engine.rule_compression_fixtures import SCENARIOS
from openjiuwen.core.context_engine.processor.offloader.rules import (
    ContentType,
    RuleContentRouter,
    RuleContext,
)


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


def test_numbered_python_source_is_routed_to_source_code_compressor():
    content = "\n".join(
        [
            "     1\tfrom __future__ import annotations",
            "     2\t",
            "     3\tdef compute_value(base: int) -> int:",
            "     4\t    total = base",
            "     5\t    for step in range(20):",
            "     6\t        total += step",
            "     7\t        if total % 7 == 0:",
            "     8\t            total += 1",
            "     9\t    return total",
        ]
    )

    result = RuleContentRouter().compress(
        content,
        RuleContext(
            max_tokens=1600,
            source_min_lines=1,
            source_max_body_lines=2,
            min_savings_ratio=0.0,
            count_tokens=lambda text: max(len(text) // 3, 1),
        ),
    )

    assert result.content_type == ContentType.SOURCE_CODE
    assert result.modified is True
    assert "     1\tfrom" not in result.content
    assert "def compute_value" in result.content
    assert "# [function body omitted; reload original source for details]" in result.content


def test_numbered_python_source_with_diff_markers_is_not_routed_as_git_diff():
    content = "\n".join(
        [
            "     1\timport re",
            "     2\t",
            "     3\tDIFF_HUNK_RE = re.compile(r'(?m)^@@ .+ @@')",
            "     4\t",
            "     5\tdef parse_diff_marker(value: str) -> bool:",
            "     6\t    if 'diff --git ' in value:",
            "     7\t        return True",
            "     8\t    if DIFF_HUNK_RE.search(value):",
            "     9\t        return True",
            "    10\t    return False",
        ]
    )

    assert RuleContentRouter().detect(content) == ContentType.SOURCE_CODE
