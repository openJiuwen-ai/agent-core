# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.


class AgentGroup:
    def __init__(self):
        self._subscription = None
        self._message_handler = None
        self._topic: str = None

    def set_subscription(self, subscription):
        self._subscription = subscription
        self._subscription.set_message_handler(message_handler=self._message_handler)
        self._subscription.activate()

    def get_topic(self):
        pass

    def get_subscription(self):
        return self._subscription