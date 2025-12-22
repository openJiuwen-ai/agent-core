#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved
import json
import unittest
from unittest.mock import Mock, patch

from openjiuwen.agent_builder.nl_to_agent.workflow_builder.intention_detector.intention_detector import \
    IntentionDetector
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.foundation.llm import SystemMessage


class TestIntentionDetector(unittest.TestCase):

    def setUp(self):
        self.mock_model = Mock()
        self.detector = IntentionDetector(self.mock_model)

    def test_format_dialog_history_normal(self):
        dialog_history = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "有什么可以帮助你的？"},
            {"role": "user", "content": "我想创建一个流程"}
        ]

        result = self.detector._format_dialog_history(dialog_history)
        expected = "用户：你好\n助手：有什么可以帮助你的？\n用户：我想创建一个流程"
        self.assertEqual(result, expected)

    def test_format_dialog_history_empty(self):
        result = self.detector._format_dialog_history([])
        self.assertEqual(result, "")

    def test_format_dialog_history_missing_fields(self):
        dialog_history = [
            {"role": "user"},  # 缺少content
            {"role": "assistant", "content": "测试内容"},
            {"content": "缺少role"}  # 缺少role
        ]

        result = self.detector._format_dialog_history(dialog_history)
        self.assertTrue("用户：" in result)
        self.assertTrue("助手：测试内容" in result)

    def test_format_dialog_history_unknown_role(self):
        dialog_history = [
            {"role": "unknown", "content": "测试内容"}
        ]

        result = self.detector._format_dialog_history(dialog_history)
        self.assertEqual(result, "用户：测试内容")

    def test_extract_intent_with_json_block(self):
        test_input = '```json\n{"provide_process": true}\n```'
        result = self.detector._extract_intent(test_input)
        self.assertEqual(result, {"provide_process": True})

    def test_extract_intent_without_json_block(self):
        test_input = '{"provide_process": false}'
        result = self.detector._extract_intent(test_input)
        self.assertEqual(result, {"provide_process": False})

    def test_extract_intent_invalid_json(self):
        test_input = "invalid json"
        with self.assertRaises(json.JSONDecodeError):
            self.detector._extract_intent(test_input)

    def test_extract_intent_empty_string(self):
        test_input = ""
        with self.assertRaises(json.JSONDecodeError):
            self.detector._extract_intent(test_input)

    def test_detect_initial_instruction_true(self):
        self.mock_model.chat.return_value = '{"provide_process": true}'

        messages = [{"role": "user", "content": "我想创建一个流程"}]

        result = self.detector.detect_initial_instruction(messages)
        self.assertTrue(result)
        self.mock_model.chat.assert_called_once()

        args, kwargs = self.mock_model.chat.call_args
        self.assertEqual(len(args[0]), 1)
        self.assertIsInstance(args[0][0], SystemMessage)
        self.assertEqual(kwargs["method"], "invoke")
        self.assertEqual(kwargs["add_prefix"], False)

    def test_detect_initial_instruction_false(self):
        self.mock_model.chat.return_value = '{"provide_process": false}'

        messages = [{"role": "user", "content": "你好"}]

        result = self.detector.detect_initial_instruction(messages)
        self.assertFalse(result)

    def test_detect_initial_instruction_empty_messages(self):
        result = self.detector.detect_initial_instruction([])
        self.assertFalse(result)

    def test_detect_initial_instruction_model_error(self):
        self.mock_model.chat.side_effect = Exception("模型服务错误")

        messages = [{"role": "user", "content": "测试"}]

        with self.assertRaises(JiuWenBaseException) as context:
            self.detector.detect_initial_instruction(messages)

        self.assertEqual(context.exception.error_code, StatusCode.NL2AGENT_WORKFLOW_INTENTION_DETECT_ERROR.code)
        self.assertIn("NL2Workflow流程意图判断出现异常", context.exception.message)


    def test_detect_initial_instruction_json_error(self):
        self.mock_model.chat.return_value = "invalid json"

        messages = [{"role": "user", "content": "测试"}]

        with self.assertRaises(JiuWenBaseException):
            self.detector.detect_initial_instruction(messages)

    def test_detect_refine_intent_true(self):
        self.mock_model.chat.return_value = '{"need_refined": true}'

        messages = [{"role": "user", "content": "需要修改流程图"}]
        flowchart_code = "graph TD\nA-->B"

        result = self.detector.detect_refine_intent(messages, flowchart_code)
        self.assertTrue(result)

        self.mock_model.chat.assert_called_once()
        args, kwargs = self.mock_model.chat.call_args

        self.assertEqual(len(args[0]), 1)
        self.assertIsInstance(args[0][0], SystemMessage)
        self.assertEqual(kwargs["method"], "invoke")
        self.assertEqual(kwargs["add_prefix"], False)

    def test_detect_refine_intent_false(self):
        self.mock_model.chat.return_value = '{"need_refined": false}'

        messages = [{"role": "user", "content": "流程图很好"}]
        flowchart_code = "graph TD\nA-->B"

        result = self.detector.detect_refine_intent(messages, flowchart_code)
        self.assertFalse(result)

    def test_detect_refine_intent_empty_messages(self):
        flowchart_code = "graph TD\nA-->B"
        result = self.detector.detect_refine_intent([], flowchart_code)
        self.assertFalse(result)

    def test_detect_refine_intent_model_error(self):
        self.mock_model.chat.side_effect = Exception("模型服务错误")

        messages = [{"role": "user", "content": "测试"}]
        flowchart_code = "graph TD\nA-->B"

        with self.assertRaises(JiuWenBaseException) as context:
            self.detector.detect_refine_intent(messages, flowchart_code)

        self.assertEqual(context.exception.error_code, StatusCode.NL2AGENT_WORKFLOW_INTENTION_DETECT_ERROR.code)
        self.assertIn("NL2Workflow流程意图判断出现异常", context.exception.message)

    def test_detect_refine_intent_json_error(self):
        self.mock_model.chat.return_value = "invalid json"

        messages = [{"role": "user", "content": "测试"}]
        flowchart_code = "graph TD\nA-->B"

        with self.assertRaises(JiuWenBaseException):
            self.detector.detect_refine_intent(messages, flowchart_code)

    def test_detect_refine_intent_none_flowchart_code(self):
        self.mock_model.chat.return_value = '{"need_refined": false}'
        messages = [{"role": "user", "content": "测试"}]
        result = self.detector.detect_refine_intent(messages, "")
        self.assertFalse(result)

    @patch(
        'openjiuwen.agent_builder.nl_to_agent.workflow_builder.intention_detector.intention_detector.INITIAL_INTENTION_PROMPT',
        '{{dialog_history}}')
    def test_prompt_template_usage_initial(self):
        self.mock_model.chat.return_value = '{"provide_process": true}'

        messages = [{"role": "user", "content": "测试"}]
        result = self.detector.detect_initial_instruction(messages)
        self.assertTrue(result)

        args, _ = self.mock_model.chat.call_args
        system_message = args[0][0]
        self.assertIn("用户：测试", system_message.content)

    @patch(
        'openjiuwen.agent_builder.nl_to_agent.workflow_builder.intention_detector.intention_detector.REFINE_INTENTION_PROMPT',
        '{{mermaid_code}}\n{{dialog_history}}')
    def test_prompt_template_usage_refine(self):
        self.mock_model.chat.return_value = '{"need_refined": true}'

        messages = [{"role": "user", "content": "测试"}]
        flowchart_code = "graph TD\nA-->B"

        result = self.detector.detect_refine_intent(messages, flowchart_code)
        self.assertTrue(result)

        args, _ = self.mock_model.chat.call_args
        system_message = args[0][0]
        self.assertIn("graph TD\nA-->B", system_message.content)
        self.assertIn("用户：测试", system_message.content)


if __name__ == '__main__':
    unittest.main(verbosity=2)
