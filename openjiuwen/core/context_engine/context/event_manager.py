# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import List, Optional, Union

from openjiuwen.core.context_engine.processor.base import ContextEvent


class ContextEventManager:
    def __init__(self):
        self._events_list: List[ContextEvent] = []
        self._working_events_list: List[ContextEvent] = []

    def add_event(self, event: ContextEvent):
        if event:
            self._events_list.append(event)
            self._working_events_list.append(event)

    def get_working_events(self) -> List[ContextEvent]:
        return self._working_events_list

    def clear_working_events(self):
        self._working_events_list = []
