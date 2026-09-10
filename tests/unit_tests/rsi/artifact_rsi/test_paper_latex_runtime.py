"""Tests for host LaTeX/MiKTeX discovery and readiness checks."""

import json
import os
from types import SimpleNamespace

import pytest

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting import agent as reporting_agent
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting import latex_runtime
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import set_project_root


def test_discover_uses_explicit_directory_and_preserves_path(tmp_path, monkeypatch):
    executable = tmp_path / "pdflatex"
    found = {"latexmk": None, "pdflatex": str(executable)}

    monkeypatch.setattr(latex_runtime.shutil, "which", lambda name, path=None: found[name])
    runtime = latex_runtime.discover_latex_runtime(tmp_path, environ={"PATH": "/existing/bin"})

    assert runtime.latexmk is None
    assert runtime.pdflatex == executable
    environment = runtime.with_environment({"PATH": "/existing/bin"})
    path_entries = environment["PATH"].split(os.pathsep)
    assert path_entries[0] == str(tmp_path)
    assert "/existing/bin" in path_entries
    assert environment["LATEX_BIN_DIR"] == str(tmp_path)


def test_preflight_accepts_one_working_engine(tmp_path, monkeypatch):
    executable = tmp_path / "pdflatex"
    monkeypatch.setattr(latex_runtime.shutil, "which", lambda name, path=None: str(executable))
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(latex_runtime.subprocess, "run", fake_run)
    runtime = latex_runtime.preflight_latex_runtime(tmp_path, environ={"PATH": ""})

    assert runtime.pdflatex == executable
    assert calls[0][0] == [str(executable), "--version"]
    assert calls[0][1]["env"]["LATEX_BIN_DIR"] == str(tmp_path)


def test_preflight_reports_missing_compiler(monkeypatch):
    monkeypatch.setattr(latex_runtime.shutil, "which", lambda name, path=None: None)

    with pytest.raises(latex_runtime.LatexRuntimeError, match="No LaTeX compiler was found"):
        latex_runtime.preflight_latex_runtime(environ={"PATH": ""})


def test_with_environment_does_not_mutate_process_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("LATEX_BIN_DIR", raising=False)
    original_path = os.environ.get("PATH")
    runtime = latex_runtime.LatexRuntime(
        latexmk=None,
        pdflatex=tmp_path / "pdflatex",
        search_dirs=(tmp_path,),
    )

    child_environment = runtime.with_environment()

    assert child_environment["LATEX_BIN_DIR"] == str(tmp_path)
    assert os.environ.get("LATEX_BIN_DIR") is None
    assert os.environ.get("PATH") == original_path


def test_reporting_writes_latex_runtime_config(tmp_path):
    workspace = tmp_path / "paper"
    runtime = latex_runtime.LatexRuntime(None, tmp_path / "pdflatex", ())
    agent = reporting_agent.ReportingAgent({"reporting": {}})
    agent._latex_runtime = runtime

    agent._write_latex_runtime_config(workspace)

    config_path = workspace / ".latex-runtime.json"
    assert json.loads(config_path.read_text(encoding="utf-8")) == {"latex_bin_dir": str(tmp_path)}


@pytest.mark.asyncio
async def test_reporting_agent_continues_after_preflight_failure(tmp_path, monkeypatch):
    set_project_root(tmp_path)
    run_calls = []

    def fail_preflight(*args, **kwargs):
        raise latex_runtime.LatexRuntimeError("missing")

    monkeypatch.setattr(
        reporting_agent,
        "preflight_latex_runtime",
        fail_preflight,
    )
    monkeypatch.setattr(
        reporting_agent,
        "discover_latex_runtime",
        lambda *args, **kwargs: latex_runtime.LatexRuntime(None, None, ()),
    )
    monkeypatch.setattr(reporting_agent.figures, "build_results_figure", lambda *args: None)
    monkeypatch.setattr(reporting_agent.ReportingAgent, "_build_evidence_blocks", lambda *args: {})

    async def fake_run_paper_agent(self, *, run_id, query):
        run_calls.append((run_id, query))
        return None

    monkeypatch.setattr(reporting_agent.ReportingAgent, "_run_paper_agent", fake_run_paper_agent)

    def fake_verify(self, **kwargs):
        return reporting_agent.ReportingOutput(
            status="failed",
            sections_dir=str(kwargs["sections_dir"]),
            refs_bib_path=str(kwargs["refs_bib_path"]),
            notes=kwargs.get("preflight_note"),
        )

    monkeypatch.setattr(reporting_agent.ReportingAgent, "_verify_and_build_output", fake_verify)

    class _Result:
        def model_dump_json(self):
            return "{}"

    inputs = SimpleNamespace(
        plan=SimpleNamespace(run_id="latex-preflight", design_path=""),
        survey=SimpleNamespace(resource_paths=["missing-summary.md"]),
        result=_Result(),
        attempt=1,
        repair_instruction="",
    )

    try:
        output = await reporting_agent.ReportingAgent({"reporting": {"latex_preflight": True}})._run_async(inputs)
    finally:
        set_project_root(None)

    assert output.status == "failed"
    assert run_calls
    assert "reporting will continue" in run_calls[0][1]
    assert "preserve source artifacts" in (output.notes or "")
