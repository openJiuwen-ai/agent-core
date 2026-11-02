"""Covers reporting/lint.py::check_structure's per-section structural
invariants, focused on the "experiments" section: it must not enforce a
fixed subsection count, since an append-only modify-paper run legitimately
produces "however many the baseline already had, plus however many this
run appended" rather than any fixed template shape. Introduction/Related
Work keep their exactly-3 invariant unchanged -- this file also guards
that those were not accidentally loosened by the same edit.
"""

from __future__ import annotations

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting.lint import (
    check_structure,
)

_ONE_SUBSECTION = "\\subsection{Paired Verifier Intervention}\nSome new prose."

_FOUR_SUBSECTIONS = (
    "\\subsection{Implementation Details}\nText.\n"
    "\\subsection{Experimental Design}\nText.\n"
    "\\subsection{Results}\nText.\n"
    "\\subsection{Paired Verifier Intervention}\nText."
)

_THREE_SUBSECTIONS = (
    "\\subsection{Implementation Details}\nText.\n"
    "\\subsection{Experimental Design}\nText.\n"
    "\\subsection{Results}\nText."
)


def test_experiments_accepts_a_single_appended_subsection():
    violations = check_structure("experiments", _ONE_SUBSECTION)
    assert not any("subsection" in v for v in violations)


def test_experiments_accepts_four_subsections_after_append():
    violations = check_structure("experiments", _FOUR_SUBSECTIONS)
    assert not any("subsection" in v for v in violations)


def test_experiments_still_accepts_the_from_scratch_three_subsection_shape():
    violations = check_structure("experiments", _THREE_SUBSECTIONS)
    assert not any("subsection" in v for v in violations)


def test_introduction_still_requires_exactly_three_item_contributions():
    violations = check_structure("introduction", "\\item one\n\\item two")
    assert any("expected exactly 3 \\item contributions, found 2" in v for v in violations)


def test_related_work_still_requires_exactly_three_subsection_themes():
    violations = check_structure(
        "related_work",
        "\\subsection{A}\nText.\n\\subsection{B}\nText.",
    )
    assert any("expected exactly 3 \\subsection themes, found 2" in v for v in violations)
