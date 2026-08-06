from dataclasses import dataclass
from enum import Enum


class Scheme(str, Enum):
    SELF = "self"
    DEBATE = "debate"
    JUDGE = "judge"
    TEACHER = "teacher"
    REVIEWER = "reviewer"


@dataclass
class Collaboration:
    initiator: str
    target: str
    scheme: Scheme


def select_collaborations() -> list[Collaboration]:
    """Example adaptive collaboration decisions for one session."""
    return [
        Collaboration("agent_1", "agent_2", Scheme.DEBATE),
        Collaboration("agent_2", "agent_3", Scheme.REVIEWER),
        Collaboration("agent_3", "agent_3", Scheme.SELF),
    ]