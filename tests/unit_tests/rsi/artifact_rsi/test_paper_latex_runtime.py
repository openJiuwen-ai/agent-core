"""Tests for host LaTeX/MiKTeX discovery and readiness checks."""

import os
from types import SimpleNamespace

import pytest

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting import agent as reporting_agent
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting import latex_runtime


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


@pytest.mark.asyncio
async def test_reporting_agent_fails_before_model_when_preflight_fails(monkeypatch):
    monkeypatch.setattr(
        reporting_agent,
        "preflight_latex_runtime",
        lambda *args, **kwargs: (_ for _ in ()).throw(latex_runtime.LatexRuntimeError("missing")),
    )
    inputs = SimpleNamespace(plan=SimpleNamespace(run_id="latex-preflight"))

    output = await reporting_agent.ReportingAgent({"reporting": {"latex_preflight": True}})._run_async(inputs)

    assert output.status == "failed"
    assert "before reporting started" in (output.notes or "")
