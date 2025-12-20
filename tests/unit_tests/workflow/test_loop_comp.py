#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import unittest

from jiuwen.core.component.end_comp import End
from jiuwen.core.component.loop_comp import LoopGroup, LoopComponent
from jiuwen.core.component.set_variable_comp import SetVariableComponent
from jiuwen.core.component.start_comp import Start
from jiuwen.core.runtime.workflow import WorkflowRuntime
from jiuwen.core.workflow.base import Workflow
from test_node import AddTenNode


class TestLoopComp(unittest.TestCase):
    def test_loop_number(self):
        flow = Workflow()
        flow.set_start_comp("start", Start(), inputs_schema={"input_arr": "${array}", "input_num": "${num}"})
        flow.set_end_comp("end", End(), inputs_schema={"end_out": "${loop}"})

        loop_group = LoopGroup()
        loop_group.add_workflow_comp("loop_1", AddTenNode("loop_1"), inputs_schema={"source": "${loop.index}"})
        loop_group.add_workflow_comp("loop_2", AddTenNode("loop_2"), inputs_schema={"source": "${loop.user_num}"})


        set_variable_component = SetVariableComponent({"${loop.user_num}":"${loop_2.result}"})

        loop_group.add_workflow_comp("loop_3", set_variable_component)
        loop_group.start_nodes(["loop_1"])
        loop_group.end_nodes(["loop_3"])
        loop_group.add_connection("loop_1", "loop_2")
        loop_group.add_connection("loop_2", "loop_3")

        loop_component = LoopComponent(loop_group, output_schema={"l_out1": "${loop_1.result}", "l_out2":"${loop_2.result}" })

        flow.add_workflow_comp("loop", loop_component, inputs_schema={"loop_type": "number", "loop_number": 12, "intermediate_var": {"user_num": "${start.input_num}"}})

        flow.add_connection("start", "loop")
        flow.add_connection("loop", "end")

        inputs = {"array":[4,5,6], "num":-3}

        async def run_workflow():
            return await flow.invoke(inputs, runtime=WorkflowRuntime())

        results = asyncio.get_event_loop().run_until_complete(run_workflow())
        assert results.result == {'responseContent': '', 'output': {'end_out': {'user_num': 117, 'index': 12, 'l_out1': [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21], 'l_out2': [7, 17, 27, 37, 47, 57, 67, 77, 87, 97, 107, 117]}}}

