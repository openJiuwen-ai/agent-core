"""Covers ReportingAgent._verify_and_build_output's tex-only fallback: when
this environment has no latexmk/pdflatex, a clean main.tex should still ship
as status="compiled" instead of failing and burning the manager's reporting
retry budget on a problem retrying can never fix. Toolchain discovery itself
(LatexRuntime/discover_latex_runtime/preflight_latex_runtime) is covered by
test_paper_latex_runtime.py; this file only covers the success/failure gate
in reporting/agent.py::_verify_and_build_output.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import (
    paper_output_path,
    paper_refs_bib_path,
    paper_sections_dir,
    paper_tex_path,
    paper_workspace_dir,
    set_project_root,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_execution.schemas import (
    ExperimentResult,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting.agent import ReportingAgent
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting.sections import DOCUMENT_ORDER


def _write_minimal_sections(run_id: str, *, cite_key: str | None = None) -> None:
    sections_dir = paper_sections_dir(run_id)
    sections_dir.mkdir(parents=True, exist_ok=True)
    for section_id in DOCUMENT_ORDER:
        text = "Some placeholder prose for this section."
        if cite_key and section_id == "related_work":
            text += f" This builds on prior work \\cite{{{cite_key}}}."
        (sections_dir / f"{section_id}.tex").write_text(text, encoding="utf-8")
    paper_refs_bib_path(run_id).write_text("", encoding="utf-8")


def _verify(run_id: str, *, toolchain_available: bool):
    agent = ReportingAgent({})
    # Bypass the real discover_latex_runtime() probe -- _run_async normally
    # resolves this once up front and _verify_and_build_output just reuses
    # it, so a fake with the one attribute the gate reads is enough here.
    agent._latex_runtime = SimpleNamespace(available=toolchain_available)
    result = ExperimentResult(run_id=run_id, workspace_dir=str(paper_workspace_dir(run_id)))
    return agent._verify_and_build_output(
        run_id=run_id,
        workspace=paper_workspace_dir(run_id),
        sections_dir=paper_sections_dir(run_id),
        refs_bib_path=paper_refs_bib_path(run_id),
        figure_paths=[],
        known_keys=set(),
        result=result,
    )


@pytest.fixture(autouse=True)
def _isolated_project_root(tmp_path):
    set_project_root(tmp_path)
    yield
    set_project_root(None)


def test_toolchain_missing_clean_tex_ships_as_compiled():
    run_id = "rsi-test-tex-only"
    _write_minimal_sections(run_id)
    paper_tex_path(run_id).write_text("\\documentclass{article}\\begin{document}x\\end{document}", encoding="utf-8")

    output = _verify(run_id, toolchain_available=False)

    assert output.status == "compiled"
    assert output.paper_pdf_path is not None
    assert output.paper_pdf_path.endswith(".tex")
    assert "no LaTeX toolchain found" in (output.notes or "")


def test_toolchain_missing_and_no_tex_still_fails():
    run_id = "rsi-test-no-tex"
    _write_minimal_sections(run_id)
    # ts-latex never ran: neither main.tex nor main.pdf exists.

    output = _verify(run_id, toolchain_available=False)

    assert output.status == "failed"
    assert output.paper_pdf_path is None


def test_toolchain_present_pdf_missing_still_fails():
    run_id = "rsi-test-real-compile-error"
    _write_minimal_sections(run_id)
    paper_tex_path(run_id).write_text("\\documentclass{article}\\begin{document}x\\end{document}", encoding="utf-8")
    # toolchain is present but compilation genuinely failed: no main.pdf.

    output = _verify(run_id, toolchain_available=True)

    assert output.status == "failed"
    assert output.paper_pdf_path is None


def test_hallucinated_citation_fails_even_with_toolchain_missing():
    run_id = "rsi-test-hallucinated-citation"
    _write_minimal_sections(run_id, cite_key="not-a-real-key")
    paper_tex_path(run_id).write_text("\\documentclass{article}\\begin{document}x\\end{document}", encoding="utf-8")

    output = _verify(run_id, toolchain_available=False)

    assert output.status == "failed"
    assert output.paper_pdf_path is None


def test_pdf_present_takes_priority_over_tex():
    run_id = "rsi-test-pdf-present"
    _write_minimal_sections(run_id)
    paper_tex_path(run_id).write_text("\\documentclass{article}\\begin{document}x\\end{document}", encoding="utf-8")
    paper_output_path(run_id).write_bytes(b"%PDF-1.4 fake")

    output = _verify(run_id, toolchain_available=False)

    assert output.status == "compiled"
    assert output.paper_pdf_path is not None
    assert output.paper_pdf_path.endswith(".pdf")
