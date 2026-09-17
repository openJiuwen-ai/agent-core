# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Receive Codex's native logical model-call spans over OTLP/HTTP.

The Codex App Server emits many internal spans.  Forwarding its trace exporter
directly to the application's OTLP backend makes transport details such as
``auth`` and ``send_data`` appear as top-level observations.  This loopback
receiver decodes the native trace stream and keeps only
``run_sampling_request``: Codex's own span for one logical sampling request.

The selected span already contains its real start/end timestamps.  Jiuwen does
not pair it with SDK response notifications or infer its boundary from tool
events.

``CodexOtelTraceReceiver`` is a per-member handle onto the process-wide
:class:`~openjiuwen.agent_teams.observability.shared_otlp.SharedOtlpReceiver`:
one loopback socket serves every member, and each handle filters the decoded
stream down to its member-relevant logical model spans.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from openjiuwen.agent_teams.observability.shared_otlp import get_shared_otlp_receiver
from openjiuwen.core.common.logging import team_logger

_LOGICAL_MODEL_SPAN = "run_sampling_request"


def _is_logical_model_span(name: str) -> bool:
    """Match the native span while tolerating a module-qualified name."""
    normalized = name.replace("::", ".")
    return normalized == _LOGICAL_MODEL_SPAN or normalized.endswith(
        f".{_LOGICAL_MODEL_SPAN}",
    )


class CodexOtelTraceReceiver:
    """Per-member handle onto the shared loopback OTLP/HTTP trace receiver.

    Subscribes to the process-wide receiver so every Codex member shares one
    listening socket; ``endpoint`` points at that shared receiver. ``aclose``
    stops forwarding to this member's callback but leaves the shared receiver
    (and its single port) serving other members.
    """

    def __init__(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self._shared = get_shared_otlp_receiver()
        self._subscriber_id: int | None = None
        self.endpoint: str | None = None

    @classmethod
    async def start(
        cls,
        callback: Callable[[dict[str, Any]], None],
    ) -> CodexOtelTraceReceiver | None:
        """Attach to the shared receiver; None when OTLP support is missing."""
        receiver = cls(callback)

        def filtered(event: dict[str, Any]) -> None:
            if _is_logical_model_span(str(event.get("name") or "")):
                callback(event)

        subscriber_id = await receiver._shared.subscribe(filtered)
        if subscriber_id is None:
            return None
        receiver._subscriber_id = subscriber_id
        receiver.endpoint = receiver._shared.endpoint
        team_logger.info(
            "otel: Codex native model-span receiver attached endpoint={}",
            receiver.endpoint,
        )
        return receiver

    async def aclose(self) -> None:
        """Detach this member's callback; the shared receiver keeps serving."""
        if self._subscriber_id is not None:
            self._shared.unsubscribe(self._subscriber_id)
            self._subscriber_id = None
        self.endpoint = None


__all__ = ["CodexOtelTraceReceiver"]
