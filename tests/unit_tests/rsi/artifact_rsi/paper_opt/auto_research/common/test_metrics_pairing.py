"""Host pairing of subset experiment rows for scientific status."""

from __future__ import annotations

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.metrics import (
    infer_proposed_name,
    overlay_paired_metrics,
    scientific_status_from_comparison,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_execution.schemas import (
    VariantResult,
)


def _variant(name: str, metrics: dict, *, status: str = "completed") -> VariantResult:
    return VariantResult(
        name=name,
        metrics=metrics,
        exit_code=0,
        log_path="",
        process_status=status,  # type: ignore[arg-type]
        metrics_state="present",
    )


def test_infer_proposed_prefers_metric_prefix_over_zero_shot():
    assert (
        infer_proposed_name(
            ["zero_shot", "one_shot", "icl_1shot"],
            metric_names=["one_shot_accuracy_gain"],
        )
        == "one_shot"
    )


def test_overlay_leaves_unpaired_row_indeterminate():
    rows = overlay_paired_metrics(
        [_variant("one_shot", {"metrics": {"accuracy": 1.0, "one_shot_accuracy_gain": None}})],
        ["one_shot_accuracy_gain"],
    )
    assert rows[0].metrics["metrics"]["one_shot_accuracy_gain"] is None


def test_overlay_computes_gain_from_zero_shot_sibling():
    rows = overlay_paired_metrics(
        [
            _variant(
                "zero_shot",
                {"metrics": {"accuracy": 0.75, "one_shot_accuracy_gain": None}},
            ),
            _variant(
                "one_shot",
                {"metrics": {"accuracy": 1.0, "one_shot_accuracy_gain": None}},
            ),
        ],
        ["one_shot_accuracy_gain"],
    )
    names = {item.name: item for item in rows}
    assert names["one_shot"].metrics["one_shot_accuracy_gain"] == 0.25
    assert names["one_shot"].metrics["metrics"]["one_shot_accuracy_gain"] == 0.25
    assert names["zero_shot"].metrics["metrics"]["one_shot_accuracy_gain"] is None


def test_overlay_uses_plan_baselines_not_method_vocabulary():
    rows = overlay_paired_metrics(
        [
            _variant("control", {"metrics": {"accuracy": 0.5, "delta": None}}),
            _variant("treatment", {"metrics": {"accuracy": 0.75, "delta": None}}),
        ],
        ["delta"],
        baselines=["control"],
    )
    names = {item.name: item for item in rows}
    assert names["treatment"].metrics["delta"] == 0.25
    assert names["control"].metrics["metrics"]["delta"] is None


def test_overlay_skips_when_sibling_deltas_disagree():
    rows = overlay_paired_metrics(
        [
            _variant("a", {"metrics": {"accuracy": 0.5, "delta": None}}),
            _variant("b", {"metrics": {"accuracy": 0.75, "delta": None}}),
            _variant("c", {"metrics": {"accuracy": 1.0, "delta": None}}),
        ],
        ["delta"],
    )
    names = {item.name: item for item in rows}
    assert names["c"].metrics["metrics"]["delta"] is None



def test_comparison_unknown_until_both_methods_exist():
    assert (
        scientific_status_from_comparison(
            ["one_shot_accuracy_gain"],
            [_variant("one_shot", {"metrics": {"accuracy": 1.0}})],
        )
        == "unknown"
    )


def test_comparison_below_threshold_on_tied_accuracy():
    assert (
        scientific_status_from_comparison(
            ["one_shot_accuracy_gain", "valid_prediction_coverage"],
            [
                _variant(
                    "zero_shot",
                    {
                        "metrics": {
                            "accuracy": 1.0,
                            "valid_prediction_coverage": 1.0,
                            "one_shot_accuracy_gain": None,
                        }
                    },
                ),
                _variant(
                    "one_shot",
                    {
                        "metrics": {
                            "accuracy": 1.0,
                            "valid_prediction_coverage": 1.0,
                            "one_shot_accuracy_gain": None,
                        }
                    },
                ),
            ],
        )
        == "below_threshold"
    )


def test_comparison_accepted_when_gain_is_positive():
    assert (
        scientific_status_from_comparison(
            ["one_shot_accuracy_gain"],
            [
                _variant("zero_shot", {"metrics": {"accuracy": 0.5}}),
                _variant("one_shot", {"metrics": {"accuracy": 1.0}}),
            ],
        )
        == "accepted"
    )


def test_named_proposed_still_compares_shared_metric():
    assert (
        scientific_status_from_comparison(
            ["accuracy"],
            [
                _variant("baseline", {"accuracy": 0.8}),
                _variant("proposed", {"accuracy": 0.9}),
            ],
        )
        == "accepted"
    )
    assert (
        scientific_status_from_comparison(
            ["accuracy"],
            [
                _variant("baseline", {"accuracy": 0.9}),
                _variant("proposed", {"accuracy": 0.8}),
            ],
        )
        == "below_threshold"
    )


def test_overlay_fills_gain_from_nested_accuracy():
    rows = overlay_paired_metrics(
        [
            _variant("zero_shot", {"evaluation": {"accuracy": 0.5}}),
            _variant("one_shot", {"evaluation": {"accuracy": 0.75}}),
        ],
        ["one_shot_accuracy_gain"],
    )
    names = {item.name: item for item in rows}
    assert names["one_shot"].metrics["one_shot_accuracy_gain"] == 0.25


def test_comparison_unknown_on_unresolved_declared_metric():
    assert (
        scientific_status_from_comparison(
            ["accuracy"],
            [
                _variant("zero_shot", {"rows": [{"accuracy": 0.2}, {"accuracy": 0.9}]}),
                _variant("proposed", {"rows": [{"accuracy": 0.2}, {"accuracy": 0.9}]}),
            ],
        )
        == "unknown"
    )
