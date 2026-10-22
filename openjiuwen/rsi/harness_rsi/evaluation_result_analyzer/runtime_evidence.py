# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bounded, sanitized copies of case-local invocation evidence for diagnosis."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_CONFIG_FIELDS = {
    "max_iterations", "on_max_iterations", "solver_max_iterations", "solver_on_max_iterations",
    "max_iterations_hard_limit", "host_max_iterations", "hard_max_iterations", "solver_hard_max_iterations", "host_hard_limit",
    "max_output_tokens", "timeout", "timeout_seconds", "role", "case_id", "evaluation_id",
    "task_id", "invocation_id", "status", "source", "effective", "limits", "solver", "runtime",
    "allowed_config_fields", "candidate_editable", "host_limits", "effective_config", "runtime_config",
}
_TERMINATION_FIELDS = {
    "truncated_by_max_iterations", "termination_reason", "result_type", "max_iterations", "iterations_used", "used_iterations",
    "iteration_count", "on_max_iterations", "budget_exhausted", "finalization_attempted",
    "finalization_succeeded", "finalization_status", "finalization_error", "final_answer_available",
    "final_output_available", "artifacts_available", "output_available", "artifact_count",
    "model_call_count", "loop_model_calls", "finalization_model_calls", "elapsed_seconds",
    "token_usage", "input_tokens", "output_tokens", "total_tokens", "status", "reason", "role",
    "case_id", "task_id", "evaluation_id", "invocation_id", "error", "execution_termination",
    "finalization", "attempted", "succeeded", "success", "effective_max_iterations",
    "effective_on_max_iterations", "actual_iterations", "actual_model_calls", "completion_status",
}
_SECRET_KEY = re.compile(r"api.?key|authorization|password|secret|credential|access.?token|(?:^|[_-])token$|environment|headers|prompt|messages|request_body|model_config", re.I)
_SECRET_TEXT = re.compile(
    r"(?i)(bearer\s+)[^\s,;\"']+|"
    r"((?:api[_-]?key|access[_-]?token|token|password|secret|authorization)[\"']?\s*[:=]\s*)"
    r"(?:[\"'][^\"']*[\"']|[^\s,;}]+)|\bsk-[A-Za-z0-9_-]{8,}"
)

_PAYLOAD_TEXT = re.compile(r"(?i)(?:prompt|messages|request_body|response_body|model_config)[\"']?\s*[:=]")
_MAX_RECORD_BYTES = 8192
_MAX_LOG_FILE_BYTES = 4 * 1024 * 1024
_MAX_LOG_TOTAL_BYTES = 16 * 1024 * 1024
_MAX_STRUCTURED_BYTES = 65536


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): "[REDACTED]" if _SECRET_KEY.search(str(key)) else _redact(item)
                for key, item in value.items()}
    if isinstance(value, list):
        result = [_redact(item) for item in value[:50]]
        return result + ([{"truncated_items": len(value) - 50}] if len(value) > 50 else [])
    if isinstance(value, str):
        if _PAYLOAD_TEXT.search(value):
            return "[Structured prompt/model payload omitted]"
        return _SECRET_TEXT.sub(lambda match: (match.group(1) or match.group(2) or "") + "[REDACTED]", value)
    return value


def _project(value: Any, fields: set[str]) -> Any:
    if isinstance(value, dict):
        return {key: _project(item, fields) for key, item in value.items() if key in fields}
    if isinstance(value, list):
        return [_project(item, fields) for item in value[:50]]
    return _redact(value)


def _local_source(case_dir: Path, value: Any, default: str) -> Path | None:
    if isinstance(value, dict):
        value = value.get("path", value.get("file", default))
    source = Path(value) if isinstance(value, str) and value else Path(default)
    source = (source if source.is_absolute() else case_dir / source).resolve()
    return source if source.is_relative_to(case_dir.resolve()) else None


def read_execution_context(
    case_dir: Path, execution: dict[str, Any], *, recorded_termination_reason: str = "",
) -> dict[str, Any]:
    """Read only whitelisted, bounded case-local structured execution facts."""
    result: dict[str, Any] = {}
    for name, default, fields in (
        ("runtime_config", "runtime_config.json", _CONFIG_FIELDS),
        ("execution_termination", "termination.json", _TERMINATION_FIELDS),
    ):
        source = _local_source(case_dir, execution.get(name), default)
        status = "unknown"
        value: dict[str, Any] = {}
        if source is not None and source.is_file():
            try:
                if source.stat().st_size > _MAX_STRUCTURED_BYTES:
                    status = "oversized"
                else:
                    data = json.loads(source.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        value = _project(data, fields)
                        status = "available"
                    else:
                        status = "invalid"
            except (OSError, ValueError):
                status = "unreadable"
        elif isinstance(execution.get(name), dict):
            inline = execution[name]
            if not any(key in inline for key in ("path", "file")):
                value = _project(inline, fields)
                status = "available" if value else "unknown"
        provenance = str(source) if source else "outside_case_rejected"
        if name == "execution_termination" and recorded_termination_reason and not value.get("termination_reason"):
            value["termination_reason"] = recorded_termination_reason
            status = "partial" if status != "available" else status
            provenance += "; termination_reason from recorded case response/metadata"
        result[name] = {"status": status, "facts": value, "source": provenance}
    return result


def prepare_runtime_evidence(
    *, case_dir: Path, execution: dict[str, Any], runtime_dir: Path, recorded_termination_reason: str = "",
) -> dict[str, Any]:
    """Copy bounded invocation logs; never infer isolation from shared logs/time."""
    destination = runtime_dir / "runtime_evidence"
    destination.mkdir()
    context = read_execution_context(case_dir, execution, recorded_termination_reason=recorded_termination_reason)
    manifest: dict[str, Any] = {
        "scope": "case-local invocation files only; identities come from records, never timestamps",
        "limits": {"single_record_bytes": _MAX_RECORD_BYTES, "per_log_bytes": _MAX_LOG_FILE_BYTES,
                   "total_log_bytes": _MAX_LOG_TOTAL_BYTES, "total_source_bytes": _MAX_LOG_TOTAL_BYTES,
                   "max_log_files": 32},
        "copy_policy": "Complete files within 4 MiB; larger files retain tail and head within input/output budgets. "
                       "Tail byte offsets are exact; original tail line numbers remain unknown without scanning the omitted middle.",
        "structured": {}, "logs": [], "missing": [],
    }
    for name, filename in (("runtime_config", "runtime_config.json"), ("execution_termination", "termination.json")):
        target = destination / filename
        target.write_text(json.dumps(context[name], ensure_ascii=False, indent=2), encoding="utf-8")
        manifest["structured"][name] = {**context[name], "path": target.relative_to(runtime_dir).as_posix()}
        if context[name]["status"] != "available":
            manifest["missing"].append(name)
    raw_logs = execution.get("runtime_logs")
    if isinstance(raw_logs, dict):
        raw_logs = raw_logs.get("paths", raw_logs.get("files", [raw_logs]))
    if isinstance(raw_logs, str):
        raw_logs = [raw_logs]
    declared = isinstance(raw_logs, list) and bool(raw_logs)
    sources = [_local_source(case_dir, item, "logs") for item in raw_logs] if declared else sorted((case_dir / "logs").rglob("*.jsonl"))
    expanded: list[Path | None] = []
    for source in sources:
        if source is not None and source.is_dir():
            expanded.extend(sorted(source.rglob("*.jsonl")))
        else:
            expanded.append(source)
    remaining = _MAX_LOG_TOTAL_BYTES
    remaining_source = _MAX_LOG_TOTAL_BYTES
    for index, source in enumerate(list(dict.fromkeys(expanded))[:32], 1):
        row: dict[str, Any] = {"source": str(source) if source else "outside_case_rejected", "status": "unknown"}
        manifest["logs"].append(row)
        if source is None or not source.resolve().is_relative_to(case_dir.resolve()) or not source.is_file():
            continue
        target = destination / f"invocation_{index:03d}.jsonl"
        try:
            copied = _copy_log(
                source=source, target=target,
                source_budget=min(_MAX_LOG_FILE_BYTES, remaining_source), output_budget=remaining,
            )
            remaining_source -= copied["bytes_examined"]
            remaining -= copied["copied_bytes"]
            row.update(copied)
            row["path"] = target.relative_to(runtime_dir).as_posix()
        except OSError:
            row["status"] = "unreadable"
    if not manifest["logs"]:
        manifest["missing"].append("case_scoped_runtime_logs")
    manifest["log_files_omitted"] = max(0, len(expanded) - 32)
    manifest["initial_search"] = _initial_log_search(runtime_dir, manifest)
    path = runtime_dir / "evidence_manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest



def _copy_log(*, source: Path, target: Path, source_budget: int, output_budget: int) -> dict[str, Any]:
    """Copy whole native logs; sample tail first when a historical file exceeds its budget."""
    source_size = source.stat().st_size
    if source_size <= source_budget:
        windows = [(0, source_size)]
    else:
        head_size = source_budget // 2
        tail_size = source_budget - head_size
        windows = [(source_size - tail_size, source_size), (0, head_size)]
    consumed = 0
    copied_bytes = 0
    emitted = 0
    truncated_records = 0
    identities: list[dict[str, Any]] = []
    source_capture_truncated: bool | str = "unknown"
    ranges = []
    output_exhausted = False
    with source.open("rb") as reader, target.open("w", encoding="utf-8") as writer:
        for start, end in windows:
            if start == end or output_exhausted:
                continue
            reader.seek(start)
            line_number = 0
            skipping = start > 0
            alignment_bytes = 0
            record_truncated = False
            window_start = reader.tell()
            while reader.tell() < end:
                offset = reader.tell()
                raw = reader.readline(min(_MAX_RECORD_BYTES + 1, end - offset))
                if not raw:
                    break
                consumed += len(raw)
                if skipping:
                    alignment_bytes += len(raw)
                    skipping = not raw.endswith(b"\n")
                    continue
                if record_truncated:
                    record_truncated = not raw.endswith(b"\n")
                    continue
                line_number += 1
                source_line = line_number if start == 0 else None
                record_truncated = not raw.endswith(b"\n") and (
                    len(raw) >= _MAX_RECORD_BYTES or (reader.tell() == end and end < source_size)
                )
                text = raw[:_MAX_RECORD_BYTES].decode("utf-8", errors="replace")
                try:
                    event = json.loads(text)
                except ValueError:
                    event = {"excerpt": text}
                if isinstance(event, dict):
                    identity = {key: str(event[key])[:160] for key in (
                        "agent_id", "session_id", "invocation_id", "parent_invocation_id", "role", "trace_id", "member_id",
                    ) if key in event}
                    if identity and identity not in identities and len(identities) < 8:
                        identities.append(_redact(identity))
                    if "truncated" in event:
                        source_capture_truncated = bool(event["truncated"])
                record = {
                    "source_line": source_line, "source_byte_offset": offset,
                    "record_truncated": record_truncated, "event": _redact(event),
                }
                encoded = json.dumps(record, ensure_ascii=False)
                if len(encoded.encode("utf-8")) > _MAX_RECORD_BYTES:
                    record.pop("event")
                    record["record_truncated"] = True
                    record["excerpt"] = json.dumps(_redact(event), ensure_ascii=False)[:1500]
                    encoded = json.dumps(record, ensure_ascii=False)
                if record["record_truncated"]:
                    truncated_records += 1
                size = len(encoded.encode("utf-8")) + 1
                if copied_bytes + size > output_budget:
                    output_exhausted = True
                    break
                writer.write(encoded + "\n")
                copied_bytes += size
                emitted += 1
            ranges.append({
                "byte_start": window_start, "byte_end_exclusive": reader.tell(),
                "source_line_start": 1 if start == 0 and line_number else None,
                "source_line_end": line_number if start == 0 else None,
                "alignment_bytes_omitted": alignment_bytes,
            })
    return {
        "status": "available" if emitted or source_size == 0 else "budget_exhausted",
        "source_bytes": source_size, "bytes_examined": consumed, "copied_bytes": copied_bytes,
        "source_ranges": ranges, "records_copied": emitted,
        "identities": identities, "source_capture_truncated": source_capture_truncated,
        "truncated_records": truncated_records, "output_budget_exhausted": output_exhausted,
        "scan_complete": consumed == source_size,
        "truncated": consumed < source_size or bool(truncated_records) or output_exhausted,
        "redacted": True,
    }

_TERMINATION_PATTERN = re.compile(
    r"Traceback|exception|timeout|timed out|max_iterations|termination_reason|failed|invocation_end|finalization", re.I,
)


def _initial_log_search(runtime_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Provide a bounded severity-first search index over copied records only."""
    candidates = []
    for item in manifest["logs"]:
        if item.get("status") != "available":
            continue
        with (runtime_dir / item["path"]).open(encoding="utf-8") as reader:
            for copied_line, text in enumerate(reader, 1):
                record = json.loads(text)
                event = record.get("event")
                if not isinstance(event, dict):
                    continue
                level = str(event.get("level", "")).upper()
                fields = ("message", "exception", "event_type", "status", "termination_reason", "error")
                summary = " ".join(str(event[key]) for key in fields if event.get(key))
                priority = 0 if level in {"WARNING", "ERROR", "CRITICAL"} else 1
                if priority and not _TERMINATION_PATTERN.search(summary):
                    continue
                candidates.append((priority, {
                    "path": item["path"], "copied_line": copied_line,
                    "source_line": record["source_line"],
                    "source_byte_offset": record.get("source_byte_offset"), "level": level,
                    "summary": summary[:500], "summary_truncated": len(summary) > 500,
                }))
    hits = []
    selected: dict[str, list[int]] = {}
    total_bytes = 0
    omitted = 0
    for _, hit in sorted(candidates, key=lambda item: (item[0], -(item[1]["source_byte_offset"] or 0))):
        nearby = selected.setdefault(hit["path"], [])
        if any(abs(hit["copied_line"] - line) <= 1 for line in nearby):
            continue
        size = len(json.dumps(hit, ensure_ascii=False).encode("utf-8"))
        if len(hits) >= 50 or total_bytes + size > 30000:
            omitted += 1
            continue
        hits.append(hit)
        nearby.append(hit["copied_line"])
        total_bytes += size
    result = {
        "policy": "WARNING/ERROR/CRITICAL first, newest records first within priority; "
                  "then termination keywords including INFO; adjacent hits deduplicated",
        "hits": hits, "omitted_matches": omitted, "max_hits": 50, "max_summary_chars": 500,
        "max_total_bytes": 32768, "truncated": bool(omitted),
        "notice": "Only bounded copied records were searched. Missing/omitted content is unknown, not negative evidence.",
    }
    path = runtime_dir / "runtime_log_search.json"
    path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return {"path": path.name, "hits": len(hits), "truncated": bool(omitted)}
