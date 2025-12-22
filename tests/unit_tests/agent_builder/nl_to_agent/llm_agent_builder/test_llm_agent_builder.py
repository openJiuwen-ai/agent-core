#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import unittest
from unittest.mock import MagicMock, patch

import openjiuwen.agent_builder.nl_to_agent.llm_agent_builder.llm_agent_builder as lb
from openjiuwen.agent_builder.nl_to_agent.llm_agent_builder.llm_agent_builder import LlmAgentBuilder, State
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode

CLARIFIED_CONTENT = "clarified_content"
GENERATED_CONTENT = "generated_content"
DSL = "dsl"


class TestLlmAgentBuilder(unittest.TestCase):
    def setUp(self):
        self.llm = MagicMock(name="llm")
        self.context_manager = MagicMock(name="context_manager")
        self.context_manager.get_history.return_value = ["query"]

        self.mock_clarifier = MagicMock(name="Clarifier")
        self.mock_clarifier.clarify.return_value = CLARIFIED_CONTENT

        self.mock_generator = MagicMock(name="Generator")
        self.mock_generator.generate.return_value = GENERATED_CONTENT

        self.mock_transformer = MagicMock(name="Transformer")
        self.mock_transformer.transform_to_dsl.return_value = DSL

        self.mock_resource_retriever = MagicMock(name="ResourceRetriever")
        self.resource = {"mock_plugin": [{"resource_id": "1"}]}
        self.mock_resource_retriever.retrieve.return_value = {"mock_plugin": [{"resource_id": "1"}]}

    def test_init_builder(self):
        with patch.object(lb, "ResourceRetriever", return_value=self.mock_resource_retriever), \
             patch.object(lb, 'Clarifier', return_value=self.mock_clarifier), \
             patch.object(lb, 'Generator', return_value=self.mock_generator), \
             patch.object(lb, 'Transformer', return_value=self.mock_transformer):
            builder = LlmAgentBuilder(self.llm, self.context_manager)

        self.assertEqual(builder._state, State.INITIAL)
        self.assertIsNone(builder._agent_config_info)
        self.assertEqual(builder._resource, {})

    def test_invalid_state(self):
        with patch.object(lb, "ResourceRetriever", return_value=self.mock_resource_retriever), \
             patch.object(lb, 'Clarifier', return_value=self.mock_clarifier), \
             patch.object(lb, 'Generator', return_value=self.mock_generator), \
             patch.object(lb, 'Transformer', return_value=self.mock_transformer):
            builder = LlmAgentBuilder(self.llm, self.context_manager)
            builder._state = 'test'

        with self.assertRaises(JiuWenBaseException) as cm:
            result = builder.execute("query")
        self.assertIn(str(StatusCode.NL2AGENT_LLM_AGENT_STATE_ERROR.code), str(cm.exception))
        self.assertIn("未知的LLM Agent构建阶段", str(cm.exception))

    def test_update_resource(self):
        with patch.object(lb, "ResourceRetriever", return_value=self.mock_resource_retriever), \
             patch.object(lb, 'Clarifier', return_value=self.mock_clarifier), \
             patch.object(lb, 'Generator', return_value=self.mock_generator), \
             patch.object(lb, 'Transformer', return_value=self.mock_transformer):
            builder = LlmAgentBuilder(self.llm, self.context_manager)
            builder._update_resource("query")
            self.assertEqual(builder._resource, {"mock_plugin": [{"resource_id": "1"}]})

            builder._update_resource("query")
            self.assertEqual(builder._resource, {"mock_plugin": [{"resource_id": "1"}]})

    def test_initial(self):
        with patch.object(lb, "ResourceRetriever", return_value=self.mock_resource_retriever), \
             patch.object(lb, 'Clarifier', return_value=self.mock_clarifier), \
             patch.object(lb, 'Generator', return_value=self.mock_generator), \
             patch.object(lb, 'Transformer', return_value=self.mock_transformer):
            builder = LlmAgentBuilder(self.llm, self.context_manager)
            result = builder.execute("query")

        self.assertEqual(result, CLARIFIED_CONTENT)
        self.assertEqual(builder._state, State.CONSTRUCT)
        self.assertEqual(builder._agent_config_info, CLARIFIED_CONTENT)
        self.assertEqual(builder._resource, {"mock_plugin": [{"resource_id": "1"}]})

    def test_construct(self):
        with patch.object(lb, "ResourceRetriever", return_value=self.mock_resource_retriever), \
             patch.object(lb, 'Clarifier', return_value=self.mock_clarifier), \
             patch.object(lb, 'Generator', return_value=self.mock_generator), \
             patch.object(lb, 'Transformer', return_value=self.mock_transformer):
            builder = LlmAgentBuilder(self.llm, self.context_manager)
            builder._state = State.CONSTRUCT
            result = builder.execute("query")

        self.assertEqual(result, DSL)
        self.assertEqual(builder._state, State.INITIAL)
        self.assertIsNone(builder._agent_config_info)
        self.assertEqual(builder._resource, {})
        self.mock_generator.generate.assert_called()
        self.mock_transformer.transform_to_dsl.assert_called_with(GENERATED_CONTENT, resource=self.resource)


if __name__ == "__main__":
    unittest.main()
