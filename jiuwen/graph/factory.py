#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from enum import Enum

from jiuwen.core.graph.base import Graph
from jiuwen.graph.pregel.graph import PregelGraph


class GraphType(Enum):
    PREGEL = 1


class GraphFactory:
    def __init__(self, graph_type: GraphType = GraphType.PREGEL):
        self._graph_type = graph_type

    def create_graph(self) -> Graph:
        if self._graph_type == GraphType.PREGEL:
            return PregelGraph()
        return PregelGraph()
