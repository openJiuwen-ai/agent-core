"""Source-grounded, deterministic extraction of paper evidence for improvement tasks."""

from __future__ import annotations

import re
from pathlib import Path

from .schemas import (
    LatexPaperDocument,
    PaperEvidence,
    ResearchClaim,
    ResearchContext,
    ResultClaim,
)

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_RESULT_SIGNAL_RE = re.compile(
    r"(?i)\b(?:accuracy|correctness|score|rate|variance|confidence|improv(?:e|es|ed|ement)|"
    r"gain|drop|increase|decrease|result|significant|threshold|failure|error)\b|\b\d+(?:\.\d+)?\s*(?:pp|%)"
)
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?%?")
_BIB_KEY_RE = re.compile(r"@\w+\s*\{\s*([^,\s]+)")
_BIB_TITLE_RE = re.compile(r"title\s*=\s*\{([^{}]*)\}", re.IGNORECASE)
_METRIC_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("answer_f1", ("answer f1", "answer-f1", "answer_f1")),
    ("answer_em", ("answer em", "answer-em", "answer_em", "exact match")),
    ("sp_f1", ("supporting-fact f1", "supporting fact f1", "sp_f1", "sp f1")),
    ("joint_f1", ("joint f1", "joint_f1")),
    ("accuracy", ("accuracy",)),
)
# Full section bodies go into ResearchContext for reporting. The manager's
# initial_prompt still uses the short sentence summaries below. Cap keeps a
# long Method from dominating the reporting task message.
_CONTEXT_TEXT_LIMIT = 12000


def _short(text: str, limit: int = 900) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _by_heading(document: LatexPaperDocument, *keywords: str) -> list[tuple[str, str]]:
    return [
        (item.title, item.content)
        for item in document.sections
        if any(key in item.title.lower() for key in keywords)
    ]


def _joined_section_text(document: LatexPaperDocument, *keywords: str) -> str:
    text = "\n\n".join(
        content.strip() for _, content in _by_heading(document, *keywords) if content.strip()
    )
    if len(text) <= _CONTEXT_TEXT_LIMIT:
        return text
    return text[: _CONTEXT_TEXT_LIMIT - 3].rstrip() + "..."


def _sentences(
    blocks: list[tuple[str, str]], limit: int, *, numeric_only: bool = False, result_only: bool = False
) -> list[tuple[str, str]]:
    output: list[tuple[str, str]] = []
    for heading, text in blocks:
        for sentence in _SENTENCE_RE.split(text):
            sentence = _short(sentence, 700)
            if len(sentence) < 35 or (numeric_only and not re.search(r"\d", sentence)):
                continue
            if result_only and not _RESULT_SIGNAL_RE.search(sentence):
                continue
            item = (heading, sentence)
            if item not in output:
                output.append(item)
            if len(output) >= limit:
                return output
    return output


def extract_paper_evidence(document: LatexPaperDocument) -> PaperEvidence:
    introduction = _by_heading(document, "introduction", "background")
    methods = _by_heading(document, "method", "approach", "model")
    experiments = _by_heading(document, "experiment", "result", "evaluation")
    results = _by_heading(document, "results")
    conclusions = _by_heading(document, "conclusion")
    limitations = _by_heading(document, "limitation", "discussion")

    question = _sentences(introduction, 1) or [("abstract", document.abstract)]
    method = _sentences(methods, 1) or [("abstract", document.abstract)]
    setup = _sentences(experiments, 4)
    result_sentences = _sentences(results or experiments, 6, numeric_only=True, result_only=True)
    conclusion_sentences = _sentences(conclusions, 4) or _sentences(limitations, 4)
    limitation_sentences = _sentences(limitations, 4)
    return PaperEvidence(
        research_question=question[0][1],
        method_summary=method[0][1],
        experiment_setup=[item[1] for item in setup],
        key_results=[
            ResultClaim(claim=text, evidence=text, source_section=heading) for heading, text in result_sentences
        ],
        conclusions=[item[1] for item in conclusion_sentences],
        limitations=[item[1] for item in limitation_sentences],
        improvement_opportunities=[item[1] for item in limitation_sentences[:3]]
        or ["Identify one measurable limitation or unanswered question in the baseline evidence."],
    )


def build_initial_prompt(document: LatexPaperDocument, evidence: PaperEvidence) -> str:
    lines = [
        "TASK MODE: modify_paper",
        "",
        "BASELINE PAPER",
        f"Title: {document.title}",
        f"Main LaTeX file: {document.main_tex_path}",
        "",
        "RESEARCH QUESTION",
        evidence.research_question,
        "",
        "EXISTING METHOD",
        evidence.method_summary,
        "",
        "EXPERIMENTAL SETUP",
    ]
    lines.extend(f"- {item}" for item in evidence.experiment_setup or ["Not explicitly extracted."])
    lines.extend(["", "REPORTED EXPERIMENTAL RESULTS"])
    if evidence.key_results:
        for result in evidence.key_results:
            lines.extend([f"- {result.claim}", f"  Evidence ({result.source_section}): {result.evidence}"])
    else:
        lines.append("- No bounded quantitative result was extracted; inspect the baseline paper directly.")
    lines.extend(["", "REPORTED CONCLUSIONS"])
    lines.extend(f"- {item}" for item in evidence.conclusions or ["Not explicitly extracted."])
    lines.extend(["", "STATED LIMITATIONS / OPEN PROBLEMS"])
    lines.extend(f"- {item}" for item in evidence.limitations or ["Not explicitly extracted."])
    lines.extend(
        [
            "",
            "IMPROVEMENT TASK",
            "Design, implement, and evaluate one concrete improvement to this baseline paper. Preserve the original "
            "paper as the baseline; do not present its reported results as new measurements. Every new claim must be "
            "supported by newly measured evidence and compared explicitly with the baseline.",
            "",
            "PROMISING STARTING POINTS",
        ]
    )
    lines.extend(f"- {item}" for item in evidence.improvement_opportunities)
    return "\n".join(lines).strip()


def _extract_numbers(text: str) -> list[float]:
    values: list[float] = []
    seen: set[float] = set()
    for match in _NUMBER_RE.finditer(text):
        raw = match.group(0).removesuffix("%")
        try:
            value = float(raw)
        except ValueError:
            continue
        if value.is_integer() and 1900 <= value <= 2099:
            continue
        if value in seen:
            continue
        seen.add(value)
        values.append(value)
    return values


def _guess_metric(text: str) -> str | None:
    lowered = text.lower().replace("\\", "")
    for metric, needles in _METRIC_ALIASES:
        if any(needle in lowered for needle in needles):
            return metric
    return None


def _claim_from_text(claim_id: str, text: str, source_section: str, evidence: str) -> ResearchClaim:
    numbers = _extract_numbers(text)
    return ResearchClaim(
        claim_id=claim_id,
        text=text,
        kind="quantitative" if numbers else "qualitative",
        source_section=source_section,
        evidence=evidence,
        metric=_guess_metric(text) if numbers else None,
        value=numbers[0] if numbers else None,
        values=numbers,
        provenance={"section": source_section},
    )


def _load_bibliography(document: LatexPaperDocument) -> tuple[str, list[str], dict[str, str]]:
    texts: list[str] = []
    keys: list[str] = []
    title_to_key: dict[str, str] = {}
    seen_keys: set[str] = set()
    for raw in document.bibliography_paths:
        path = Path(raw)
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        texts.append(text)
        for match in _BIB_KEY_RE.finditer(text):
            key = match.group(1)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            keys.append(key)
            start = match.start()
            end = start + 800
            title_match = _BIB_TITLE_RE.search(text[start:end])
            if title_match:
                title_to_key.setdefault(title_match.group(1).strip(), key)
    return "\n\n".join(texts), keys, title_to_key


def paper_evidence_to_research_context(
    document: LatexPaperDocument, evidence: PaperEvidence
) -> ResearchContext:
    """Deterministically lift validated paper evidence into typed prior-paper context."""
    claims: list[ResearchClaim] = []
    for index, result in enumerate(evidence.key_results):
        claims.append(
            _claim_from_text(
                f"prior:{result.source_section}:{index}",
                result.claim,
                result.source_section,
                result.evidence,
            )
        )
    for index, text in enumerate(evidence.conclusions):
        claims.append(_claim_from_text(f"prior:conclusion:{index}", text, "conclusion", text))
    bibliography_text, citation_keys, title_to_key = _load_bibliography(document)
    method = _joined_section_text(document, "method", "approach", "model") or evidence.method_summary
    setup_text = _joined_section_text(document, "experiment", "evaluation")
    experiment_setup = [setup_text] if setup_text else list(evidence.experiment_setup)
    extracted: list[float] = []
    seen: set[float] = set()
    for text in (
        evidence.research_question,
        method,
        *experiment_setup,
        *evidence.conclusions,
        *evidence.limitations,
        *[claim.text for claim in claims],
    ):
        for value in _extract_numbers(text):
            if value not in seen:
                seen.add(value)
                extracted.append(value)
    for claim in claims:
        for value in claim.values:
            if value not in seen:
                seen.add(value)
                extracted.append(value)
    return ResearchContext(
        title=document.title,
        abstract=document.abstract,
        problem=evidence.research_question,
        method=method,
        experiment_setup=experiment_setup,
        claims=claims,
        conclusions=list(evidence.conclusions),
        limitations=list(evidence.limitations),
        improvement_opportunities=list(evidence.improvement_opportunities),
        bibliography_text=bibliography_text,
        citation_keys=citation_keys,
        title_to_key=title_to_key,
        extracted_numbers=extracted,
    )
