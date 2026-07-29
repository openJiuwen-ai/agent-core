# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team-specific confirmation payload models.

``TeamConfirmPayload`` extends the base ``ConfirmPayload`` with a
``decided_by`` field that tracks who made the approval decision.
``TeamPermissionConfirmResponse`` extends ``PermissionConfirmResponse``
with the same ``decided_by`` field for the dataclass-based confirm path.

Both are used exclusively by ``TeamPermissionRail`` and belong to the
agent_teams subsystem.
"""

from dataclasses import dataclass
from typing import Optional

from openjiuwen.harness.rails.interrupt.confirm_rail import ConfirmPayload
from openjiuwen.harness.security.models import PermissionConfirmResponse


class TeamConfirmPayload(ConfirmPayload):
    """Team-mode confirmation payload with ``decided_by`` tracking.

    ``decided_by`` records who made the approval decision (e.g. ``"leader"``).
    It is not exposed to the LLM — it is set internally by
    ``TeamPermissionRail.parse_confirm_payload``.
    """

    decided_by: str | None = None


@dataclass(frozen=True)
class TeamPermissionConfirmResponse(PermissionConfirmResponse):
    """Team-mode confirmation response with ``decided_by`` tracking.

    ``decided_by`` records who made the approval decision (e.g. ``"leader"``).
    It is not exposed to the LLM — it is set internally by
    ``TeamPermissionRail.parse_confirm_payload``.
    """

    decided_by: Optional[str] = None


__all__ = [
    "TeamConfirmPayload",
    "TeamPermissionConfirmResponse",
]
