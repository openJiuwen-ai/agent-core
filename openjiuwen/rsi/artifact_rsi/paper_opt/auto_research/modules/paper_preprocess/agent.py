"""High-level in-memory preprocessing for paper-improvement tasks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .extractor import build_initial_prompt, extract_paper_evidence
from .latex_validation import validate_latex_paper
from .schemas import LatexPaperDocument, PaperPreprocessInput, PaperPreprocessOutput


class PaperPreprocessAgent:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

    def run(
        self, inputs: PaperPreprocessInput, *, document: LatexPaperDocument | None = None
    ) -> PaperPreprocessOutput:
        document = document or validate_latex_paper(inputs.paper_dir)
        evidence = extract_paper_evidence(document)
        return PaperPreprocessOutput(
            document=document, evidence=evidence, initial_prompt=build_initial_prompt(document, evidence)
        )


def preprocess_paper(paper_dir: str | Path = "experiments/mgr-agent-1/paper") -> str:
    return PaperPreprocessAgent().run(PaperPreprocessInput(paper_dir=str(paper_dir))).initial_prompt
