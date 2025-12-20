#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Union

from jiuwen.core.runtime.agent import StaticAgentRuntime
from jiuwen.core.runtime.config import Config
from jiuwen.core.runtime.runtime import Runtime
from jiuwen.core.runtime.wrapper import TaskRuntime, WrappedRuntime, StaticWrappedRuntime
from jiuwen.core.stream.base import OutputSchema


class AgentRuntime(WrappedRuntime, StaticWrappedRuntime):
    def __init__(self, config: Config = None):
        inner = StaticAgentRuntime(config)
        super().__init__(inner)
        self._runtime = inner

    async def write_stream(self, data: Union[dict, OutputSchema]):
        return await self.write_custom_stream(data)

    async def pre_run(self, **kwargs) -> Runtime:
        session_id = kwargs.get("session_id")
        if session_id is None:
            session_id = kwargs.get("trace_id")
        inputs = kwargs.get("inputs")
        inner = await self._runtime.create_agent_runtime(session_id, inputs)
        return TaskRuntime(inner=inner)

    async def release(self, session_id: str):
        await self._runtime.checkpointer().release(session_id)