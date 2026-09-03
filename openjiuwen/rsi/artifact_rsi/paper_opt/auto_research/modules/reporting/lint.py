"""Deterministic per-section and per-document checks.

See docs/paper_writing_design.md §6: this module's own checks, not a ported
``draft_lint.py`` — the same "host verifies, doesn't trust the model"
principle ``reporting._verify_and_annotate`` already established, applied
per-section (word band, fabricated numbers, banned phrases) plus one
document-level hard gate (citation fabrication) this module has that
``reporting`` doesn't need, since ``reporting`` emits no citations at all.
"""

from __future__ import annotations

import re

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_execution.schemas import ExperimentResult
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting.latex import escape_latex
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting.sections import SectionSpec

_NUMBER_RE = re.compile(r"-?\d+\.\d+%?|-?\d+%")
_NUMBER_TOLERANCE = 1e-6

# A decimal immediately preceded by a hyperparameter cue ("learning rate of
# 0.001") is a design constant, not a claimed result — ported verbatim from
# spark-to-paper-skills' draft_lint.py HYPERPARAM_CUE (fetched and read
# directly from its actual source, not reconstructed from a summary), which
# exempts exactly this pattern from its own fabricated-number scan.
_HYPERPARAM_CUE_RE = re.compile(
    r"\b(?:learning[\s-]?rate|lr|dropout|momentum|weight[\s-]?decay|decay|"
    r"temperature|label[\s-]?smoothing|warm[\s-]?up|step[\s-]?size|epsilon)\b"
    r"\s*(?:of|=|:|to|at|is|was)?\s*$",
    re.IGNORECASE,
)

# Ported verbatim (regex and rationale) from spark-to-paper-skills'
# draft_lint.py AI_TELLS — phrases context-free enough that a regex flags
# them with ~zero false positives. Their own comment explains why broader
# words like "leverage" or connectives like "moreover"/"furthermore" are
# deliberately NOT here: acceptability of those depends on context a regex
# can't judge, and banning them outright pushed their model toward
# fragmented "comma soup" prose instead. Left as their judgment call, not
# re-litigated here.
_AI_TELLS_RE = re.compile(
    r"\bit is worth (noting|mentioning|emphasi[sz]ing)\b"
    r"|\bit should be noted\b"
    r"|\bplays? a (crucial|key|vital|pivotal|critical|central|significant) role\b"
    r"|\ba testament to\b"
    r"|\b(rich|intricate)\s+tapestry\b|\btapestry of\b"
    r"|\bdelv(e|es|ing) into\b"
    r"|\b(in|within) the realm of\b"
    r"|\bever-(evolving|changing|growing)\b"
    r"|\bin today'?s (world|era|landscape)\b"
    r"|\bnavigat(e|es|ing) the (landscape|complexit|intricac)"
    r"|\bparadigm shift\b|\bgame[- ]chang(er|ing)\b"
    r"|\bin order to\b",
    re.IGNORECASE,
)

# Same four invariant checks draft_lint.py runs on every section regardless
# of section-specific recipe (banned LaTeX commands in prose, a heading
# that starts with a digit, a markdown fence) — ported as-is, see
# check_structure below.
_BANNED_LATEX_RE = re.compile(r"\\textbf|\\textit\{|\\documentclass|\\usepackage|\\begin\{document\}")
_HEADING_NUM_RE = re.compile(r"\\(?:sub)*section\*?\{\s*\d")

_CITE_RE = re.compile(r"\\cite[a-zA-Z]*\{([^}]+)\}")
_ITEM_RE = re.compile(r"\\item\b")
_SUBSECTION_RE = re.compile(r"\\subsection\b")
_NOTATION_HEADING_RE = re.compile(r"\\subsection\{\s*Notation", re.IGNORECASE)


def check_structure(section_id: str, text: str) -> list[str]:
    """Structural + invariant checks ported from spark-to-paper-skills'
    draft_lint.py (fetched and read directly from its actual source, not a
    summary): exactly-3 \\item contributions in Introduction, exactly-3
    themed \\subsections in Related Work, a \\subsection{Notation} in
    Method, exactly-3 named \\subsections in Experiments (their
    `contrib_items`/`subsection_count`/`require_notation_table` recipe
    checks), plus the invariant checks every section gets there regardless
    of recipe (banned LaTeX in prose, a numbered heading, a markdown
    fence). Their real script reads these target counts from a
    `template.json` per venue; this project has one fixed template
    (sections.py), so the targets are inlined directly rather than
    threading a template file through for a single fixed shape."""
    violations: list[str] = []
    if section_id == "introduction":
        n = len(_ITEM_RE.findall(text))
        if n != 3:
            violations.append(f"expected exactly 3 \\item contributions, found {n}")
    elif section_id == "related_work":
        n = len(_SUBSECTION_RE.findall(text))
        if n != 3:
            violations.append(f"expected exactly 3 \\subsection themes, found {n}")
    elif section_id == "method":
        if not _NOTATION_HEADING_RE.search(text):
            violations.append("missing \\subsection{Notation}")
    elif section_id == "experiments":
        n = len(_SUBSECTION_RE.findall(text))
        if n != 3:
            violations.append(
                f"expected exactly 3 \\subsection (Implementation Details, Experimental "
                f"Design, Results), found {n}"
            )
    elif section_id == "discussion":
        if not _SUBSECTION_RE.search(text):
            violations.append("missing a \\subsection (e.g. Limitations and Failure Modes)")
    if _BANNED_LATEX_RE.search(text):
        violations.append(
            "banned LaTeX command in prose (\\textbf/\\textit/\\documentclass/\\usepackage/\\begin{document})"
        )
    if _HEADING_NUM_RE.search(text):
        violations.append("section/subsection heading starts with a number")
    if "```" in text:
        violations.append("markdown code fence not allowed")
    return violations


_ROUNDING_PRECISIONS = (1, 2, 3, 4, 6)


def known_numbers(result: ExperimentResult) -> set[float]:
    """Same construction as ``reporting.agent.ReportingAgent._known_numbers``
    — every real metric value, plus its ``* 100`` percent-of-fraction
    equivalent, so "0.62" and "62%" both verify against the same value.

    Also includes each value rounded to 1-4 (and 6) decimal places. Real
    metrics routinely carry full float precision
    (``0.29666666666666667``); no one writes that in prose — a human
    naturally rounds it ("0.2967", "0.3"). Without this, a real,
    correctly-cited number gets flagged as fabricated purely because it
    was rounded for readability (confirmed against a real run: HotpotQA's
    ``sp_f1=0.29666666666666667`` written as "0.3"/"0.2967", both
    rejected before this fix). Deliberately stops at 4 significant
    rounding levels rather than also adding ``round(value, 0)`` — rounding
    a fraction metric to a bare integer (0 or 1) would make the check
    accept almost anything in range and defeat its purpose."""
    numbers: set[float] = set()
    for variant in result.variants:
        for value in variant.metrics.values():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            for candidate in (float(value), float(value) * 100):
                for precision in _ROUNDING_PRECISIONS:
                    numbers.add(round(candidate, precision))
    return numbers


def _extract_unmatched_numbers(text: str, known: set[float]) -> list[float]:
    """Same fabricated-number scan as before, plus the HYPERPARAM_CUE
    exemption ported above: a decimal immediately preceded by a
    hyperparameter cue is a design constant, never flagged even if it
    doesn't match a real metric value."""
    values: list[float] = []
    for match in _NUMBER_RE.finditer(text):
        if _HYPERPARAM_CUE_RE.search(text[max(0, match.start() - 30) : match.start()]):
            continue
        raw = match.group(0).removesuffix("%")
        try:
            value = float(raw)
        except ValueError:
            continue
        if not any(abs(value - k) < _NUMBER_TOLERANCE for k in known):
            values.append(value)
    return values


def lint_section(text: str, spec: SectionSpec, result: ExperimentResult) -> list[str]:
    """Returns human-readable violations, empty if the section passes.
    Never raises — a lint failure is something for the bounded repair
    completion (docs/paper_writing_design.md §8's sibling mechanism for the
    compile loop, applied here to prose) to fix, not a reason to crash."""
    violations: list[str] = []

    word_count = len(text.split())
    if word_count < spec.min_words:
        violations.append(f"too short: {word_count} words, expected at least {spec.min_words}")
    elif word_count > spec.max_words:
        violations.append(f"too long: {word_count} words, expected at most {spec.max_words}")

    known = known_numbers(result)
    unmatched = sorted(set(_extract_unmatched_numbers(text, known)))
    if unmatched:
        shown = ", ".join(str(n) for n in unmatched[:10])
        violations.append(f"number(s) not found in result.variants: {shown}")

    found_phrases = sorted({m.group(0).lower() for m in _AI_TELLS_RE.finditer(text)})
    if found_phrases:
        violations.append(f"banned phrase(s): {', '.join(found_phrases)}")

    if not spec.allow_citations and _CITE_RE.search(text):
        violations.append(f"section {spec.id!r} must not contain citations")

    violations.extend(check_structure(spec.id, text))

    return violations


def cited_keys(tex_text: str) -> set[str]:
    cited: set[str] = set()
    for match in _CITE_RE.finditer(tex_text):
        for key in match.group(1).split(","):
            cleaned = key.strip()
            if cleaned:
                cited.add(cleaned)
    return cited


def check_citations(tex_text: str, known_keys: set[str]) -> list[str]:
    """Hard-fail gate (docs/paper_writing_design.md §6): every ``\\cite``
    key emitted must exist in the ``refs.bib`` this module built. Returns
    the hallucinated keys, empty if none — a fabricated citation is a
    deterministic content bug, not noise, so unlike the soft numeric
    warning in ``reporting``, any hit here should block the module
    (see agent.py)."""
    return sorted(cited_keys(tex_text) - known_keys)


def check_zero_citation_sections(
    drafts: dict[str, str], allow_citation_ids: list[str], known_keys: set[str]
) -> list[str]:
    """Flags a section whose spec allows citations but that cites nothing
    at all, when real citable sources actually exist. Ported concept (not
    code) from spark-to-paper-skills' citations_lint.py
    `section_zero_citations` check. Soft here, not a hard fail like
    `check_citations` above: their target bibliography is 40-50 sources,
    ours is however many `topic_survey` happened to find for this run —
    a genuinely source-thin run can legitimately have little to cite in
    some sections, so this is surfaced as a note for a human to judge, not
    a block. Returns `[]` immediately if there's nothing to cite at all
    (`known_keys` empty) — that's a bibliography gap, not a per-section
    coverage gap, and already visible from an empty refs.bib."""
    if not known_keys:
        return []
    return [
        section_id
        for section_id in allow_citation_ids
        if drafts.get(section_id) and not _CITE_RE.search(drafts[section_id])
    ]


_HEADING_TEXT_RE = re.compile(r"\\subsection\*?\{([^}]*)\}")


def extract_subsection_headings(tex_text: str) -> list[str]:
    """Every ``\\subsection{...}`` title in a section body, in document
    order, whitespace-collapsed. This is the single source of truth for
    method-figure node labels (ts-figure/scripts/extract_headings.py) —
    the figure is derived from what ts-write actually committed to, never
    authored ahead of the prose, so there is no separate vocabulary for
    the two to drift apart from."""
    return [" ".join(m.group(1).split()) for m in _HEADING_TEXT_RE.finditer(tex_text)]


def check_method_figure_headings(node_labels: list[str], headings: list[str]) -> list[str]:
    """Hard structural check (not a fuzzy soft note): every figure node
    label must be one of method.tex's real subsection headings, exact
    match after whitespace collapse. Unlike a free-text terminology
    instruction, this can be exact because the labels are supposed to be
    *copied* from the headings, not independently reworded — a mismatch
    here means ts-figure ignored its own instructions, not that the model
    paraphrased legitimately."""
    heading_set = {" ".join(h.split()) for h in headings}
    return [
        label
        for label in node_labels
        if " ".join(label.split()) not in heading_set
    ]


_METHOD_FIGURE_INCLUDE_RE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{[^}]*method_figure[^}]*\}")


def check_method_figure_included(method_text: str) -> bool:
    """True if method.tex already references the spliced-in method
    figure. Used by agent.py's post-session verification to require one
    when a MethodFigureSpec was actually authored for this run."""
    return bool(_METHOD_FIGURE_INCLUDE_RE.search(method_text))


# Style/geometry constants a figure-drawing script legitimately hardcodes
# (figure size in inches/mm, dpi, font sizes, line widths, axis padding,
# alpha/opacity, hex color channel values via int() elsewhere) — exempted
# from the fabrication scan below the same way _HYPERPARAM_CUE_RE exempts
# design constants from the prose scan. Deliberately narrow: anything not
# matching one of these cues is treated as a claimed data value and must
# trace back to a real metric.
_PLOT_CONSTANT_KW_RE = re.compile(
    r"\b(?:figsize|dpi|fontsize|font_size|linewidth|lw|alpha|pad|width|height|"
    r"markersize|capsize|rotation|zorder|bbox|rounding_size|mutation_scale|"
    r"elinewidth|hspace|wspace)\b\s*[:=]\s*"
    # value span: a parenthesized/bracketed tuple (one level deep — plot
    # kwargs never nest further) or a single bare number.
    r"(?:\([^)]*\)|\[[^\]]*\]|-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_CODE_NUMBER_RE = re.compile(r"-?\d+\.\d+|-?\d+")


def scan_script_for_fabrication(source: str, known: set[float]) -> list[float]:
    """Same principle as ``_extract_unmatched_numbers``, retargeted at a
    figure-drawing script's source instead of prose: any numeric literal
    that isn't a known real metric value (``known_numbers(result)``) and
    doesn't fall inside a plotting/style keyword-argument's value span
    (``figsize=(7.0, 3.0)``, ``dpi=300``, ...) is treated as a
    possibly-fabricated data value. Exemption is scoped to the matched
    keyword-argument span, not the whole line — a real data line like
    ``ax.bar(x, values, linewidth=1.2)`` still has any hardcoded literal in
    ``values`` checked; only the ``linewidth=1.2`` span is exempt. Regex
    span matching, not AST-based — a deliberately simple static check, not
    a full data-flow analysis; it catches hardcoded results, not every
    conceivable way to launder one (e.g. a value computed from an
    unrelated hardcoded constant a few lines earlier would still slip
    through). Meant to be paired with an independent re-run of the
    script, never used alone."""
    values: list[float] = []
    for line in source.splitlines():
        code = line.split("#", 1)[0]
        if not code.strip():
            continue
        exempt_spans = [m.span() for m in _PLOT_CONSTANT_KW_RE.finditer(code)]
        for match in _CODE_NUMBER_RE.finditer(code):
            if any(start <= match.start() < end for start, end in exempt_spans):
                continue
            try:
                value = float(match.group(0))
            except ValueError:
                continue
            if any(abs(value - k) < _NUMBER_TOLERANCE for k in known):
                continue
            # Small integers are near-universally indices, loop bounds, or
            # percent-conversion factors in plotting code, not results —
            # the prose scanner doesn't need this exemption (results.json
            # values are never bare small integers in prose), but
            # plotting code is full of them legitimately. Deliberately
            # does NOT exempt fractions in [0, 1) generally: most of this
            # project's own metrics (accuracy, rejection rate, ...) live
            # exactly in that range, so a blanket exemption there would
            # hide the single most likely fabricated value instead of
            # catching it. A genuine alpha=/opacity constant is already
            # covered by the keyword-span exemption above, not this one.
            if value in (0, 1, 2, 100):
                continue
            values.append(value)
    return values


def check_completeness(tex_text: str, result: ExperimentResult, metrics: list[str]) -> list[str]:
    """Variant/metric name-mention check, same idea as
    ``reporting._verify_and_annotate``'s completeness check, adapted for
    LaTeX escaping (docs/paper_writing_design.md §5) — compares against
    both the escaped and raw form, since either could legitimately appear."""
    missing: list[str] = []
    for variant in result.variants:
        if escape_latex(variant.name) not in tex_text and variant.name not in tex_text:
            missing.append(variant.name)
    for metric in metrics:
        if escape_latex(metric) not in tex_text and metric not in tex_text:
            missing.append(metric)
    return missing
