#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import AsyncIterator, Any

from jiuwen.core.component.base import WorkflowComponent
from jiuwen.core.context_engine.base import Context
from jiuwen.core.graph.executable import Input, Output
from jiuwen.core.runtime.base import ComponentExecutable
from jiuwen.core.runtime.runtime import Runtime
from jiuwen.core.runtime.utils import extract_origin_key, is_ref_path


class CommonNode(ComponentExecutable, WorkflowComponent):

    def __init__(self, node_id: str):
        super().__init__()
        self.node_id = node_id

    async def invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        return inputs

    async def stream(self, inputs: Input, runtime: Runtime, context: Context) -> AsyncIterator[Output]:
        yield await self.invoke(inputs, runtime, context)


class AddTenNode(ComponentExecutable, WorkflowComponent):

    def __init__(self, node_id: str, check_map: dict = None):
        super().__init__()
        self.node_id = node_id
        self.check_map = check_map

    @staticmethod
    def generate_value(runtime: Runtime, value: Any):
        if isinstance(value, str) and is_ref_path(value):
            ref_str = extract_origin_key(value)
            return runtime.get_global_state(ref_str)
        return value

    async def invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        if self.check_map:
            for key, value in self.check_map.items():
                assert inputs.get(key) == self.generate_value(runtime, value)
        return {"result": inputs["source"] + 10}
