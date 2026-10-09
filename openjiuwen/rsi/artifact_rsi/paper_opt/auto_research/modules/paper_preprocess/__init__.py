"""Paper-improvement input preparation, including standalone LaTeX validation."""

from .agent import PaperPreprocessAgent, preprocess_paper
from .latex_validation import validate_latex_paper
from .schemas import (
    LatexPaperDocument,
    LatexValidationError,
    PaperPreprocessError,
    PaperPreprocessInput,
    PaperPreprocessOutput,
    ResearchClaim,
    ResearchContext,
)

__all__ = [
    "LatexPaperDocument",
    "LatexValidationError",
    "PaperPreprocessAgent",
    "PaperPreprocessError",
    "PaperPreprocessInput",
    "PaperPreprocessOutput",
    "ResearchClaim",
    "ResearchContext",
    "preprocess_paper",
    "validate_latex_paper",
]
