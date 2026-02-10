# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Callable, Dict, List

from openjiuwen.core.common.logging import session_logger, LogEventType
from openjiuwen.core.session.callback.base import BaseHandler


class CallbackManager:
    def __init__(self):
        self._handlers: Dict[str, BaseHandler] = {}
        self._trigger_events: Dict[str, List] = {}

    def _instantiation_handler(self, handler_class_name: Callable):
        handler = handler_class_name(owner=self)
        if not isinstance(handler, handler_class_name):
            raise TypeError("handler class name cannot be instantiation")
        return handler

    def _init_handler(self, handler_map: dict):
        for handler_name, handler in handler_map.items():
            self._handlers[handler_name] = handler
            trigger_events = handler.get_trigger_events()
            self._trigger_events[handler_name] = trigger_events

    async def trigger(self, handler_class_name: str, event_name: str, **kwargs):
        if handler_class_name not in self._trigger_events or event_name not in self._trigger_events[
            handler_class_name
        ]:
            session_logger.error(
                "Event name not registered in callback manager",
                event_type=LogEventType.SYSTEM_ERROR,
                metadata={"handler_class_name": handler_class_name, "event_name": event_name}
            )
            raise TypeError(f"event name not exists")
        handler = self._handlers[handler_class_name]
        if hasattr(handler, event_name):
            method = getattr(handler, event_name)
            await method(**kwargs)

    def register(self, configs: dict):
        self._init_handler(configs)
