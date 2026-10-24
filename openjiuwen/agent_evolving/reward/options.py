"""Named option groups for Reward calls, retaining existing positional and keyword argument order.

The entry points bind their trailing arguments through these dataclasses. Unknown names, duplicate values
and missing required arguments still raise TypeError, without silently dropping caller options.
"""

from dataclasses import dataclass
from typing import Any


def _ignore_log(message):
    """Default log callback for callers that do not request progress."""


@dataclass(frozen=True)
class OptionalCall:
    """Named trailing arguments for optional; field order preserves positional callers."""

    stage: Any
    notes: Any
    log: Any


@dataclass(frozen=True)
class TerminalJudge:
    """Named trailing arguments for evaluate_terminal; field order preserves positional callers."""

    judge: Any
    log: Any
    gathered: Any
    seeing: Any = True


@dataclass(frozen=True)
class CriteriaOptions:
    """Named trailing arguments for write_criteria; field order preserves positional callers."""

    log: Any = _ignore_log
    knowledge: Any = ""
    producer: Any = None
    previous_result: Any = None


@dataclass(frozen=True)
class FeedbackOptions:
    """Named trailing arguments for feedback_claims; field order preserves positional callers."""

    criteria: Any
    config: Any
    log: Any = _ignore_log
    producer: Any = None
    notes: Any = None


@dataclass(frozen=True)
class SummaryOptions:
    """Named trailing arguments for overall_summary; field order preserves positional callers."""

    judge: Any
    log: Any
    notes: Any


@dataclass(frozen=True)
class RewardOptions:
    """Named trailing arguments for reward; field order preserves positional callers."""

    txt_response: Any = ""
    user_note: Any = ""
    process_signal: Any = ""
    rubric_path: Any = None
    previous_result: Any = None
    log: Any = _ignore_log
    warnings: Any = ()


@dataclass(frozen=True)
class WorkspaceOptions:
    """Named trailing arguments for score_workspace; field order preserves positional callers."""

    txt_response: Any = ""
    criteria: Any = None
    log: Any = _ignore_log
    warnings: Any = ()
    rubric_path: Any = None
    user_note: Any = ""
    process_signal: Any = ""
    previous_result: Any = None


@dataclass(frozen=True)
class TextRewardOptions:
    """Named trailing arguments for text_reward; field order preserves positional callers."""

    response: Any = ""
    references: Any = None
    criteria: Any = None
    log: Any = _ignore_log


@dataclass(frozen=True)
class EvidenceOptions:
    """Named trailing arguments for gather; field order preserves positional callers."""

    log: Any
    rounds: Any = 12
    seconds: Any = 300
