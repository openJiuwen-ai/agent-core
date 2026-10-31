"""Per-module model usage summed from a run's agent traces and written to report.json."""

from __future__ import annotations

import json

import pytest

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import (
    manager_report_path,
    modules_dir,
    set_project_root,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.extensions.rails.observability_rail import (
    summarize_model_usage,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.manager.artifacts import write_crash_terminal

_RUN = "usage-run"


@pytest.fixture(autouse=True)
def _project(tmp_path):
    set_project_root(tmp_path)
    try:
        yield tmp_path
    finally:
        set_project_root(None)


def _trace(module: str, attempt: str | None, records: list[dict], tail: str = ""):
    directory = modules_dir(_RUN) / module
    if attempt is not None:
        directory = directory / attempt
    directory.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(record) for record in records]
    (directory / "agent_trace.jsonl").write_text("\n".join(lines) + "\n" + tail, encoding="utf-8")


def test_usage_is_summed_per_module_across_attempts():
    _trace("code_implementation", "round_001_attempt_001", [
        {"event": "trace_start"},
        {"event": "model_call_end", "call_id": "m1",
         "usage": {"input_tokens": 100, "output_tokens": 30, "cache_read_tokens": 20}},
        {"event": "model_call_end", "call_id": "m2", "usage": {"prompt_tokens": 50, "completion_tokens": 5}},
        {"event": "tool_call_end", "call_id": "t1"},
    ])
    _trace("code_implementation", "round_002_attempt_001", [
        {"event": "model_call_end", "call_id": "m1",
         "usage": {"input_tokens": 10, "output_tokens": 1, "prompt_cache_hit_tokens": 4}},
    ])
    _trace("manager", None, [
        {"event": "model_call_end", "call_id": "m1", "usage": {"input_tokens": 7, "output_tokens": 2}},
    ])

    assert summarize_model_usage(_RUN) == {
        "code_implementation": {
            "calls": 3, "failed_calls": 0, "calls_without_usage": 0,
            "input_tokens": 160, "output_tokens": 36, "cache_hit_tokens": 24,
        },
        "manager": {
            "calls": 1, "failed_calls": 0, "calls_without_usage": 0,
            "input_tokens": 7, "output_tokens": 2, "cache_hit_tokens": 0,
        },
    }


def test_failed_calls_and_calls_without_usage_are_counted_not_estimated():
    _trace(
        "reflection",
        "round_001_attempt_001",
        [
            {"event": "model_call_start", "call_id": "m1"},
            {"event": "model_call_error", "call_id": "m1", "error": {"type": "APIConnectionError"}},
            # after_model_call closes the failed call again, without a call id or usage.
            {"event": "model_call_end", "call_id": None, "usage": {}},
            {"event": "model_call_end", "call_id": "m2", "usage": {}},
            {"event": "model_call_end", "call_id": "m3", "usage": {"input_tokens": 9, "output_tokens": 3}},
        ],
        tail='{"event": "model_call_end", "call_id": "m4", "usa',  # a line still being written
    )

    assert summarize_model_usage(_RUN)["reflection"] == {
        "calls": 2, "failed_calls": 1, "calls_without_usage": 1,
        "input_tokens": 9, "output_tokens": 3, "cache_hit_tokens": 0,
    }


def test_a_run_without_traces_has_no_usage():
    assert summarize_model_usage(_RUN) == {}


def test_terminal_report_records_the_usage():
    _trace("experiment_design", "round_001_attempt_001", [
        {"event": "model_call_end", "call_id": "m1", "usage": {"input_tokens": 11, "output_tokens": 4}},
    ])

    write_crash_terminal(run_id=_RUN, status="failed", reason="stopped for the test")

    report = json.loads(manager_report_path(_RUN).read_text(encoding="utf-8"))
    assert report["model_usage"]["experiment_design"]["calls"] == 1
    assert report["model_usage"]["experiment_design"]["input_tokens"] == 11
