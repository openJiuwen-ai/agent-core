"""Paper-improvement input preparation, including standalone LaTeX validation."""

from .agent import PaperPreprocessAgent, preprocess_paper
from .latex_validation import validate_latex_paper
from .schemas import (
    LatexPaperDocument,
    LatexValidationError,
    PaperPreprocessError,
    PaperPreprocessInput,
    PaperPreprocessOutput,
)

__all__ = [
    "LatexPaperDocument",
    "LatexValidationError",
    "PaperPreprocessAgent",
    "PaperPreprocessError",
    "PaperPreprocessInput",
    "PaperPreprocessOutput",
    "preprocess_paper",
    "validate_latex_paper",
]
