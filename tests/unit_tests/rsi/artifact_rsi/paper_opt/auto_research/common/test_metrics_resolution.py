"""Plan-aware metric resolution against nested and noisy payloads."""

from __future__ import annotations

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.metrics import (
    compact_metrics,
    compact_metrics_for_manager,
    materialize_handoff_metrics,
    metric_number,
    resolve_metric,
    scientific_status_from_comparison,
    score_number,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_execution.schemas import (
    ExperimentResult,
    VariantResult,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting.evidence import (
    normalize_current_run_evidence,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting.figures import (
    numeric_metric_names,
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


def test_resolve_root_without_wrapper():
    hit = resolve_metric({"accuracy": 0.81}, "accuracy")
    assert hit.status == "resolved"
    assert hit.value == 0.81
    assert hit.path == "accuracy"


def test_resolve_canonical_metrics_mapping():
    hit = resolve_metric({"metrics": {"macro_f1": 0.91}, "n_questions": 16}, "macro_f1")
    assert hit.status == "resolved"
    assert hit.value == 0.91
    assert hit.path == "metrics.macro_f1"


def test_resolve_value_wrapper_and_numeric_string():
    payload = {"metrics": {"accuracy": {"value": "0.75"}}}
    hit = resolve_metric(payload, "accuracy")
    assert hit.status == "resolved"
    assert hit.value == 0.75
    assert metric_number(payload, "accuracy") == 0.75


def test_resolve_deep_nesting_beyond_compact_cap():
    payload = {f"aux_{index}": index for index in range(45)}
    payload["evaluation"] = {"summary": {"accuracy": 0.42}}
    assert "evaluation.summary.accuracy" not in compact_metrics(payload)
    hit = resolve_metric(payload, "accuracy")
    assert hit.status == "resolved"
    assert hit.value == 0.42
    assert hit.path == "evaluation.summary.accuracy"


def test_resolve_metric_inside_list():
    payload = {"rows": [{"id": "a", "score": 0.5}, {"id": "b", "score": 0.5}]}
    hit = resolve_metric(payload, "score")
    assert hit.status == "resolved"
    assert hit.value == 0.5


def test_duplicate_leaf_names_with_conflicting_values_are_ambiguous():
    payload = {"rows": [{"accuracy": 0.5}, {"accuracy": 0.9}]}
    hit = resolve_metric(payload, "accuracy")
    assert hit.status == "ambiguous"
    assert hit.value is None
    assert metric_number(payload, "accuracy") is None


def test_canonical_and_root_conflict_is_ambiguous():
    payload = {"accuracy": 0.5, "metrics": {"accuracy": 0.9}}
    hit = resolve_metric(payload, "accuracy")
    assert hit.status == "ambiguous"
    assert set(hit.candidates) == {"accuracy", "metrics.accuracy"}


def test_missing_endpoint():
    hit = resolve_metric({"status": "ok", "n_questions": 16}, "accuracy")
    assert hit.status == "missing"
    assert hit.value is None


def test_dotted_path_name():
    payload = {"results_table": {"exact_label_accuracy": 1.0}}
    hit = resolve_metric(payload, "results_table.exact_label_accuracy")
    assert hit.status == "resolved"
    assert hit.value == 1.0


def test_materialize_pins_declared_metric_past_compact_limit():
    payload = {f"aux_{index}": index for index in range(45)}
    payload["hidden"] = {"paired_accuracy_difference": 0.0}
    compact, diagnostic = materialize_handoff_metrics(
        payload, plan_metrics=["paired_accuracy_difference"]
    )
    assert compact["paired_accuracy_difference"] == 0.0
    assert diagnostic["resolved"]["paired_accuracy_difference"] == (
        "hidden.paired_accuracy_difference"
    )


def test_manager_compact_pins_declared_name():
    payload = {f"aux_{index}": index for index in range(30)}
    payload["metrics"] = {"macro_f1": 0.91}
    pinned = compact_metrics_for_manager(payload, plan_metrics=["macro_f1"], limit=20)
    assert pinned["macro_f1"] == 0.91


def test_comparison_unknown_when_declared_metric_is_ambiguous():
    status = scientific_status_from_comparison(
        ["accuracy"],
        [
            _variant("zero_shot", {"rows": [{"accuracy": 0.4}, {"accuracy": 0.9}]}),
            _variant("proposed", {"rows": [{"accuracy": 0.4}, {"accuracy": 0.9}]}),
        ],
    )
    assert status == "unknown"


def test_score_number_reads_nested_accuracy():
    assert score_number({"evaluation": {"accuracy": 0.6}}) == 0.6


def test_reporting_uses_resolved_plan_metrics():
    result = ExperimentResult(
        run_id="run",
        workspace_dir="ws",
        status="completed",
        variants=[
            _variant("proposed", {"evaluation": {"summary": {"accuracy": 0.9}}}),
            _variant("zero_shot", {"evaluation": {"summary": {"accuracy": 0.8}}}),
        ],
    )
    names = numeric_metric_names(result, plan_metrics=["accuracy"])
    assert names == ["accuracy"]
    rows = normalize_current_run_evidence(result, plan_metrics=["accuracy"])
    assert {(item.method, item.value) for item in rows} == {
        ("proposed", 0.9),
        ("zero_shot", 0.8),
    }
