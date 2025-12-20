#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio

from jiuwen.core.runtime.runtime import BaseRuntime
from jiuwen.core.graph.vertex import Vertex


class StreamActor:
    def __init__(self):
        self.loop = asyncio.get_event_loop()
        self._stream_nodes: dict[str, Vertex] = {}

    def init(self, context: BaseRuntime):
        for _, node in self._stream_nodes.items():
            node.init(context)

    def add_stream_consumer(self, consumer: Vertex, node_id: str):
        if node_id not in self._stream_nodes.keys():
            self._stream_nodes[node_id] = consumer

    async def run(self):
        streams = [node.stream_call() for node_id, node in self._stream_nodes.items()]
        return asyncio.gather(*streams)