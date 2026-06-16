import json

from examples.context_engine.rule_compression_fixtures import SCENARIOS
from openjiuwen.core.context_engine.processor.offloader.rules import (
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
