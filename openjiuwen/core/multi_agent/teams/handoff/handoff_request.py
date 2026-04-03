# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""HandoffRequest -- internal drive message published between container agents."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, List, Optional

if TYPE_CHECKING:
    from openjiuwen.core.session.agent_team import Session


@dataclass
class HandoffRequest:
    """Drive message published to ``container_{agent_id}`` topics by :class:`~HandoffTeam`.

    Attributes:
        input_message: User or intermediate input for the next agent hop.
        history:       Accumulated handoff history across hops (list of dicts).
        session:       Team session for stream I/O.  ``None`` in unit-test scenarios.
    """
    input_message: Any
    history: List[dict] = field(default_factory=list)
    session: Optional["Session"] = None

    @property
    def session_id(self) -> str:
        """Session ID derived from the attached session; empty string when no session is attached."""
        return self.session.get_session_id() if self.session is not None else ""
