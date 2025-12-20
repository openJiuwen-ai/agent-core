#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import unittest

from jiuwen.core.common.exception.exception import JiuWenBaseException
from jiuwen.core.component.branch_comp import BranchComponent
from jiuwen.core.component.end_comp import End
from jiuwen.core.component.start_comp import Start
from jiuwen.core.runtime.workflow import WorkflowRuntime
from jiuwen.core.workflow.base import Workflow


class TestBranchComponent(unittest.TestCase):
    def test_add_branch_error(self):
        branch = BranchComponent()
        with self.assertRaises(JiuWenBaseException):
            branch.add_branch(condition=None, target="a", branch_id='')
        with self.assertRaises(JiuWenBaseException):
            branch.add_branch(condition="sss", target='', branch_id='')
        with self.assertRaises(JiuWenBaseException):
            branch.add_branch(condition="sss", target=None, branch_id='')
        with self.assertRaises(JiuWenBaseException):
            branch.add_branch(condition="sss", target=['', "xxx"], branch_id='')
        with self.assertRaises(JiuWenBaseException):
            branch.add_branch(condition="sss", target=["xxx", None], branch_id='')


    def runtime_with_expression(self, expression, value):
        workflow = Workflow()
        workflow.set_start_comp("start", Start(), inputs_schema={"input": "${data}"})
        branch_comp = BranchComponent()
        branch_comp.add_branch(condition=expression, target=["print_inputs"])
        workflow.add_workflow_comp("branch_component", branch_comp)
        workflow.add_workflow_comp("print_inputs", Start(), inputs_schema={"data": "${start}"})
        workflow.set_end_comp("end", End(), inputs_schema={"end_out": "${print_inputs}"})

        workflow.add_connection("start", "branch_component")
        workflow.add_connection("print_inputs", "end")

        inputs = {"data": value}

        async def run_workflow():
            return await workflow.invoke(inputs, WorkflowRuntime())

        asyncio.get_event_loop().run_until_complete(run_workflow())

    def test_expression_is_empty(self):
       self.runtime_with_expression("is_empty(${start.input})", None)
       self.runtime_with_expression("is_empty(${start.input})", [])
       self.runtime_with_expression("is_empty(${start.input})", '')
       self.runtime_with_expression("is_empty(${start.input})", {})
       with self.assertRaises(JiuWenBaseException) as error:
           self.runtime_with_expression("is_empty(${start.input})", 0)

       with self.assertRaises(JiuWenBaseException) as error:
           self.runtime_with_expression("is_not_empty(${start.input})", 1.2)

       self.runtime_with_expression("is_empty(${start.input}[0])", [None, 'y'])
       self.runtime_with_expression("is_empty(${start.input}['x'])", {'x': None})
       self.runtime_with_expression("is_empty(${start.input}['x'][0])", {'x': [None]})

    def test_expression_is_not_empty(self):
        self.runtime_with_expression("is_not_empty(${start.input})", 'x')
        self.runtime_with_expression("is_not_empty(${start.input})", {'a':'a'})
        self.runtime_with_expression("is_not_empty(${start.input})", ['a'])
        self.runtime_with_expression("is_not_empty(${start.input})", (1,2))
        with self.assertRaises(JiuWenBaseException) as error:
            self.runtime_with_expression("is_not_empty(${start.input})", None)

        with self.assertRaises(JiuWenBaseException) as error:
            self.runtime_with_expression("is_not_empty(${start.input})", None)

        with self.assertRaises(JiuWenBaseException) as error:
            self.runtime_with_expression("is_not_empty(${start.input})", 1.2)

        self.runtime_with_expression("is_not_empty(${start.input}[0])", ['x', 'y'])
        self.runtime_with_expression("is_not_empty(${start.input}['x'])", {'x' : 'x'})
        self.runtime_with_expression("is_not_empty(${start.input}['x'][0])", {'x' : ['x']})