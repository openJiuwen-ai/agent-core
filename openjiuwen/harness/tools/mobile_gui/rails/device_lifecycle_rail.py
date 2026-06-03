# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentRail,
    InvokeInputs,
)
from openjiuwen.harness.tools.mobile_gui.config import MobileGuiRuntimeSettings
from openjiuwen.harness.tools.mobile_gui.state import mobile_gui_shared


class DeviceLifecycleRail(AgentRail):
    """Inject Android device handle at invoke boundaries (uiautomator2)."""

    priority: int = 95

    def __init__(
        self,
        settings: MobileGuiRuntimeSettings,
    ) -> None:
        super().__init__()
        self._settings = settings
        self._cleanup_go_home = settings.cleanup_go_home
        self._health_check = settings.health_check

        if settings.device is not None:
            self._device = settings.device
            logger.info("[DeviceLifecycleRail] using externally supplied device")
        else:
            serial = settings.device_serial
            self._device = self._connect_device(serial)

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        if not isinstance(ctx.inputs, InvokeInputs):
            return

        if self._health_check:
            self._verify_device_health()

        ctx.extra["device_handle"] = self._device
        mobile_gui_shared["device_handle"] = self._device

        query = ctx.inputs.query
        q_preview = str(query)[:80] + "..." if len(str(query)) > 80 else str(query)
        logger.info(
            "[DeviceLifecycleRail] before_invoke: device injected, query=%s",
            q_preview,
        )

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        if self._cleanup_go_home:
            self._safe_go_home()

        result_type = "unknown"
        if isinstance(ctx.inputs, InvokeInputs) and ctx.inputs.result:
            result_type = ctx.inputs.result.get("result_type", "unknown")
        logger.info(
            "[DeviceLifecycleRail] after_invoke: cleanup done, result_type=%s",
            result_type,
        )

    @staticmethod
    def _connect_device(serial: str) -> Any:
        import uiautomator2 as u2

        logger.info("[DeviceLifecycleRail] connecting to device: %s", serial)
        try:
            d = u2.connect(serial)
            info = d.info
            product = info.get("productName", "Unknown")
            logger.info(
                "[DeviceLifecycleRail] device connected: %s (%s)",
                product,
                serial,
            )
            return d
        except Exception as e:
            logger.error("[DeviceLifecycleRail] device connection failed: %s", e)
            raise ConnectionError(
                f"Cannot connect to device {serial}: {e}. "
                "Check USB debugging, adb devices, and DEVICE_SERIAL."
            ) from e

    def _verify_device_health(self) -> None:
        try:
            self._device.app_current()
        except Exception as e:
            logger.warning(
                "[DeviceLifecycleRail] device health check failed: %s (continuing)",
                e,
            )

    def _safe_go_home(self) -> None:
        try:
            self._device.press("home")
            logger.info("[DeviceLifecycleRail] returned to home screen")
        except Exception as e:
            logger.warning("[DeviceLifecycleRail] go-home failed: %s", e)
