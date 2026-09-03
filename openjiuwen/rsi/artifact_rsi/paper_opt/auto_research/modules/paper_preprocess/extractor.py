"""Source-grounded, deterministic extraction of paper evidence for improvement tasks."""

from __future__ import annotations

import re

from .schemas import LatexPaperDocument, PaperEvidence, ResultClaim

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_RESULT_SIGNAL_RE = re.compile(
    r"(?i)\b(?:accuracy|correctness|score|rate|variance|confidence|improv(?:e|es|ed|ement)|"
    r"gain|drop|increase|decrease|result|significant|threshold|failure|error)\b|\b\d+(?:\.\d+)?\s*(?:pp|%)"
)


def _short(text: str, limit: int = 900) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _by_heading(document: LatexPaperDocument, *keywords: str) -> list[tuple[str, str]]:
    return [(item.title, item.content) for item in document.sections if any(key in item.title.lower() for key in keywords)]


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
        research_question=question[0][1], method_summary=method[0][1],
        experiment_setup=[item[1] for item in setup],
        key_results=[ResultClaim(claim=text, evidence=text, source_section=heading) for heading, text in result_sentences],
        conclusions=[item[1] for item in conclusion_sentences],
        limitations=[item[1] for item in limitation_sentences],
        improvement_opportunities=[item[1] for item in limitation_sentences[:3]]
        or ["Identify one measurable limitation or unanswered question in the baseline evidence."],
    )


def build_initial_prompt(document: LatexPaperDocument, evidence: PaperEvidence) -> str:
    lines = [
        "TASK MODE: modify_paper", "", "BASELINE PAPER", f"Title: {document.title}",
        f"Main LaTeX file: {document.main_tex_path}", "", "RESEARCH QUESTION", evidence.research_question,
        "", "EXISTING METHOD", evidence.method_summary, "", "EXPERIMENTAL SETUP",
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
    lines.extend([
        "", "IMPROVEMENT TASK",
        "Design, implement, and evaluate one concrete improvement to this baseline paper. Preserve the original paper "
        "as the baseline; do not present its reported results as new measurements. Every new claim must be supported "
        "by newly measured evidence and compared explicitly with the baseline.", "", "PROMISING STARTING POINTS",
    ])
    lines.extend(f"- {item}" for item in evidence.improvement_opportunities)
    return "\n".join(lines).strip()
