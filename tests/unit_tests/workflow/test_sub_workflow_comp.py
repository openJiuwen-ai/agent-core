#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import unittest

from jiuwen.core.common.exception.exception import JiuWenBaseException
from jiuwen.core.component.end_comp import End
from jiuwen.core.component.start_comp import Start
from jiuwen.core.component.workflow_comp import SubWorkflowComponent
from jiuwen.core.runtime.workflow import WorkflowRuntime
from jiuwen.core.workflow.base import Workflow
from jiuwen.core.workflow.workflow_config import WorkflowConfig


class TestSubWorkflowComp(unittest.TestCase):
    def test_add_component(self):
        main_workflow = Workflow(WorkflowConfig(workflow_max_nesting_depth=2))
        main_workflow.set_start_comp("start", Start())
        main_workflow.add_workflow_comp("fick_comp", SubWorkflowComponent(main_workflow))
        main_workflow.set_end_comp("end", End())
        main_workflow.add_connection("start", 'fick_comp')
        main_workflow.add_connection('fick_comp', "end")
        with self.assertRaises(JiuWenBaseException):
            async def run():
                return await main_workflow.invoke(inputs={}, runtime=WorkflowRuntime())

            asyncio.get_event_loop().run_until_complete(run())

    def create_nesting_workflow(self, sub_workflow_depth=0, workflow_config=None):
        workflow = Workflow(workflow_config)
        workflow.set_start_comp("start", Start())
        if sub_workflow_depth > 0:
            workflow.add_workflow_comp(f'sub{sub_workflow_depth}',
                                       SubWorkflowComponent(self.create_nesting_workflow(sub_workflow_depth - 1)))
        workflow.set_end_comp("end", End())
        if sub_workflow_depth > 0:
            workflow.add_connection("start", f'sub{sub_workflow_depth}')
            workflow.add_connection(f'sub{sub_workflow_depth}', "end")
        else:
            workflow.add_connection("start", "end")
        return workflow

    def test_sub_invoke(self):
        with self.assertRaises(JiuWenBaseException) as err:
            workflow_config = WorkflowConfig(workflow_max_nesting_depth=1)
            main_workflow = self.create_nesting_workflow(3, workflow_config)

            async def run():
                return await main_workflow.invoke(inputs={}, runtime=WorkflowRuntime())

            asyncio.get_event_loop().run_until_complete(run())
            assert err.msg == "failed to invoke, caused by failed to invoke, caused by Sub workflow component running error, detail: workflow nesting hierarchy is too big, must <= 1"

        workflow_config = WorkflowConfig(workflow_max_nesting_depth=3)
        main_workflow = self.create_nesting_workflow(3, workflow_config)

        async def run():
            return await main_workflow.invoke(inputs={}, runtime=WorkflowRuntime())

        asyncio.get_event_loop().run_until_complete(run())

        workflow_config = WorkflowConfig(workflow_max_nesting_depth=0)
        main_workflow = self.create_nesting_workflow(0, workflow_config)

        async def run():
            return await main_workflow.invoke(inputs={}, runtime=WorkflowRuntime())

        asyncio.get_event_loop().run_until_complete(run())
