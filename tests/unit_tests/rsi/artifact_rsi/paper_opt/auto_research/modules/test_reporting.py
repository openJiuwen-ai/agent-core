"""Covers ReportingAgent._verify_and_build_output's tex-only fallback: a
clean main.tex should still ship as status="compiled" instead of failing
and burning the manager's reporting retry budget on a problem retrying
can never fix. Three independent triggers are covered: no latexmk/pdflatex
on PATH, a ts-latex skill deployment missing its scripts/ directory, and
the manager's reporting retry budget being exhausted. Toolchain discovery
itself (LatexRuntime/discover_latex_runtime/preflight_latex_runtime) is
covered by test_paper_latex_runtime.py; this file only covers the
success/failure gate in reporting/agent.py::_verify_and_build_output.
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
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting.agent import (
    _MATERIALIZED_SKILLS_DIRNAME,
    ReportingAgent,
)
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


def _verify(
    run_id: str,
    *,
    toolchain_available: bool,
    skill_scripts_present: bool = True,
    is_final_attempt: bool = False,
):
    agent = ReportingAgent({})
    # Bypass the real discover_latex_runtime() probe -- _run_async normally
    # resolves this once up front and _verify_and_build_output just reuses
    # it, so a fake with the one attribute the gate reads is enough here.
    agent._latex_runtime = SimpleNamespace(available=toolchain_available)
    # _build_paper_agent normally materializes the ts-latex skill (incl.
    # scripts/compile.py) into the workspace before the session runs;
    # _verify_and_build_output is exercised here without going through
    # that step, so a healthy deployment has to be faked explicitly --
    # otherwise every case here would spuriously look like the "skill
    # deployment is missing scripts/" environment failure.
    if skill_scripts_present:
        scripts_dir = paper_workspace_dir(run_id) / _MATERIALIZED_SKILLS_DIRNAME / "ts-latex" / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        (scripts_dir / "compile.py").write_text("", encoding="utf-8")
    result = ExperimentResult(run_id=run_id, workspace_dir=str(paper_workspace_dir(run_id)))
    return agent._verify_and_build_output(
        run_id=run_id,
        workspace=paper_workspace_dir(run_id),
        sections_dir=paper_sections_dir(run_id),
        refs_bib_path=paper_refs_bib_path(run_id),
        figure_paths=[],
        known_keys=set(),
        result=result,
        is_final_attempt=is_final_attempt,
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


def test_compiled_output_exposes_lint_issues_as_a_list():
    run_id = "rsi-test-lint-issues"
    _write_minimal_sections(run_id)
    paper_tex_path(run_id).write_text("\\documentclass{article}\\begin{document}x\\end{document}", encoding="utf-8")

    output = _verify(run_id, toolchain_available=False)

    assert output.status == "compiled"
    assert output.lint_issues
    assert any("no LaTeX toolchain found" in issue for issue in output.lint_issues)


def test_failed_output_exposes_lint_issues_as_a_list():
    run_id = "rsi-test-lint-failed"
    _write_minimal_sections(run_id)

    output = _verify(run_id, toolchain_available=False)

    assert output.status == "failed"
    assert output.lint_issues
    assert any("no compiled PDF" in issue for issue in output.lint_issues)


def test_latex_skill_missing_scripts_ships_tex_even_with_toolchain_present():
    # A skill deployment that materializes ts-latex without its scripts/
    # directory severs the only bridge between a host-discovered toolchain
    # and the agent's own shell -- no retry fixes a file that was never
    # copied, so this must ship tex-only on the very first attempt rather
    # than waiting for the manager's reporting retry budget to run out.
    run_id = "rsi-test-skill-incomplete"
    _write_minimal_sections(run_id)
    paper_tex_path(run_id).write_text("\\documentclass{article}\\begin{document}x\\end{document}", encoding="utf-8")

    output = _verify(run_id, toolchain_available=True, skill_scripts_present=False)

    assert output.status == "compiled"
    assert output.paper_pdf_path is not None
    assert output.paper_pdf_path.endswith(".tex")
    assert "scripts/ directory" in (output.notes or "")


def test_final_attempt_with_valid_tex_ships_even_though_pdf_missing():
    # Neither toolchain-missing nor skill-incomplete is detected here, so
    # an earlier attempt would correctly keep failing (see
    # test_toolchain_present_pdf_missing_still_fails) -- but once the
    # manager has no reporting retries left, losing the whole node over a
    # rendering-only gap is worse than shipping the tex it already
    # verified.
    run_id = "rsi-test-final-attempt-fallback"
    _write_minimal_sections(run_id)
    paper_tex_path(run_id).write_text("\\documentclass{article}\\begin{document}x\\end{document}", encoding="utf-8")

    output = _verify(run_id, toolchain_available=True, is_final_attempt=True)

    assert output.status == "compiled"
    assert output.paper_pdf_path is not None
    assert output.paper_pdf_path.endswith(".tex")
    assert "exhausting all reporting retries" in (output.notes or "")


def test_hallucinated_citation_fails_even_on_final_attempt():
    # The final-attempt safety net only covers a rendering-only gap -- it
    # must not paper over a genuine content problem just because the
    # retry budget is spent.
    run_id = "rsi-test-hallucinated-final-attempt"
    _write_minimal_sections(run_id, cite_key="not-a-real-key")
    paper_tex_path(run_id).write_text("\\documentclass{article}\\begin{document}x\\end{document}", encoding="utf-8")

    output = _verify(run_id, toolchain_available=True, is_final_attempt=True)

    assert output.status == "failed"
    assert output.paper_pdf_path is None


def test_task_query_inlines_manager_contract_brief():
    query = ReportingAgent._build_task_query(
        {"design": "the living design"},
        "",
        contract_brief="# Manager subtask contract\n\n## Goal\n\ncompare against the baseline",
    )

    assert "## Manager subtask contract" in query
    assert "compare against the baseline" in query
    assert "## Evidence: design" in query


def test_task_query_omits_contract_section_when_brief_is_empty():
    query = ReportingAgent._build_task_query({"design": "the living design"}, "")

    assert "## Manager subtask contract" not in query


def test_task_query_keeps_repair_instruction_and_contract_brief():
    query = ReportingAgent._build_task_query(
        {"design": "the living design"},
        "abstract is 40 words short",
        contract_brief="## Goal\n\nfinish the paper",
    )

    assert "abstract is 40 words short" in query
    assert "## Manager subtask contract" in query
    assert "finish the paper" in query
