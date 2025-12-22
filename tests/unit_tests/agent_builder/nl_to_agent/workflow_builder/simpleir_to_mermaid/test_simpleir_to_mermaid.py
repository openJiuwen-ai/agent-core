#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved
import unittest
from unittest.mock import patch

from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.simpleir_to_mermaid import SimpleIrToMermaid


class TestSimpleIrToMermaid(unittest.TestCase):
    def setUp(self):
        self.converter = SimpleIrToMermaid()

    def test_edge_transform_basic_next(self):
        nodes = [
            {"id": "1", "description": "开始", "next": "2"},
            {"id": "2", "description": "结束", "type": "End"}
        ]

        edges = SimpleIrToMermaid._edge_transform(nodes)

        expected = [{"来源": "1", "去向": "2"}]
        self.assertEqual(edges, expected)

    def test_edge_transform_no_next_with_conditions(self):
        nodes = [
            {
                "id": "1",
                "description": "条件节点",
                "type": "Condition",
                "parameters": {
                    "conditions": [
                        {"branch": "是", "next": "2", "description": "当满足条件[A]"},
                        {"branch": "否", "next": "3", "description": "当满足条件[B]"}
                    ]
                }
            },
            {"id": "2", "description": "分支1"},
            {"id": "3", "description": "分支2"}
        ]

        edges = SimpleIrToMermaid._edge_transform(nodes)

        expected = [
            {"来源": "1", "去向": "2", "分支": "是", "描述": "当满足条件[A]"},
            {"来源": "1", "去向": "3", "分支": "否", "描述": "当满足条件[B]"}
        ]
        self.assertEqual(len(edges), 2)
        self.assertEqual(edges, expected)

    def test_edge_transform_end_node(self):
        nodes = [
            {"id": "1", "description": "结束节点", "type": "End"}
        ]

        edges = SimpleIrToMermaid._edge_transform(nodes)
        self.assertEqual(edges, [])

    def test_edge_transform_mixed_nodes(self):
        nodes = [
            {"id": "1", "description": "开始", "next": "2"},
            {
                "id": "2",
                "description": "条件判断",
                "parameters": {
                    "conditions": [
                        {"branch": "成功", "next": "3", "description": "当操作成功"},
                        {"branch": "失败", "next": "4", "description": "当操作失败"}
                    ]
                }
            },
            {"id": "3", "description": "成功处理", "next": "5"},
            {"id": "4", "description": "失败处理", "next": "5"},
            {"id": "5", "description": "结束", "type": "End"}
        ]

        edges = SimpleIrToMermaid._edge_transform(nodes)

        self.assertEqual(len(edges), 5)
        sources = [e["来源"] for e in edges]
        self.assertIn("1", sources)
        self.assertIn("2", sources)
        self.assertIn("3", sources)
        self.assertIn("4", sources)

    def test_trans_to_mermaid_basic(self):
        data = {
            "nodes": [
                {"id": "1", "description": "开始节点"},
                {"id": "2", "description": "结束节点"}
            ],
            "edges": [
                {"来源": "1", "去向": "2"}
            ]
        }

        result = SimpleIrToMermaid._trans_to_mermaid(data)

        self.assertIn("graph TD", result)
        self.assertIn("1[节点1: 开始节点]", result)
        self.assertIn("2[节点2: 结束节点]", result)
        self.assertIn("1 --> 2", result)

    def test_trans_to_mermaid_multiple_edges(self):
        data = {
            "nodes": [
                {"id": "1", "description": "条件节点"},
                {"id": "2", "description": "分支1"},
                {"id": "3", "description": "分支2"}
            ],
            "edges": [
                {"来源": "1", "去向": "2", "描述": "当满足条件[A]"},
                {"来源": "1", "去向": "3", "描述": "当满足条件[B]"}
            ]
        }

        with patch('openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.simpleir_to_mermaid.Counter') as mock_counter:
            mock_counter.return_value = {"1": 2}

            result = SimpleIrToMermaid._trans_to_mermaid(data)

            self.assertIn("-- 当满足条件[A] -->", result)
            self.assertIn("-- 当满足条件[B] -->", result)

    def test_trans_to_mermaid_condition_extraction(self):
        data = {
            "nodes": [
                {"id": "1", "description": "条件节点"},
                {"id": "2", "description": "目标节点"}
            ],
            "edges": [
                {"来源": "1", "去向": "2", "描述": "当满足条件[价格>100]"}
            ]
        }

        with patch('openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.simpleir_to_mermaid.Counter') as mock_counter:
            mock_counter.return_value = {"1": 2}

            result = SimpleIrToMermaid._trans_to_mermaid(data)

            self.assertIn("-- 当满足条件[价格>100] -->", result)

    def test_trans_to_mermaid_special_characters(self):
        data = {
            "nodes": [
                {"id": "1", "description": "测试`引号\"处理"}
            ],
            "edges": []
        }

        result = SimpleIrToMermaid._trans_to_mermaid(data)

        self.assertIn("测试'引号'处理", result)
        self.assertNotIn("`", result)
        self.assertNotIn('"', result)

    def test_transform_to_mermaid_integration(self):
        json_data = [
            {
                "id": "1",
                "description": "开始",
                "next": "2"
            },
            {
                "id": "2",
                "description": "条件判断",
                "parameters": {
                    "conditions": [
                        {"branch": "是", "next": "3", "description": "当满足条件[X]"},
                        {"branch": "否", "next": "4", "description": "当满足条件[Y]"}
                    ]
                }
            },
            {"id": "3", "description": "分支A", "next": "5"},
            {"id": "4", "description": "分支B", "next": "5"},
            {"id": "5", "description": "结束", "type": "End"}
        ]

        result = self.converter.transform_to_mermaid(json_data)

        self.assertIn("graph TD", result)
        self.assertIn("1[节点1: 开始]", result)
        self.assertIn("2[节点2: 条件判断]", result)

        self.assertIn("1 --> 2", result)
        self.assertIn("3 --> 5", result)
        self.assertIn("4 --> 5", result)

    def test_edge_transform_empty_nodes(self):
        edges = SimpleIrToMermaid._edge_transform([])
        self.assertEqual(edges, [])

    def test_trans_to_mermaid_empty_data(self):
        data = {"nodes": [], "edges": []}
        result = SimpleIrToMermaid._trans_to_mermaid(data)
        self.assertEqual(result, "graph TD")

    def test_edge_transform_condition_without_next(self):
        nodes = [
            {
                "id": "1",
                "parameters": {
                    "conditions": [
                        {"branch": "是", "description": "无目标条件"}
                    ]
                }
            }
        ]

        edges = SimpleIrToMermaid._edge_transform(nodes)
        self.assertEqual(edges, [])

    @patch('openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.simpleir_to_mermaid.re.search')
    def test_trans_to_mermaid_regex_failure(self, mock_search):
        mock_search.return_value = None

        data = {
            "nodes": [
                {"id": "1", "description": "测试节点"}
            ],
            "edges": [
                {"来源": "1", "去向": "2", "描述": "不匹配的描述"}
            ]
        }

        with patch('openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.simpleir_to_mermaid.Counter') as mock_counter:
            mock_counter.return_value = {"1": 2}

            result = SimpleIrToMermaid._trans_to_mermaid(data)

            self.assertIn("-- 不匹配的描述 -->", result)

    def test_trans_to_mermaid_edge_without_description(self):
        data = {
            "nodes": [
                {"id": "1", "description": "节点1"},
                {"id": "2", "description": "节点2"}
            ],
            "edges": [
                {"来源": "1", "去向": "2"}
            ]
        }

        result = SimpleIrToMermaid._trans_to_mermaid(data)
        self.assertIn("1 --> 2", result)


if __name__ == '__main__':
    unittest.main(verbosity=2)
