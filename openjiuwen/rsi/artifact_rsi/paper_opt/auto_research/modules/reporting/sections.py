"""Fixed section template — see docs/paper_writing_design.md §3.

Host-owned Python constant, not a ported ``template.json``: the input shape
(survey/plan/result/reflection) never varies, so there is nothing for a
dynamic outline agent to discover — the same reasoning
``docs/reporting_design.md`` §3 used for its own fixed template.
"""

from __future__ import annotations

from dataclasses import dataclass

EvidenceKey = str


@dataclass(frozen=True)
class SectionSpec:
    id: str
    title: str
    # Which evidence block (see agent.py's _build_evidence_blocks) this
    # section is grounded in — the model is told to write from only this
    # block, mirroring reporting._build_task_prompt's per-section discipline.
    evidence_key: EvidenceKey
    min_words: int
    max_words: int
    allow_citations: bool = True


# "abstract" is drafted last in agent.py (from the other sections' finished
# text, not from a primary evidence block) despite appearing first in the
# document — see agent.py's _WRITE_ORDER.

SECTIONS: tuple[SectionSpec, ...] = (
    SectionSpec("abstract", "Abstract", "abstract", 150, 220, allow_citations=False),
    SectionSpec("introduction", "Introduction", "background", 700, 1200),
    SectionSpec("related_work", "Related Work", "background", 400, 800),
    SectionSpec("method", "Method", "method", 2000, 3000),
    SectionSpec("experiments", "Experiments", "results", 1000, 1500),
    SectionSpec("discussion", "Discussion", "discussion", 900, 1400),
    SectionSpec("conclusion", "Conclusion", "discussion", 200, 280, allow_citations=False),
)

# modify_paper still writes a new draft, but from a prior paper plus one
# follow-up rather than a from-scratch 2000-word Method. Same maxima;
# lower floors so a complete compiled PDF beats padding.
_MODIFY_MIN_WORDS: dict[str, int] = {
    "abstract": 120,
    "introduction": 500,
    "related_work": 400,
    "method": 800,
    "experiments": 500,
    "discussion": 500,
    "conclusion": 180,
}

LINT_OVERRIDES_FILENAME = "lint_overrides.json"

# Document order == SECTIONS order above; this is a separate list only
# because agent.py needs to draft "abstract" last while still emitting it
# first in the compiled document.
DOCUMENT_ORDER: tuple[str, ...] = tuple(section.id for section in SECTIONS)


def section_by_id(section_id: str) -> SectionSpec:
    for section in SECTIONS:
        if section.id == section_id:
            return section
    raise KeyError(f"unknown section id: {section_id!r}")


def section_specs(*, modify_paper: bool = False) -> tuple[SectionSpec, ...]:
    if not modify_paper:
        return SECTIONS
    return tuple(
        SectionSpec(
            spec.id,
            spec.title,
            spec.evidence_key,
            _MODIFY_MIN_WORDS.get(spec.id, spec.min_words),
            spec.max_words,
            spec.allow_citations,
        )
        for spec in SECTIONS
    )


def apply_word_band_overrides(spec: SectionSpec, overrides: dict | None) -> SectionSpec:
    """Apply host-written ``lint_overrides.json`` bands to a section spec."""
    if not overrides:
        return spec
    mins = overrides.get("min_words") or {}
    maxs = overrides.get("max_words") or {}
    return SectionSpec(
        spec.id,
        spec.title,
        spec.evidence_key,
        int(mins.get(spec.id, spec.min_words)),
        int(maxs.get(spec.id, spec.max_words)),
        spec.allow_citations,
    )


def word_band_instructions(specs: tuple[SectionSpec, ...]) -> str:
    bands = "; ".join(f"{spec.title} {spec.min_words}-{spec.max_words}" for spec in specs)
    return (
        "Word-count targets for this modify_paper run override ts-write's create-paper "
        f"defaults. Host lint uses the same bands: {bands}. Prefer a complete compiled "
        "paper over stretching Method to 2000 words."
    )
