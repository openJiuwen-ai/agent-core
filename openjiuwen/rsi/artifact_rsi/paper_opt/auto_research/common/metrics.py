"""Helpers for preparing experiment metrics for agent handoffs."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_execution.schemas import VariantResult

_MAX_COMPACT_METRICS = 40
_MAX_METRIC_STRING = 120
_PROMPT_SUMMARY_MAX_CHARS = 8000
_PROMPT_SUMMARY_MAX_STRING = 240
_PROMPT_SUMMARY_MAX_SCALAR_LIST = 24
_PROMPT_SUMMARY_MAX_KEYS = 40
_MAX_EVENT_CLASSES = 12
_MAX_EXAMPLE_TASKS = 3
_MAX_RESOLVE_DEPTH = 16
_MAX_RESOLVE_NODES = 10_000
_MAX_DIAGNOSTIC_STRING = 400
_CANONICAL_METRICS_KEY = "metrics"
_BACKTICK_IDENT = re.compile(r"`([a-z][a-z0-9_]{2,})`")
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
_ITEM_RECORD_KEYS = ("per_question", "task_records", "records", "item_records")
_EXCEPTION_FAILURE_RE = re.compile(r"^[A-Za-z]+(?:Error|Exception)\s*:")
_PAIR_METRIC_STATUS = "computed_from_last_measured_rows"
MetricResolutionStatus = Literal["resolved", "missing", "ambiguous"]


@dataclass(frozen=True)
class MetricResolution:
    """One plan-metric lookup against a raw metrics JSON object."""

    name: str
    status: MetricResolutionStatus
    value: float | int | None = None
    path: str = ""
    candidates: tuple[str, ...] = ()


def infer_baseline_names(*texts: str) -> list[str]:
    """Pull `--method` baseline names out of task/design text.

    Looks for backtick-quoted snake_case identifiers ending in `_baseline` or
    `_comparator` so the host can actually invoke every named variant.
    """
    found: list[str] = []
    seen: set[str] = set()
    blob = "\n".join(item for item in texts if item)
    for name in _BACKTICK_IDENT.findall(blob):
        if name in seen:
            continue
        if name.endswith("_baseline") or name.endswith("_comparator"):
            seen.add(name)
            found.append(name)
    return found


def _join_metric_path(prefix: str, key: str) -> str:
    return f"{prefix}.{key}" if prefix else str(key)


def _metric_path_leaf(path: str) -> str:
    return str(path).rsplit(".", 1)[-1]


def _coerce_finite_number(value: Any) -> float | int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        lowered = text.lower()
        if lowered in {"nan", "inf", "+inf", "-inf"}:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
        if not math.isfinite(number):
            return None
        if all(char.isdigit() or char in "+-" for char in text):
            return int(number)
        return number
    return None


def _numeric_cell(value: Any) -> float | int | None:
    direct = _coerce_finite_number(value)
    if direct is not None:
        return direct
    if isinstance(value, dict) and "value" in value:
        return _coerce_finite_number(value.get("value"))
    return None


def _follow_dotted_path(payload: Any, path: str) -> float | int | None:
    current: Any = payload
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        if isinstance(current, list) and part.isdigit():
            index = int(part)
            if 0 <= index < len(current):
                current = current[index]
                continue
        return None
    return _numeric_cell(current)


def _collect_numeric_paths(
    node: Any,
    *,
    prefix: str = "",
    depth: int = 0,
    remaining: list[int] | None = None,
) -> list[tuple[str, float | int]]:
    """Uncapped walk of mappings and sequences for numeric leaves."""
    found: list[tuple[str, float | int]] = []
    if remaining is None:
        remaining = [_MAX_RESOLVE_NODES]
    if depth > _MAX_RESOLVE_DEPTH or remaining[0] <= 0:
        return found
    remaining[0] -= 1
    if isinstance(node, dict):
        cell = _numeric_cell(node) if prefix else None
        if cell is not None and "value" in node:
            found.append((prefix, cell))
            for child_key, child in node.items():
                if child_key == "value":
                    continue
                found.extend(
                    _collect_numeric_paths(
                        child,
                        prefix=_join_metric_path(prefix, str(child_key)),
                        depth=depth + 1,
                        remaining=remaining,
                    )
                )
            return found
        for child_key, child in node.items():
            found.extend(
                _collect_numeric_paths(
                    child,
                    prefix=_join_metric_path(prefix, str(child_key)),
                    depth=depth + 1,
                    remaining=remaining,
                )
            )
        return found
    if isinstance(node, list):
        for index, child in enumerate(node):
            found.extend(
                _collect_numeric_paths(
                    child,
                    prefix=_join_metric_path(prefix, str(index)),
                    depth=depth + 1,
                    remaining=remaining,
                )
            )
        return found
    number = _numeric_cell(node)
    if number is not None and prefix:
        found.append((prefix, number))
    return found


def _unique_or_ambiguous(
    name: str, hits: list[tuple[str, float | int]]
) -> MetricResolution:
    if not hits:
        return MetricResolution(name=name, status="missing")
    paths = tuple(path for path, _value in hits)
    values = {hit[1] for hit in hits}
    if len(values) > 1:
        return MetricResolution(
            name=name,
            status="ambiguous",
            candidates=paths,
        )
    path, value = sorted(hits, key=lambda item: item[0])[0]
    return MetricResolution(name=name, status="resolved", value=value, path=path, candidates=paths)


def resolve_metric(metrics: dict[str, Any] | None, name: str) -> MetricResolution:
    """Resolve one declared metric from canonical, root, path, or unique leaf.

    Order: ``metrics.<name>``, root ``<name>``, exact dotted path, then a
    unique recursive leaf whose final path segment equals ``name``. Conflicting
    numeric values are ``ambiguous`` rather than first-match.
    """
    cleaned = str(name).strip()
    if not cleaned:
        return MetricResolution(name=name, status="missing")
    payload = metrics if isinstance(metrics, dict) else {}
    if not payload:
        return MetricResolution(name=cleaned, status="missing")

    hits: list[tuple[str, float | int]] = []
    nested = payload.get(_CANONICAL_METRICS_KEY)
    if isinstance(nested, dict) and cleaned in nested:
        canonical = _numeric_cell(nested.get(cleaned))
        if canonical is not None:
            hits.append((_join_metric_path(_CANONICAL_METRICS_KEY, cleaned), canonical))
    if cleaned in payload:
        root = _numeric_cell(payload.get(cleaned))
        if root is not None:
            hits.append((cleaned, root))
    if hits:
        return _unique_or_ambiguous(cleaned, hits)

    if "." in cleaned:
        dotted = _follow_dotted_path(payload, cleaned)
        if dotted is not None:
            return MetricResolution(name=cleaned, status="resolved", value=dotted, path=cleaned)

    leaf_hits = [
        (path, value)
        for path, value in _collect_numeric_paths(payload)
        if _metric_path_leaf(path) == cleaned
    ]
    return _unique_or_ambiguous(cleaned, leaf_hits)


def resolve_plan_metrics(
    metrics: dict[str, Any] | None,
    names: list[str] | None = None,
) -> dict[str, MetricResolution]:
    """Resolve each declared plan metric against a raw payload."""
    resolved: dict[str, MetricResolution] = {}
    for name in names or []:
        cleaned = str(name).strip()
        if not cleaned or cleaned in resolved:
            continue
        resolved[cleaned] = resolve_metric(metrics, cleaned)
    return resolved


def metric_resolution_diagnostic(
    resolutions: dict[str, MetricResolution],
    *,
    limit: int = 20,
) -> dict[str, Any]:
    """Bounded provenance for missing/ambiguous/resolved plan metrics."""
    resolved: dict[str, str] = {}
    missing: list[str] = []
    ambiguous: dict[str, list[str]] = {}
    for name, hit in resolutions.items():
        if hit.status == "resolved" and len(resolved) < limit:
            resolved[name] = hit.path
        elif hit.status == "missing" and len(missing) < limit:
            missing.append(name)
        elif hit.status == "ambiguous" and len(ambiguous) < limit:
            ambiguous[name] = list(hit.candidates)[:8]
    diagnostic: dict[str, Any] = {}
    if resolved:
        diagnostic["resolved"] = resolved
    if missing:
        diagnostic["missing"] = missing
    if ambiguous:
        diagnostic["ambiguous"] = ambiguous
    return diagnostic


def materialize_handoff_metrics(
    metrics: dict[str, Any] | None,
    *,
    plan_metrics: list[str] | None = None,
    limit: int = _MAX_COMPACT_METRICS,
) -> tuple[dict[str, float | int | str], dict[str, Any]]:
    """Compact display scalars plus uncapped resolved plan metrics."""
    raw = dict(metrics) if isinstance(metrics, dict) else {}
    compact = compact_metrics(raw, limit=limit)
    resolutions = resolve_plan_metrics(raw, plan_metrics)
    for name, hit in resolutions.items():
        if hit.status == "resolved" and hit.value is not None:
            compact[name] = hit.value
            if hit.path and hit.path not in compact:
                compact[hit.path] = hit.value
        elif hit.status == "ambiguous":
            compact[f"{name}_status"] = "ambiguous"
    return compact, metric_resolution_diagnostic(resolutions)


def _clip_text(value: str, max_len: int) -> str:
    if len(value) <= max_len:
        return value
    return value[: max_len - 1] + "…"


def _is_object_list(value: Any) -> bool:
    return isinstance(value, list) and any(isinstance(item, dict) for item in value)


def compact_metrics(
    metrics: dict[str, Any],
    *,
    prefix: str = "",
    limit: int = _MAX_COMPACT_METRICS,
) -> dict[str, float | int | str]:
    """Keep scalars; omit lists of objects. Display-only.

    Scientific reads use :func:`resolve_metric`.
    """
    compact: dict[str, float | int | str] = {}

    def _walk(key: str, value: Any) -> None:
        if len(compact) >= limit:
            return
        if _is_object_list(value):
            return
        if isinstance(value, bool):
            compact[key] = str(value)
            return
        if isinstance(value, (int, float)):
            compact[key] = value
            return
        if isinstance(value, str):
            compact[key] = _clip_text(value, _MAX_METRIC_STRING)
            return
        if isinstance(value, dict):
            nested_value = value.get("value")
            if isinstance(nested_value, (int, float)) and not isinstance(nested_value, bool):
                compact[key] = nested_value
                return
            for child_key, child in value.items():
                next_key = f"{key}.{child_key}" if key else str(child_key)
                _walk(next_key, child)
                if len(compact) >= limit:
                    return

    _walk(prefix, metrics)
    return compact


def _prompt_summary_stub(
    kind: str,
    *,
    n: int | None = None,
    keys: list[str] | None = None,
    path: str = "",
) -> dict[str, Any]:
    stub: dict[str, Any] = {"_omitted": kind}
    if n is not None:
        stub["n"] = n
    if keys:
        stub["keys"] = keys[:_PROMPT_SUMMARY_MAX_KEYS]
    if path:
        stub["path"] = path
    return stub


def _object_list_keys(items: list[Any]) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in item:
            name = str(key)
            if name in seen:
                continue
            seen.add(name)
            keys.append(name)
            if len(keys) >= _PROMPT_SUMMARY_MAX_KEYS:
                return keys
    return keys


def _clip_prompt_string(value: str) -> str:
    return _clip_text(value, _PROMPT_SUMMARY_MAX_STRING)


def _is_prompt_stub(value: Any) -> bool:
    return isinstance(value, dict) and str(value.get("_omitted") or "") in {
        "object_list",
        "scalar_list",
        "object",
    }


def _dump_prompt_size(value: Any) -> int:
    try:
        return len(json.dumps(value, default=str, ensure_ascii=False))
    except (TypeError, ValueError):
        return len(str(value))


def _summarize_prompt_value(value: Any, *, path: str) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return _clip_prompt_string(value)
    if isinstance(value, list):
        if not value:
            return []
        if _is_object_list(value):
            return _prompt_summary_stub(
                "object_list",
                n=len(value),
                keys=_object_list_keys(value),
                path=path,
            )
        if len(value) > _PROMPT_SUMMARY_MAX_SCALAR_LIST:
            head = [_summarize_prompt_value(item, path=path) for item in value[:3]]
            stub = _prompt_summary_stub("scalar_list", n=len(value), path=path)
            stub["head"] = head
            return stub
        return [_summarize_prompt_value(item, path=path) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _summarize_prompt_value(child, path=path) for key, child in value.items()
        }
    return _clip_prompt_string(str(value))


def _shrink_prompt_summary(summarized: dict[str, Any], *, path: str, max_chars: int) -> dict[str, Any]:
    result = dict(summarized)
    while _dump_prompt_size(result) > max_chars:
        candidates = [
            (key, _dump_prompt_size(value))
            for key, value in result.items()
            if isinstance(value, dict) and not _is_prompt_stub(value)
        ]
        if not candidates:
            return _prompt_summary_stub(
                "object",
                n=len(result),
                keys=list(result),
                path=path,
            )
        key, _ = max(candidates, key=lambda item: item[1])
        child = result[key]
        result[key] = _prompt_summary_stub(
            "object",
            keys=list(child) if isinstance(child, dict) else [],
            path=path,
        )
    return result


def summarize_metrics_for_prompt(
    metrics: dict[str, Any] | None,
    *,
    path: str = "",
    max_chars: int = _PROMPT_SUMMARY_MAX_CHARS,
) -> dict[str, Any]:
    """Keep scalars and small objects; stub lists of objects for a judge prompt.

    Shape-only: no metric-name vocabulary. Item-record arrays stay off the
    prompt (count + keys + path) so n=100+ payloads remain bounded.
    """
    raw = dict(metrics) if isinstance(metrics, dict) else {}
    summarized = _summarize_prompt_value(raw, path=path)
    if not isinstance(summarized, dict):
        summarized = {"value": summarized}
    if _dump_prompt_size(summarized) <= max_chars:
        return summarized
    return _shrink_prompt_summary(summarized, path=path, max_chars=max_chars)


def compact_metrics_for_manager(
    metrics: dict[str, Any],
    *,
    plan_metrics: list[str] | None = None,
    limit: int = _MAX_COMPACT_METRICS,
) -> dict[str, float | int | str]:
    """Compact metrics for the manager prompt, pinning plan values first.

    Display compaction is insertion-order + limit. Resolved plan metrics are
    written under their declared names before the cap so nested endpoints
    survive noisy payloads. Remaining slots are structural scalars, not a
    hardcoded score-name hunt.
    """
    names = [str(name).strip() for name in (plan_metrics or []) if str(name).strip()]
    out: dict[str, float | int | str] = {}
    for name in names:
        hit = resolve_metric(metrics, name)
        if len(out) >= limit:
            return out
        if hit.status == "resolved" and hit.value is not None:
            out[name] = hit.value
            if hit.path and hit.path not in out and len(out) < limit:
                out[hit.path] = hit.value
        elif hit.status == "ambiguous":
            out[f"{name}_status"] = "ambiguous"
    rest = compact_metrics(dict(metrics), limit=limit)
    for key, value in rest.items():
        if key in out:
            continue
        if len(out) >= limit:
            return out
        out[key] = value
    return out


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
    if status in _HARNESS_FAILED_STATUSES:
        return True
    return bool(_metric_str(metrics, "failure_stage"))


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


def primary_metric_unresolved(
    metrics: dict[str, Any] | None,
    primary_metric: str,
) -> MetricsContractIssue | None:
    """Mechanical check: the committed primary metric must resolve to a finite number."""
    cleaned = str(primary_metric or "").strip()
    if not cleaned:
        return None
    hit = resolve_metric(metrics, cleaned)
    if hit.status == "resolved" and hit.value is not None:
        return None
    return _contract_issue(
        "primary_metric_unresolved",
        f"primary metric {cleaned!r} did not resolve to a finite number (status={hit.status})",
    )


def item_failure_rate(metrics: dict[str, Any] | None) -> float | None:
    """Failed item records / total records. None when no item records exist."""
    if not isinstance(metrics, dict) or not metrics:
        return None
    records = _item_records(metrics)
    if not records:
        return None
    failed = sum(1 for item in records if _item_operationally_failed(item))
    return failed / len(records)


SanityStatus = Literal["ok", "invalid_run", "unknown"]


def run_sanity(
    metrics: dict[str, Any] | None,
    *,
    expected_method: str = "",
    metrics_state: str = "present",
    primary_metric: str = "",
    process_status: str = "",
) -> SanityStatus:
    """Mechanical run validity. Partial item failures never invalidate a run."""
    if process_status == "failed":
        return "invalid_run"
    contract = validate_metrics_contract(
        metrics,
        expected_method=expected_method,
        metrics_state=metrics_state,
    )
    if not contract.ok:
        return "invalid_run"
    if primary_metric_unresolved(metrics, primary_metric) is not None:
        return "invalid_run"
    if not metrics:
        return "unknown"
    return "ok"


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
                "smoke requires at least one item record (parser-only stubs are invalid); "
                "write a non-empty top-level `per_question` list of objects "
                "(also accepted: `task_records`, `records`, `item_records`)",
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
    """Copy a diagnostic sidecar: omit object lists, clip long strings, keep scalars."""
    if depth > 8:
        return None
    if _is_object_list(value):
        return None
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, child in value.items():
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
    if isinstance(value, str):
        return _clip_text(value, _MAX_DIAGNOSTIC_STRING)
    return value


def failure_class_from_metrics(
    metrics: dict[str, Any] | None,
    *,
    process_status: str = "",
    sanity: str = "",
) -> str:
    if harness_failed(metrics) or process_status == "failed" or sanity == "invalid_run":
        return "infrastructure"
    return "unknown"


def metric_number(metrics: dict[str, Any], name: str) -> float | int | None:
    """Read a numeric plan metric from a raw or compact payload."""
    hit = resolve_metric(metrics, name)
    if hit.status != "resolved":
        return None
    return hit.value


def score_number(
    metrics: dict[str, Any],
    plan_metrics: list[str] | None = None,
) -> float | None:
    """First resolved committed plan metric on this row."""
    for name in plan_metrics or []:
        value = metric_number(metrics, name)
        if value is not None:
            return float(value)
    return None


def infer_baseline_variant_names(
    names: list[str],
    *,
    baselines: list[str] | None = None,
) -> set[str]:
    """Baselines come from the plan, or the host's `baseline` / `*_baseline` names."""
    named = {item.strip() for item in (baselines or []) if str(item).strip()}
    found: set[str] = set()
    for name in names:
        if name == "proposed":
            continue
        if name in named or name == "baseline":
            found.add(name)
            continue
        if name.endswith("_baseline") or name.endswith("_comparator"):
            found.add(name)
    return found


def infer_proposed_name(
    names: list[str],
    *,
    metric_names: list[str] | None = None,
    baselines: list[str] | None = None,
) -> str | None:
    """Pick the treatment method: `proposed`, else a metric-prefixed name."""
    if "proposed" in names:
        return "proposed"
    baseline_names = infer_baseline_variant_names(names, baselines=baselines)
    treatments = [name for name in names if name not in baseline_names]
    for metric in metric_names or []:
        for name in sorted(treatments, key=len, reverse=True):
            if metric == name or metric.startswith(f"{name}_"):
                return name
    return treatments[0] if treatments else None


def _write_pair_metric(metrics: dict[str, Any], name: str, value: float) -> dict[str, Any]:
    out = copy.deepcopy(metrics)
    out[name] = value
    nested = out.get("metrics")
    if isinstance(nested, dict):
        nested[name] = value
        nested[f"{name}_status"] = _PAIR_METRIC_STATUS
        status_map = nested.get("metric_status")
        if isinstance(status_map, dict):
            status_map[name] = "paired"
        out["metrics"] = nested
    else:
        out[f"{name}_status"] = _PAIR_METRIC_STATUS
    return out


def _peer_variants(
    item: VariantResult,
    variants: list[VariantResult],
    baseline_names: set[str],
) -> list[VariantResult]:
    others = [
        peer
        for peer in variants
        if peer.name != item.name and peer.process_status == "completed"
    ]
    if not baseline_names:
        return others
    named = [peer for peer in others if peer.name in baseline_names]
    return named or others


def overlay_paired_metrics(
    variants: list[VariantResult],
    metric_names: list[str],
    *,
    baselines: list[str] | None = None,
) -> list[VariantResult]:
    """Fill plan metrics that a single-method artifact left empty.

    Pairing uses last-measured sibling scores. Disk ``results/*.metrics.json``
    files are left as the process wrote them.
    """
    if not metric_names or len(variants) < 2:
        return variants
    names = [item.name for item in variants]
    baseline_names = infer_baseline_variant_names(names, baselines=baselines)
    proposed_name = infer_proposed_name(
        names, metric_names=metric_names, baselines=baselines
    )
    seed = next((item for item in variants if item.name == proposed_name), None)
    seed_peers = _peer_variants(seed, variants, baseline_names) if seed is not None else []
    peer_names = {peer.name for peer in seed_peers}
    overlaid: list[VariantResult] = []
    for item in variants:
        if (
            item.name in peer_names
            or item.name in baseline_names
            or item.process_status != "completed"
        ):
            overlaid.append(item)
            continue
        missing: list[str] = []
        blocked = False
        for metric_name in metric_names:
            hit = resolve_metric(item.metrics, metric_name)
            if hit.status == "ambiguous":
                blocked = True
                break
            if hit.status == "missing":
                missing.append(metric_name)
        if blocked or not missing:
            overlaid.append(item)
            continue
        peers = _peer_variants(item, variants, baseline_names)
        score_name = None
        for name in metric_names:
            if name in missing:
                continue
            if metric_number(item.metrics, name) is None:
                continue
            if all(metric_number(peer.metrics, name) is not None for peer in peers):
                score_name = name
                break
        if score_name is None:
            overlaid.append(item)
            continue
        proposed_score = score_number(item.metrics, [score_name])
        peer_scores = [score_number(peer.metrics, [score_name]) for peer in peers]
        if proposed_score is None or any(score is None for score in peer_scores) or not peer_scores:
            overlaid.append(item)
            continue
        deltas = [proposed_score - float(score) for score in peer_scores if score is not None]
        if len(set(deltas)) != 1:
            overlaid.append(item)
            continue
        metrics = dict(item.metrics)
        for metric in missing:
            metrics = _write_pair_metric(metrics, metric, deltas[0])
        overlaid.append(item.model_copy(update={"metrics": metrics}))
    return overlaid


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
    rate = item_failure_rate(metrics)
    if rate is not None:
        diagnostic["item_failure_rate"] = rate

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
