"""Plan-aware metric resolution against nested and noisy payloads."""

from __future__ import annotations

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.metrics import (
    compact_metrics,
    compact_metrics_for_manager,
    harness_failed,
    item_failure_rate,
    materialize_handoff_metrics,
    metric_number,
    numeric_metric_values,
    primary_metric_unresolved,
    resolve_metric,
    run_sanity,
    sanitize_diagnostic_payload,
    score_number,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_execution.schemas import (
    ExperimentResult,
    VariantResult,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting import lint
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting.evidence import (
    normalize_current_run_evidence,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting.figures import (
    numeric_metric_names,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting.sections import section_by_id


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


def test_manager_compact_surfaces_custom_primary():
    payload = {f"aux_{index}": index for index in range(30)}
    payload["metrics"] = {"weird_custom_score": 0.42}
    pinned = compact_metrics_for_manager(
        payload, plan_metrics=["weird_custom_score"], limit=20
    )
    assert pinned["weird_custom_score"] == 0.42


def test_compact_skips_unknown_object_lists():
    payload = {
        "accuracy": 0.9,
        "observations": [
            {"id": "a", "prompt": "long prompt text"},
            {"id": "b", "prompt": "another prompt"},
        ],
    }
    compact = compact_metrics(payload)
    assert compact["accuracy"] == 0.9
    dumped = str(compact)
    assert "long prompt text" not in dumped
    assert not any("observations" in str(key) for key in compact)


def test_score_number_reads_nested_plan_metric():
    assert score_number({"evaluation": {"accuracy": 0.6}}, ["accuracy"]) == 0.6
    assert score_number({"evaluation": {"accuracy": 0.6}}) is None


def test_sanitize_drops_object_lists_keeps_short_scalars():
    payload = {
        "answer": "42",
        "user_prompt": [{"role": "user", "content": "secret item text"}],
        "gold_label": [{"id": 1, "text": "gold"}],
        "detail": "x" * 500,
    }
    cleaned = sanitize_diagnostic_payload(payload)
    assert cleaned["answer"] == "42"
    assert "user_prompt" not in cleaned
    assert "gold_label" not in cleaned
    assert cleaned["detail"].endswith("…")
    assert len(cleaned["detail"]) == 400


def test_harness_failed_on_status_or_any_stage():
    assert harness_failed({"status": "failed"})
    assert harness_failed({"status": "completed", "failure_stage": "gpu_init"})
    assert not harness_failed({"status": "completed", "accuracy": 0.8})


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


def _completed_items(*statuses: str) -> dict:
    return {
        "status": "completed",
        "n_questions": len(statuses),
        "method": "proposed",
        "metrics": {"accuracy": 0.5},
        "per_question": [{"status": status} for status in statuses],
    }


def test_primary_metric_unresolved_when_missing():
    issue = primary_metric_unresolved({"status": "completed"}, "accuracy")
    assert issue is not None
    assert issue.reason == "primary_metric_unresolved"


def test_primary_metric_unresolved_accepts_canonical_number():
    assert primary_metric_unresolved({"metrics": {"accuracy": 0.81}}, "accuracy") is None


def test_run_sanity_ok_with_minority_item_failures():
    metrics = _completed_items("ok", "ok", "failed", "ok")
    assert run_sanity(metrics, primary_metric="accuracy") == "ok"
    assert item_failure_rate(metrics) == 0.25


def test_run_sanity_invalid_when_all_item_records_failed():
    metrics = _completed_items("failed", "failed")
    assert run_sanity(metrics, primary_metric="accuracy") == "invalid_run"
    assert item_failure_rate(metrics) == 1.0


def test_run_sanity_invalid_when_primary_missing():
    metrics = {
        "status": "completed",
        "method": "proposed",
        "metrics": {"macro_f1": 0.4},
    }
    assert run_sanity(metrics, primary_metric="accuracy") == "invalid_run"


def _nested_payload() -> dict:
    return {
        "status": "completed",
        "metrics": {"accuracy": 0.6612},
        "budget_match": {"threshold": {"value": 0.95}, "delta_vs_baseline": -0.08},
        "per_question": [{"tokens": 1234.5}, {"tokens": 987.25}],
        "flag": True,
    }


def test_numeric_metric_values_reaches_nested_summaries_but_not_item_records():
    values = numeric_metric_values(_nested_payload())
    assert 0.6612 in values
    assert 0.95 in values
    assert -0.08 in values
    assert 1234.5 not in values
    assert 987.25 not in values
    assert not any(isinstance(value, bool) for value in values)


def test_lint_knows_the_values_the_host_table_renders():
    result = ExperimentResult(
        run_id="r1",
        workspace_dir="",
        variants=[_variant("proposed", _nested_payload())],
        status="completed",
    )
    # The host results table resolves this plan metric from metrics.accuracy ...
    assert numeric_metric_names(result, plan_metrics=["accuracy"]) == ["accuracy"]
    # ... so quoting it (or a nested summary value) in prose must not be flagged.
    text = "The proposed variant reaches 0.6612 accuracy (66.12%), a -0.08 delta at the 0.95 threshold."
    known = lint.known_numbers(result)
    assert lint._extract_unmatched_numbers(text, known) == []
    violations = lint.lint_section(text, section_by_id("experiments"), result)
    assert not [v for v in violations if v.startswith("number(s) not found")]
    # A per-item raw value is still not a reportable result.
    assert lint._extract_unmatched_numbers("one question used 1234.5 tokens", known) == [1234.5]
