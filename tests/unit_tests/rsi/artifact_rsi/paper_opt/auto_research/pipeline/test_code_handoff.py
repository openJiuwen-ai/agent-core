"""Smoke VariantHandoff should carry metrics, not SDK log tails."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import set_project_root
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.manager.schemas import VariantHandoff
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.pipeline.subagents import (
    _code_failure_excerpts,
    _copy_runner_excerpt,
    _smoke_metrics_note,
    _variant_handoffs_from_logs,
)

_TRACE = (
    "Traceback (most recent call last):\n"
    '  File "run.py", line 142, in main\n'
    '    raise ValueError("paired_discordance")\n'
    "ValueError: paired_discordance\n"
)


@pytest.fixture(autouse=True)
def _project_root(tmp_path: Path):
    set_project_root(tmp_path)
    yield
    set_project_root(None)


def _write_smoke(
    tmp_path: Path,
    name: str,
    *,
    metrics: dict,
    log: str = "SMOKE_OK",
) -> Path:
    log_dir = tmp_path / "smoke"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{name}.log").write_text(log, encoding="utf-8")
    (log_dir / f"{name}.metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    return log_dir


def test_smoke_variant_handoff_loads_metrics_and_process_status(tmp_path: Path):
    log_dir = _write_smoke(
        tmp_path,
        "proposed",
        metrics={"accuracy": 0.875, "n_questions": 1, "model_call_count": 2},
    )

    variants, log_paths = _variant_handoffs_from_logs(
        names=["proposed"],
        log_dir=log_dir,
        passed=True,
        excerpt_limit=400,
    )

    assert len(variants) == 1
    item = variants[0]
    assert item.passed is True
    assert item.process_status == "completed"
    assert item.metrics_state == "present"
    assert item.metrics["accuracy"] == 0.875
    assert item.metrics["n_questions"] == 1
    assert item.excerpt == ""
    assert log_paths


def test_failed_variant_is_marked_failed_even_if_metrics_exist(tmp_path: Path):
    log_dir = _write_smoke(tmp_path, "proposed", metrics={"accuracy": 0.1})

    variants, _ = _variant_handoffs_from_logs(
        names=["proposed"],
        log_dir=log_dir,
        passed=False,
        excerpt_limit=400,
        failed_names={"proposed"},
    )

    assert variants[0].passed is False
    assert variants[0].process_status == "failed"
    assert variants[0].metrics["accuracy"] == 0.1


def test_failed_smoke_excerpt_copies_runner_error_tree(tmp_path: Path):
    log_dir = _write_smoke(
        tmp_path,
        "proposed",
        metrics={"accuracy": 0.1},
        log="this log must not be parsed\nTraceback (most recent call last):\n  File ignored\n",
    )
    (log_dir / "promotion.log").write_text("--- promotion ---\nstatus=ok\n", encoding="utf-8")

    variants, _ = _variant_handoffs_from_logs(
        names=["proposed"],
        log_dir=log_dir,
        passed=False,
        excerpt_limit=400,
        failed_names={"proposed"},
        smoke_failures={"proposed": "exit_code=1"},
        error_trees={"proposed": _TRACE},
    )
    excerpts = _code_failure_excerpts(
        succeeded=False,
        smoke_failures={"proposed": "exit_code=1"},
        variants=variants,
    )

    assert variants[0].excerpt == _TRACE.strip()
    assert "this log must not" not in variants[0].excerpt
    assert excerpts
    assert any("ValueError: paired_discordance" in item for item in excerpts)
    assert not any("promotion" in item.lower() for item in excerpts)


def test_failed_smoke_without_error_tree_uses_smoke_failures_not_log(tmp_path: Path):
    log_dir = _write_smoke(
        tmp_path,
        "proposed",
        metrics={"accuracy": 0.1},
        log=(
            "Traceback (most recent call last):\n"
            '  File "ignored.py", line 1\n'
            "ValueError: from log\n"
        ),
    )
    variants, _ = _variant_handoffs_from_logs(
        names=["proposed"],
        log_dir=log_dir,
        passed=False,
        excerpt_limit=400,
        failed_names={"proposed"},
        smoke_failures={"proposed": "metrics contract failed: n_questions"},
        error_trees={},
    )
    assert variants[0].excerpt == "metrics contract failed: n_questions"
    assert "from log" not in variants[0].excerpt


def test_successful_code_handoff_omits_failure_excerpts():
    excerpts = _code_failure_excerpts(
        succeeded=True,
        smoke_failures={},
        variants=[
            VariantHandoff(name="proposed", passed=True, excerpt="should not appear"),
        ],
    )
    assert excerpts == []


def test_failed_code_handoff_keeps_contract_text_when_no_traceback():
    excerpts = _code_failure_excerpts(
        succeeded=False,
        smoke_failures={"proposed": "metrics contract failed: n_questions"},
        variants=[
            VariantHandoff(
                name="proposed",
                passed=False,
                process_status="failed",
            )
        ],
    )
    assert any("metrics contract failed" in item for item in excerpts)


def test_smoke_metrics_note_summarizes_variants():
    note = _smoke_metrics_note(
        [
            VariantHandoff(
                name="proposed",
                passed=True,
                process_status="completed",
                metrics={"accuracy": 0.875},
            ),
            VariantHandoff(
                name="baseline",
                passed=True,
                process_status="completed",
                metrics={"accuracy": 0.5},
            ),
        ]
    )
    assert note.startswith("smoke metrics:")
    assert "proposed=" in note
    assert "baseline=" in note
    assert "0.875" in note


def test_copy_runner_excerpt_prefers_error_tree_over_harness_banner():
    tree = _TRACE.strip()
    assert _copy_runner_excerpt(tree, "Harness failed at llm/call") == tree
    assert _copy_runner_excerpt("", "Harness failed at llm/call") == "Harness failed at llm/call"
    assert _copy_runner_excerpt("", "", "missing metrics") == "missing metrics"
    assert _copy_runner_excerpt("") == ""
