# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Subagent transcript payloads and parent-session stream emission."""

from __future__ import annotations

from typing import Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.stream.base import OutputSchema
from openjiuwen.harness.subagent_runtime.config import SubagentRuntimeConfig
from openjiuwen.harness.subagent_runtime.models import SubagentMessage

SUBAGENT_MESSAGE_EVENT_TYPE = "subagent_message"
_MAX_CONSECUTIVE_FAILURES = 3


def build_message_payload(message: SubagentMessage) -> dict[str, Any]:
    """Build the external subagent transcript payload."""
    return message.to_dict()


async def emit_subagent_message(
    session: Session,
    *,
    projection: dict[str, Any],
) -> None:
    """Write one subagent transcript update to the parent session stream."""
    await session.write_stream(
        OutputSchema(
            type=SUBAGENT_MESSAGE_EVENT_TYPE,
            index=0,
            payload={"subagent_message": projection},
        )
    )


class TranscriptEmitter:
    """Emit full-fidelity subagent transcript events without dropping."""

    def __init__(
        self,
        session: Session,
        *,
        config: SubagentRuntimeConfig,
    ) -> None:
        self._session = session
        self._config = config
        self._disabled = False
        self._consecutive_failures = 0

    @property
    def disabled(self) -> bool:
        return self._disabled

    async def emit(self, message: SubagentMessage) -> None:
        if self._disabled or not self._config.enable_transcript_stream:
            return
        try:
            await emit_subagent_message(
                self._session,
                projection=build_message_payload(message),
            )
            self._consecutive_failures = 0
        except Exception as exc:
            self._consecutive_failures += 1
            if self._consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                self._disabled = True
                logger.warning(
                    "[subagent_message] emitter disabled after %s failures: %s",
                    self._consecutive_failures,
                    exc,
                )


__all__ = [
    "SUBAGENT_MESSAGE_EVENT_TYPE",
    "TranscriptEmitter",
    "build_message_payload",
    "emit_subagent_message",
]
