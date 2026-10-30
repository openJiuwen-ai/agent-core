"""What a scoring call can be given besides the Task and the delivered work."""

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable


def quiet(message):
    """The default log: says nothing."""


@dataclass
class RewardOptions:
    """The optional inputs of reward(), text_reward() and score_workspace(); every field may be left out.

    txt_response: the agent's final text reply (scored when the answer is in it).
    user_note: the user's remarks on the work.
    process_signal: environment errors met during the run and the user's feedback across turns. Both become
        checks in the rubric, each in the criterion it concerns.
    rubric_path: a file that keeps the rubric (with those checks added so far), so later rounds are scored on the
        same criteria; without it every call writes new criteria. To start over with a kept file, move it aside
        first (archive_rubric).
    previous_result: an earlier result JSON of the same Task, used only when new criteria are written: they keep
        its floor claims and raise the challenge claims above what the work already met.
    criteria: criteria given by the caller, used as they are (batch runs); None writes them from the Task.
    log: called with a line of text for every step the run takes.
    warnings: notes to put into the result's warnings (the call adds its own to a copy, not to this list).
    """

    txt_response: str = ""
    user_note: str = ""
    process_signal: str = ""
    rubric_path: str | Path | None = None
    previous_result: dict[str, Any] | None = None
    criteria: list[dict[str, Any]] | None = None
    log: Callable[[str], None] = quiet
    warnings: list[str] = field(default_factory=list)

    def for_run(self):
        """A private copy for one run: `log` is always callable and `warnings` is a list the stages may append to."""
        return replace(self, log=self.log or quiet, warnings=list(self.warnings or ()))
