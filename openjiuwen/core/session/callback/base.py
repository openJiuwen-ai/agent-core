# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import abstractmethod


def trigger_event(func):
    func.is_trigger_event = True
    return func


class BaseHandler:
    """Stateless data processing"""

    def __init_subclass__(cls):
        super().__init_subclass__()
        cls.trigger_event = trigger_event

    def __init__(self, owner):
        self.owner = owner

    @abstractmethod
    def event_name(self):
        pass

    def get_trigger_events(self):
        trigger_events = []
        for name in dir(self):
            attr = getattr(self, name)
            if callable(attr) and getattr(attr, "is_trigger_event", False):
                trigger_events.append(name)
        return trigger_events