"""Covers validate_latex_paper's simplified, best-effort behavior: only a
missing directory or a missing main.tex is fatal. Everything else (preamble
shape, title/abstract, section-naming conventions, figures, bibliography)
degrades to a non-fatal `warnings` entry instead of rejecting the paper --
see the module docstring in latex_validation.py for why.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.paper_preprocess.latex_validation import (
    validate_latex_paper,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.paper_preprocess.schemas import (
    LatexValidationError,
)


def _write(root: Path, name: str, content: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_missing_directory_is_still_fatal(tmp_path: Path):
    with pytest.raises(LatexValidationError):
        validate_latex_paper(tmp_path / "does-not-exist")


def test_missing_main_tex_is_still_fatal(tmp_path: Path):
    tmp_path.joinpath("empty-dir").mkdir()
    with pytest.raises(LatexValidationError):
        validate_latex_paper(tmp_path / "empty-dir")


def test_full_conventional_paper_has_no_warnings(tmp_path: Path):
    _write(
        tmp_path,
        "main.tex",
        r"""
\documentclass{article}
\title{A Test Paper}
\begin{document}
\begin{abstract}
This is a test abstract.
\end{abstract}
\section{Introduction}
Some intro text.
\section{Experiment}
We ran an experiment \cite{foo2024}.
\section{Conclusion}
We conclude.
\bibliography{refs}
\end{document}
""",
    )
    _write(tmp_path, "refs.bib", "@article{foo2024,\n  title={Foo},\n}\n")

    doc = validate_latex_paper(tmp_path)

    assert doc.title == "A Test Paper"
    assert doc.abstract
    assert len(doc.sections) == 3
    assert doc.warnings == []


def test_non_empirical_paper_without_experiment_or_conclusion_sections_is_accepted(tmp_path: Path):
    """No 'experiment/result/evaluation' or 'conclusion/discussion' section
    at all -- a survey/position paper shape the old validator hard-rejected
    outright. Must now succeed with readable text, no warning about it
    (that check was a structural assumption, not a resilience concern, so
    it was removed rather than downgraded to a warning)."""
    _write(
        tmp_path,
        "main.tex",
        r"""
\documentclass{article}
\title{A Survey of Things}
\begin{document}
\begin{abstract}
A survey abstract.
\end{abstract}
\section{Background}
Prior work.
\section{Open Problems}
Future directions.
\end{document}
""",
    )

    doc = validate_latex_paper(tmp_path)

    assert doc.title == "A Survey of Things"
    assert [s.title for s in doc.sections] == ["Background", "Open Problems"]
    assert doc.warnings == []


def test_title_with_optional_short_title_arg_degrades_to_a_warning(tmp_path: Path):
    """`\\title[Short]{Full}` (ACM/IEEE-style optional short title) isn't
    recognized by the title regex -- must not be fatal, just noted."""
    _write(
        tmp_path,
        "main.tex",
        r"""
\documentclass{article}
\title[Short]{The Full Title}
\begin{document}
\begin{abstract}
Abstract text.
\end{abstract}
\section{Intro}
Text.
\end{document}
""",
    )

    doc = validate_latex_paper(tmp_path)

    assert doc.title == ""
    assert any("\\title" in w for w in doc.warnings)


def test_unresolved_figure_degrades_to_a_warning(tmp_path: Path):
    """A \\graphicspath-relative figure this resolver can't find must not
    block ingestion -- only the figure list is incomplete."""
    _write(
        tmp_path,
        "main.tex",
        r"""
\documentclass{article}
\title{T}
\begin{document}
\begin{abstract}
A.
\end{abstract}
\section{Intro}
See \includegraphics{fig1}.
\end{document}
""",
    )

    doc = validate_latex_paper(tmp_path)

    assert doc.figure_paths == []
    assert any("figure" in w for w in doc.warnings)


def test_biblatex_style_citations_without_a_bibtex_file_degrades_to_a_warning(tmp_path: Path):
    """\\addbibresource (biblatex) isn't recognized by the \\bibliography-only
    regex, so a \\cite with no matching \\bibliography{} reads as "citations
    with no local bib file" -- must not be fatal."""
    _write(
        tmp_path,
        "main.tex",
        r"""
\documentclass{article}
\title{T}
\begin{document}
\begin{abstract}
A.
\end{abstract}
\section{Intro}
As shown \cite{foo2024}.
\end{document}
""",
    )

    doc = validate_latex_paper(tmp_path)

    assert doc.bibliography_paths == []
    assert any("bibliography" in w for w in doc.warnings)


def test_cyclic_include_does_not_hang_or_raise(tmp_path: Path):
    _write(tmp_path, "main.tex", r"\documentclass{article}\input{a}")
    _write(tmp_path, "a.tex", r"\input{main}")

    doc = validate_latex_paper(tmp_path)

    assert any("cyclic" in w for w in doc.warnings)
