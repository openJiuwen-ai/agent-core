"""Helpers for preparing experiment metrics for agent handoffs."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_execution.schemas import VariantResult


_SKIP_METRIC_KEYS = frozenset(
    {
        "records",
        "task_records",
        "prompts",
        "paired_item_deltas",
        "planner_ledger",
        "posthoc",
        "configuration",
        "planner_hop2_failure_analysis",
        "ordered_page_ids",
        "distinct_page_ids",
        "validations",
    }
)
_MAX_COMPACT_METRICS = 40
_MAX_METRIC_STRING = 120
_MAX_EVENT_CLASSES = 12
_MAX_EXAMPLE_TASKS = 3
_BACKTICK_IDENT = re.compile(r"`([a-z][a-z0-9_]{2,})`")
_SKIP_BASELINE_NAMES = frozenset(
    {
        "proposed",
        "all",
        "run",
        "output",
        "method",
        "status",
        "accuracy",
        "create_deep_agent",
        "reactagent",
        "api_key",
        "api_base",
        "model_name",
    }
)
_ACCEPTED_STATUSES = frozenset({"accepted"})
_FAILED_STATUSES = frozenset({"non_acceptable", "diagnostic_failed", "failed"})
_HARNESS_FAILED_STATUSES = frozenset({"failed", "diagnostic_failed"})
_COMPLETED_RUN_STATUSES = frozenset(
    {
        "completed",
        "ok",
        "success",
        "succeeded",
        "accepted",
        "non_acceptable",
        "completed_live",
    }
)
_ITEM_RECORD_KEYS = ("per_question", "task_records", "records")
_EXCEPTION_FAILURE_RE = re.compile(r"^[A-Za-z]+(?:Error|Exception)\s*:")
_INFRA_STAGES = frozenset(
    {
        "dataset_download",
        "agent_init",
        "tool_call",
        "metrics_write",
        "runtime_setup",
    }
)
_INFRA_ACQUISITION = frozenset({"dataset_acquisition_failure"})
_UNSAFE_DIAGNOSTIC_KEYS = frozenset(
    {
        "failure_prefix",
        "prefix",
        "body",
        "content",
        "csv",
        "problem",
        "answer",
        "canary",
        "prompt",
        "prompts",
        "raw",
        "decrypted",
        "records",
        "task_records",
        "explanation",
    }
)
_SCORE_LEAVES = frozenset(
    {
        "accuracy",
        "exact_match",
        "f1",
        "score",
        "pass_rate",
        "success_rate",
        "semantic_correct",
    }
)


def infer_baseline_names(*texts: str) -> list[str]:
    """Pull `--method` baseline names out of task/design text.

    Looks for backtick-quoted snake_case identifiers ending in `_baseline` or
    `_comparator` so the host can actually invoke every named variant.
    """
    found: list[str] = []
    seen: set[str] = set()
    blob = "\n".join(item for item in texts if item)
    for name in _BACKTICK_IDENT.findall(blob):
        if name in _SKIP_BASELINE_NAMES or name in seen:
            continue
        if name.endswith("_baseline") or name.endswith("_comparator"):
            seen.add(name)
            found.append(name)
    return found


def compact_metrics(
    metrics: dict[str, Any],
    *,
    prefix: str = "",
    limit: int = _MAX_COMPACT_METRICS,
) -> dict[str, float | int | str]:
    """Keep only scalar routing metrics; drop per-item traces and nested blobs."""
    compact: dict[str, float | int | str] = {}

    def _walk(key: str, value: Any) -> None:
        if len(compact) >= limit:
            return
        if isinstance(value, bool):
            compact[key] = str(value)
            return
        if isinstance(value, (int, float)):
            compact[key] = value
            return
        if isinstance(value, str):
            if len(value) <= _MAX_METRIC_STRING:
                compact[key] = value
            return
        if isinstance(value, dict):
            nested_value = value.get("value")
            if isinstance(nested_value, (int, float)) and not isinstance(nested_value, bool):
                compact[key] = nested_value
                return
            for child_key, child in value.items():
                if child_key in _SKIP_METRIC_KEYS:
                    continue
                next_key = f"{key}.{child_key}" if key else str(child_key)
                _walk(next_key, child)
                if len(compact) >= limit:
                    return

    _walk(prefix, metrics)
    return compact


def _truthy_flag(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value == 1:
            return True
        if value == 0:
            return False
        return None
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return None


def _score_below_one(compact: dict[str, float | int | str]) -> bool:
    for key, value in compact.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        leaf = key.rsplit(".", 1)[-1].lower()
        if leaf in _SCORE_LEAVES or any(leaf.endswith(part) for part in _SCORE_LEAVES):
            if float(value) < 1.0:
                return True
    return False


def _metric_str(metrics: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = metrics.get(key)
        if value is None or isinstance(value, (dict, list)):
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def harness_failed(metrics: dict[str, Any] | None) -> bool:
    """True when metrics describe an infrastructure/harness failure, not a score."""
    if not metrics:
        return False
    status = _metric_str(metrics, "status").lower()
    stage = _metric_str(metrics, "failure_stage")
    acquisition = _metric_str(metrics, "acquisition_status")
    if status in _HARNESS_FAILED_STATUSES:
        return True
    if stage in _INFRA_STAGES:
        return True
    if acquisition in _INFRA_ACQUISITION:
        return True
    return False


def failure_fingerprint(metrics: dict[str, Any] | None) -> str:
    """Stable short id for a failure cause. Uses an explicit fingerprint when present."""
    if not metrics:
        return ""
    existing = _metric_str(metrics, "fingerprint")
    if existing:
        return existing[:32]
    bits = [
        _metric_str(metrics, "failure_stage"),
        _metric_str(metrics, "failure_substage"),
        _metric_str(metrics, "error_code"),
        _metric_str(metrics, "error_type"),
        _metric_str(metrics, "detail")[:80],
    ]
    if not any(bits):
        return ""
    return hashlib.sha256("|".join(bits).encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class MetricsContractIssue:
    """One metrics-contract failure with a stable fingerprint."""

    reason: str
    fingerprint: str
    detail: str = ""


@dataclass
class MetricsContractResult:
    """Outcome of :func:`validate_metrics_contract`."""

    ok: bool
    issues: list[MetricsContractIssue] = field(default_factory=list)

    @property
    def reason(self) -> str:
        return self.issues[0].reason if self.issues else ""

    @property
    def fingerprint(self) -> str:
        if not self.issues:
            return ""
        if len(self.issues) == 1:
            return self.issues[0].fingerprint
        blob = "|".join(item.fingerprint for item in self.issues)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]

    @property
    def errors(self) -> list[str]:
        return [item.detail or item.reason for item in self.issues]


def _contract_issue(reason: str, detail: str = "") -> MetricsContractIssue:
    source = f"{reason}|{detail[:80]}"
    return MetricsContractIssue(
        reason=reason,
        detail=detail,
        fingerprint=hashlib.sha256(source.encode("utf-8")).hexdigest()[:12],
    )


def _coerce_positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _declared_item_records(metrics: dict[str, Any]) -> tuple[bool, list[dict[str, Any]]]:
    """Whether an item-record list key is present, and the dict rows in it.

    An empty list still counts as declared so ``n_questions: 1`` with
    ``per_question: []`` is a mismatch, not a scalar payload.
    """
    for key in _ITEM_RECORD_KEYS:
        if key not in metrics:
            continue
        value = metrics[key]
        if isinstance(value, list):
            return True, [item for item in value if isinstance(item, dict)]
    return False, []


def _item_records(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    _, records = _declared_item_records(metrics)
    return records


def _looks_like_exception(reason: Any) -> bool:
    text = str(reason or "").strip()
    return bool(text) and bool(_EXCEPTION_FAILURE_RE.match(text))


def _item_operationally_failed(record: dict[str, Any]) -> bool:
    status = str(record.get("status") or "").strip().lower()
    if status in _HARNESS_FAILED_STATUSES:
        return True
    parseable = _truthy_flag(record.get("parseable"))
    if record.get("failed") is True and parseable is not True:
        return True
    return _looks_like_exception(record.get("failure_reason"))


def validate_metrics_contract(
    metrics: dict[str, Any] | None,
    *,
    expected_method: str = "",
    metrics_state: str = "present",
) -> MetricsContractResult:
    """Check that a metrics JSON object is a completed, well-formed run.

    Scientific zeroes (accuracy/F1 of 0, unused tools on a deterministic
    baseline) are valid. Missing optional fields are ignored so simple
    scalar payloads remain process-complete. Structural lies — a missing
    file, invalid JSON, the wrong ``method``, a harness-failed status, a
    non-positive ``n_questions``, a count mismatch (including an empty
    item-record list), or a top-level ``completed`` result whose every
    item record failed operationally — are rejected with a stable
    reason/fingerprint.
    """
    issues: list[MetricsContractIssue] = []
    if metrics_state == "missing":
        issues.append(_contract_issue("missing_metrics", "metrics file is missing"))
        return MetricsContractResult(ok=False, issues=issues)
    if metrics_state == "invalid_json":
        issues.append(_contract_issue("invalid_json", "metrics file is not valid JSON object"))
        return MetricsContractResult(ok=False, issues=issues)
    if not isinstance(metrics, dict) or not metrics:
        issues.append(_contract_issue("missing_metrics", "metrics payload is empty or not an object"))
        return MetricsContractResult(ok=False, issues=issues)

    method = metrics.get("method")
    if expected_method and method is not None and str(method).strip() != expected_method:
        issues.append(
            _contract_issue(
                "method_mismatch",
                f"metrics method={method!r} does not match invoked variant {expected_method!r}",
            )
        )

    if harness_failed(metrics):
        detail = (
            _metric_str(metrics, "detail")
            or _metric_str(metrics, "error_type")
            or _metric_str(metrics, "status")
            or "harness failed"
        )
        issues.append(_contract_issue("harness_failed", detail[:180]))

    if "n_questions" in metrics:
        n_questions = _coerce_positive_int(metrics.get("n_questions"))
        if n_questions is None:
            issues.append(
                _contract_issue(
                    "malformed_n_questions",
                    f"n_questions={metrics.get('n_questions')!r} is not an integer",
                )
            )
        elif n_questions <= 0:
            issues.append(
                _contract_issue(
                    "n_questions_non_positive",
                    f"n_questions={n_questions} must be positive",
                )
            )
        else:
            declared, records = _declared_item_records(metrics)
            if declared and len(records) != n_questions:
                issues.append(
                    _contract_issue(
                        "n_questions_mismatch",
                        f"n_questions={n_questions} does not match {len(records)} item records",
                    )
                )

    records = _item_records(metrics)
    status = _metric_str(metrics, "status").lower()
    if (
        records
        and status in _COMPLETED_RUN_STATUSES
        and all(_item_operationally_failed(item) for item in records)
    ):
        issues.append(
            _contract_issue(
                "universal_item_failure",
                f"status={status!r} but all {len(records)} item records failed operationally",
            )
        )

    return MetricsContractResult(ok=not issues, issues=issues)


def validate_smoke_live_path(metrics: dict[str, Any] | None) -> MetricsContractResult:
    """Smoke-only checks: one real item record and a live model call.

    Not part of :func:`validate_metrics_contract` so full runs and scalar
    payloads are unaffected. Parser-only stubs (empty ``per_question``,
    ``model_call_count`` missing or 0) are rejected.
    """
    issues: list[MetricsContractIssue] = []
    if not isinstance(metrics, dict) or not metrics:
        issues.append(_contract_issue("missing_metrics", "smoke metrics payload is empty"))
        return MetricsContractResult(ok=False, issues=issues)

    n_questions = _coerce_positive_int(metrics.get("n_questions"))
    if n_questions is None or n_questions < 1:
        issues.append(
            _contract_issue(
                "smoke_n_questions",
                f"smoke requires n_questions >= 1, got {metrics.get('n_questions')!r}",
            )
        )

    declared, records = _declared_item_records(metrics)
    if not declared or not records:
        issues.append(
            _contract_issue(
                "smoke_missing_item_records",
                "smoke requires at least one item record (parser-only stubs are invalid)",
            )
        )

    count = _coerce_positive_int(metrics.get("model_call_count"))
    if count is None or count < 1:
        issues.append(
            _contract_issue(
                "smoke_no_model_call",
                f"smoke requires model_call_count >= 1, got {metrics.get('model_call_count')!r}",
            )
        )

    return MetricsContractResult(ok=not issues, issues=issues)


def sanitize_diagnostic_payload(value: Any, *, depth: int = 0) -> Any:
    """Drop protected/raw payload fields from a diagnostic JSON object."""
    if depth > 8:
        return None
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, child in value.items():
            if str(key).lower() in _UNSAFE_DIAGNOSTIC_KEYS:
                continue
            sanitized = sanitize_diagnostic_payload(child, depth=depth + 1)
            if sanitized is not None:
                cleaned[str(key)] = sanitized
        return cleaned
    if isinstance(value, list):
        return [
            item
            for item in (sanitize_diagnostic_payload(child, depth=depth + 1) for child in value[:40])
            if item is not None
        ][:20]
    if isinstance(value, str) and len(value) > 400:
        return value[:399] + "…"
    return value


def failure_class_from_metrics(
    metrics: dict[str, Any] | None,
    *,
    process_status: str = "",
    scientific_status: str = "",
) -> str:
    if harness_failed(metrics) or process_status == "failed":
        return "infrastructure"
    if scientific_status in {"accepted", "below_threshold"}:
        return "scientific"
    return "unknown"


def scientific_status_from_metrics(metrics: dict[str, Any]) -> str:
    """Map harness scalars onto a routing label. Unknown when metrics are empty.

    Process-complete statuses (`completed`, `ok`, …) are not scientific
    acceptance. Only an explicit acceptance flag or `status=accepted` counts.
    Infrastructure failures (`failure_stage`, acquisition errors, `status=failed`)
    are `unknown`, not `below_threshold`.
    """
    if not metrics:
        return "unknown"
    if harness_failed(metrics):
        return "unknown"
    compact = compact_metrics(dict(metrics))
    flagged = _truthy_flag(compact.get("acceptance"))
    if flagged is None:
        for key, value in compact.items():
            leaf = str(key).rsplit(".", 1)[-1].lower()
            if leaf in {"acceptance", "eligible_for_acceptance"}:
                flagged = _truthy_flag(value)
                if flagged is not None:
                    break
    if flagged is not None:
        return "accepted" if flagged else "below_threshold"
    status = str(compact.get("status", "")).strip().lower()
    if status in _FAILED_STATUSES:
        return "below_threshold"
    if status in _ACCEPTED_STATUSES:
        return "accepted"
    if _score_below_one(compact):
        return "below_threshold"
    return "unknown"


def scientific_status_from_comparison(
    metric_names: list[str], variants: list[VariantResult]
) -> str:
    """Compare the "proposed" variant against every other ("baseline")
    variant on each of the plan's declared metrics — the actual acceptance
    question for this pipeline's baseline-vs-proposed experiments, which
    scientific_status_from_metrics() never answers on its own (it only ever
    reads one variant's metrics in isolation, never a baseline).

    No explicit threshold needed: "not worse than any baseline on any
    declared metric, and strictly better on at least one" is the acceptance
    bar for this pipeline's baseline-vs-proposed convention. "unknown" when
    there isn't enough data to compare (no declared metrics, no proposed/
    baseline variant, or proposed didn't complete) — same fail-safe default
    scientific_status_from_metrics() uses.
    """
    proposed = next((v for v in variants if v.name == "proposed"), None)
    baselines = [v for v in variants if v.name != "proposed"]
    if not metric_names or proposed is None or not baselines:
        return "unknown"
    if proposed.process_status != "completed":
        return "unknown"
    strictly_better = False
    for metric in metric_names:
        p_val = proposed.metrics.get(metric)
        if not isinstance(p_val, (int, float)) or isinstance(p_val, bool):
            continue
        for baseline in baselines:
            b_val = baseline.metrics.get(metric)
            if not isinstance(b_val, (int, float)) or isinstance(b_val, bool):
                continue
            if p_val < b_val:
                return "below_threshold"
            if p_val > b_val:
                strictly_better = True
    return "accepted" if strictly_better else "below_threshold"


def metric_diagnostics(
    metrics: dict[str, Any],
    *,
    failure_kind: str = "",
    metrics_state: str = "",
    duration_ms: int | None = None,
    exit_code: int | None = None,
) -> dict[str, Any]:
    """Deterministic digest of per-task traces for manager routing."""
    diagnostic: dict[str, Any] = {}
    if failure_kind:
        diagnostic["failure_kind"] = failure_kind
    if metrics_state:
        diagnostic["metrics_state"] = metrics_state
    if duration_ms is not None:
        diagnostic["duration_ms"] = duration_ms
    if exit_code is not None:
        diagnostic["exit_code"] = exit_code

    stage = _metric_str(metrics, "failure_stage")
    substage = _metric_str(metrics, "failure_substage")
    error_code = _metric_str(metrics, "error_code")
    error_type = _metric_str(metrics, "error_type")
    detail = _metric_str(metrics, "detail")
    acquisition = _metric_str(metrics, "acquisition_status")
    if stage:
        diagnostic["failure_stage"] = stage
    if substage:
        diagnostic["failure_substage"] = substage
    if error_code:
        diagnostic["error_code"] = error_code
    if error_type:
        diagnostic["error_type"] = error_type
    if detail:
        diagnostic["detail"] = detail[:180]
    if acquisition:
        diagnostic["acquisition_status"] = acquisition
    fingerprint = failure_fingerprint(metrics)
    if fingerprint:
        diagnostic["fingerprint"] = fingerprint
    retryable = metrics.get("retryable")
    if isinstance(retryable, bool):
        diagnostic["retryable"] = retryable
    diagnostics_path = _metric_str(metrics, "diagnostics_path")
    if diagnostics_path:
        diagnostic["diagnostics_path"] = diagnostics_path

    records = metrics.get("task_records")
    if not isinstance(records, list):
        records = metrics.get("records")
    if not isinstance(records, list):
        return diagnostic

    selected = metrics.get("selected_indices")
    diagnostic["n_tasks"] = len(records)
    browsed = 0
    event_counts: dict[str, int] = {}
    failing_examples: list[str] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        if record.get("browsed_both_tools") is True:
            browsed += 1
        events = record.get("events")
        if isinstance(events, list):
            for event in events:
                if not isinstance(event, dict):
                    continue
                tool = str(event.get("tool") or "unknown")
                detail = str(event.get("detail_class") or ("ok" if event.get("ok") else "error"))
                ok_flag = "ok" if event.get("ok") is True else "fail"
                key = f"{tool}/{detail}/{ok_flag}"
                event_counts[key] = event_counts.get(key, 0) + 1
        failed = record.get("browsed_both_tools") is False or record.get("correct") is False
        if failed and len(failing_examples) < _MAX_EXAMPLE_TASKS:
            if isinstance(selected, list) and index < len(selected):
                failing_examples.append(str(selected[index]))
            else:
                item_id = record.get("item_id") or record.get("id") or record.get("index")
                failing_examples.append(str(item_id if item_id is not None else index))
    diagnostic["browsed_both_tools"] = browsed
    if event_counts:
        ranked = sorted(event_counts.items(), key=lambda item: (-item[1], item[0]))
        diagnostic["event_counts"] = dict(ranked[:_MAX_EVENT_CLASSES])
    if failing_examples:
        diagnostic["example_failing_tasks"] = failing_examples
    return diagnostic
