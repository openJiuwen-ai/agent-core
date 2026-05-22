# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Any

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext

from openjiuwen.harness.tools.mobile_gui.state import mobile_gui_shared


def ensure_mobile_gui_session_bridge(ctx: AgentCallbackContext | None) -> None:
    """Mirror outer DeepAgent invoke state into inner ReAct ctx.extra.

    DeepAgent runs :class:`DeviceLifecycleRail` and this rail's ``before_invoke`` on the
    **outer** agent only. The inner :class:`ReActAgent` uses a separate callback context
    for ``before_model_call`` and tools — copy ``device_handle`` and ``pinned_user_goal``
    from ``mobile_gui_shared`` (populated on the outer side) so perception, anchors, and
    tools see a consistent session.
    """
    if ctx is None:
        return
    if ctx.extra.get("device_handle") is None and mobile_gui_shared.get("device_handle") is not None:
        ctx.extra["device_handle"] = mobile_gui_shared["device_handle"]
    if ctx.extra.get("pinned_user_goal") is None and mobile_gui_shared.get("pinned_user_goal") is not None:
        ctx.extra["pinned_user_goal"] = mobile_gui_shared["pinned_user_goal"]


# Back-compat name
ensure_device_handle_on_ctx = ensure_mobile_gui_session_bridge


def get_shared_extra(ctx: AgentCallbackContext | None) -> dict:
    ensure_mobile_gui_session_bridge(ctx)
    if ctx is not None:
        return ctx.extra
    return mobile_gui_shared


def get_device_handle(extra: dict) -> tuple[Any | None, str | None]:
    device = extra.get("device_handle")
    if device is None:
        return None, (
            "Error: DeviceNotReady: device_handle not found in ctx.extra. "
            "Ensure DeviceLifecycleRail ran BEFORE_INVOKE."
        )
    return device, None
