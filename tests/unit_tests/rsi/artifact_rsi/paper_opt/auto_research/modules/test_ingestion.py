"""Covers ingest_latex's simplified, best-effort behavior (mirrors the fix
applied to paper_preprocess/latex_validation.py): only a genuinely-empty
document is fatal. Cyclic/missing includes, unresolved figures, missing
bibliography files, unclosed table/figure/abstract environments, unbalanced
braces after a section command, and exceeding max_figures all degrade to a
non-fatal entry in `PaperDocument.warnings` instead of aborting scoring for
the whole paper.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.paper_scoring.ingestion import (
    LatexIngestError,
    ingest_latex,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.paper_scoring.schemas import (
    PaperScoringSettings,
)

_SETTINGS = PaperScoringSettings(min_text_chars=1)


def _write(root: Path, name: str, content: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _write_png(root: Path, name: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (4, 4)).save(path)
    return path


_BASE_BODY = (
    r"\section{Introduction}"
    "\nSome introductory text that is long enough to be kept as section body.\n"
)


def test_missing_tex_file_is_still_fatal(tmp_path: Path):
    with pytest.raises(LatexIngestError):
        ingest_latex(tmp_path / "missing.tex", settings=_SETTINGS)


def test_full_conventional_paper_ingests_with_no_warnings(tmp_path: Path):
    main = _write(
        tmp_path,
        "main.tex",
        r"\documentclass{article}\begin{document}" + _BASE_BODY + r"\end{document}",
    )

    doc = ingest_latex(main, settings=_SETTINGS)

    assert doc.sections
    assert doc.warnings == []


def test_cyclic_include_is_skipped_not_fatal(tmp_path: Path):
    main = _write(
        tmp_path, "main.tex", r"\documentclass{article}\begin{document}\input{a}" + r"\end{document}"
    )
    _write(tmp_path, "a.tex", r"\input{main}" + _BASE_BODY)

    doc = ingest_latex(main, settings=_SETTINGS)

    assert any("cyclic" in w for w in doc.warnings)
    assert doc.sections  # the rest of a.tex after the cyclic \input was still read


def test_missing_include_is_skipped_not_fatal(tmp_path: Path):
    main = _write(
        tmp_path,
        "main.tex",
        r"\documentclass{article}\begin{document}\input{missing}" + _BASE_BODY + r"\end{document}",
    )

    doc = ingest_latex(main, settings=_SETTINGS)

    assert any("included file not found" in w for w in doc.warnings)
    assert doc.sections


def test_unresolved_figure_is_skipped_not_fatal(tmp_path: Path):
    main = _write(
        tmp_path,
        "main.tex",
        r"\documentclass{article}\begin{document}"
        + _BASE_BODY
        + r"\includegraphics{does-not-exist.png}"
        + r"\end{document}",
    )

    doc = ingest_latex(main, settings=_SETTINGS)

    assert doc.figures == []
    assert any("skipped figure" in w for w in doc.warnings)
    assert "[FIGURE unresolved" in doc.full_text


def test_unsupported_figure_type_is_skipped_not_fatal(tmp_path: Path):
    _write(tmp_path, "fig.tiff", "not really a tiff, just needs to exist")
    main = _write(
        tmp_path,
        "main.tex",
        r"\documentclass{article}\begin{document}"
        + _BASE_BODY
        + r"\includegraphics{fig.tiff}"
        + r"\end{document}",
    )

    doc = ingest_latex(main, settings=_SETTINGS)

    assert doc.figures == []
    assert any("unsupported figure type" in w for w in doc.warnings)


def test_missing_bibliography_file_is_skipped_not_fatal(tmp_path: Path):
    main = _write(
        tmp_path,
        "main.tex",
        r"\documentclass{article}\begin{document}"
        + _BASE_BODY
        + r"As shown \cite{foo2024}."
        + r"\bibliography{refs}"
        + r"\end{document}",
    )

    doc = ingest_latex(main, settings=_SETTINGS)

    assert doc.bibliography == []
    assert any("bibliography file not found" in w for w in doc.warnings)


def test_unclosed_table_environment_is_treated_as_body_text(tmp_path: Path):
    main = _write(
        tmp_path,
        "main.tex",
        r"\documentclass{article}\begin{document}"
        + _BASE_BODY
        + r"\begin{table}\caption{Unclosed}Some table-ish text without an end tag."
        + r"\end{document}",
    )

    doc = ingest_latex(main, settings=_SETTINGS)

    assert doc.sections
    assert "table-ish text" in doc.full_text


def test_unclosed_abstract_environment_is_treated_as_body_text(tmp_path: Path):
    main = _write(
        tmp_path,
        "main.tex",
        r"\documentclass{article}\begin{document}"
        + r"\begin{abstract}An abstract that never closes."
        + _BASE_BODY
        + r"\end{document}",
    )

    doc = ingest_latex(main, settings=_SETTINGS)

    assert not any(s.canonical_name == "abstract" for s in doc.sections)
    assert "abstract that never closes" in doc.full_text


def test_unbalanced_braces_after_section_command_is_treated_as_body_text(tmp_path: Path):
    main = _write(
        tmp_path,
        "main.tex",
        r"\documentclass{article}\begin{document}"
        + r"\section{Unbalanced Title"
        + "\nBody text that follows the broken heading.\n"
        + r"\section{Conclusion}"
        + "\nA real, well-formed section.\n"
        + r"\end{document}",
    )

    doc = ingest_latex(main, settings=_SETTINGS)

    assert any("unbalanced braces" in w for w in doc.warnings)
    assert "Body text that follows the broken heading" in doc.full_text
    assert any(s.name == "Conclusion" for s in doc.sections)


def test_max_figures_cap_stops_collecting_but_keeps_going(tmp_path: Path):
    _write_png(tmp_path, "fig1.png")
    _write_png(tmp_path, "fig2.png")
    main = _write(
        tmp_path,
        "main.tex",
        r"\documentclass{article}\begin{document}"
        + _BASE_BODY
        + r"\includegraphics{fig1.png}"
        + r"\includegraphics{fig2.png}"
        + r"\end{document}",
    )

    doc = ingest_latex(main, settings=PaperScoringSettings(min_text_chars=1, max_figures=1))

    assert len(doc.figures) == 1
    assert any("max_figures=1 reached" in w for w in doc.warnings)
    assert "[FIGURE omitted" in doc.full_text
