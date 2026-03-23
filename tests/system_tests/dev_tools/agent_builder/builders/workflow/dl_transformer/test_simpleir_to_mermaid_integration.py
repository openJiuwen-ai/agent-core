# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
System tests for SimpleIR to Mermaid module.

Tests SimpleIrToMermaid integration.
"""
import pytest

from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.simpleir_to_mermaid import SimpleIrToMermaid


class TestSimpleIrToMermaidIntegration:
    """Test SimpleIrToMermaid integration."""

    @staticmethod
    def test_transform_to_mermaid_basic():
        """Test basic transform_to_mermaid."""
        nodes = [
            {
                "id": "node_start",
                "type": "Start",
                "description": "Start",
                "parameters": {"outputs": [{"name": "query", "description": "input"}]},
                "next": "node_end"
            },
            {
                "id": "node_end",
                "type": "End",
                "description": "End",
                "parameters": {"inputs": [], "configs": {"template": "{{result}}"}}
            }
        ]
        
        result = SimpleIrToMermaid.transform_to_mermaid(nodes)
        
        assert "graph TD" in result
        assert "node_start" in result
        assert "node_end" in result

    @staticmethod
    def test_transform_to_mermaid_with_llm():
        """Test transform_to_mermaid with LLM node."""
        nodes = [
            {
                "id": "node_start",
                "type": "Start",
                "description": "Start",
                "next": "node_llm"
            },
            {
                "id": "node_llm",
                "type": "LLM",
                "description": "LLM Node",
                "next": "node_end"
            },
            {
                "id": "node_end",
                "type": "End",
                "description": "End"
            }
        ]
        
        result = SimpleIrToMermaid.transform_to_mermaid(nodes)
        
        assert "graph TD" in result
        assert "node_llm" in result

    @staticmethod
    def test_transform_to_mermaid_empty_nodes():
        """Test transform_to_mermaid with empty nodes."""
        result = SimpleIrToMermaid.transform_to_mermaid([])
        
        assert "graph TD" in result


class TestSimpleIrToMermaidEdgeTransform:
    """Test SimpleIrToMermaid _edge_transform method."""

    @staticmethod
    def test_edge_transform_with_next():
        """Test edge transform with next field."""
        nodes = [
            {"id": "node_1", "type": "Start", "next": "node_2"},
            {"id": "node_2", "type": "End"}
        ]
        
        edges = SimpleIrToMermaid.edge_transform(nodes)
        
        assert len(edges) == 1
        assert edges[0]["source"] == "node_1"
        assert edges[0]["target"] == "node_2"

    @staticmethod
    def test_edge_transform_with_conditions():
        """Test edge transform with conditions."""
        nodes = [
            {
                "id": "node_1",
                "type": "Branch",
                "parameters": {
                    "conditions": [
                        {"branch": "branch_1", "description": "condition 1", "next": "node_2"},
                        {"branch": "branch_2", "description": "condition 2", "next": "node_3"}
                    ]
                }
            },
            {"id": "node_2", "type": "End"},
            {"id": "node_3", "type": "End"}
        ]
        
        edges = SimpleIrToMermaid.edge_transform(nodes)
        
        assert len(edges) == 2
        assert edges[0]["source"] == "node_1"
        assert edges[0]["target"] == "node_2"
        assert edges[1]["target"] == "node_3"

    @staticmethod
    def test_edge_transform_empty_nodes():
        """Test edge transform with empty nodes."""
        edges = SimpleIrToMermaid.edge_transform([])
        
        assert len(edges) == 0


class TestSimpleIrToMermaidTransToMermaid:
    """Test SimpleIrToMermaid _trans_to_mermaid method."""

    @staticmethod
    def test_trans_to_mermaid_basic():
        """Test basic mermaid transformation."""
        data = {
            "nodes": [
                {"id": "node_1", "description": "Start Node"},
                {"id": "node_2", "description": "End Node"}
            ],
            "edges": [
                {"source": "node_1", "target": "node_2"}
            ]
        }
        
        result = SimpleIrToMermaid.trans_to_mermaid(data)
        
        assert "graph TD" in result
        assert "node_1" in result
        assert "node_2" in result
        assert "-->" in result

    @staticmethod
    def test_trans_to_mermaid_with_description():
        """Test mermaid transformation with description."""
        data = {
            "nodes": [
                {"id": "node_1", "description": "Start Node"},
                {"id": "node_2", "description": "End Node"}
            ],
            "edges": [
                {"source": "node_1", "target": "node_2", "description": "condition"}
            ]
        }
        
        result = SimpleIrToMermaid.trans_to_mermaid(data)
        
        assert "Start Node" in result
        assert "End Node" in result

    @staticmethod
    def test_trans_to_mermaid_empty_data():
        """Test mermaid transformation with empty data."""
        data = {"nodes": [], "edges": []}
        
        result = SimpleIrToMermaid.trans_to_mermaid(data)
        
        assert "graph TD" in result


class TestSimpleIrToMermaidComplexWorkflow:
    """Test SimpleIrToMermaid with complex workflow."""

    @staticmethod
    def test_transform_complex_workflow():
        """Test transform complex workflow."""
        nodes = [
            {
                "id": "node_start",
                "type": "Start",
                "description": "开始",
                "parameters": {"outputs": [{"name": "query", "description": "用户输入"}]},
                "next": "node_intent"
            },
            {
                "id": "node_intent",
                "type": "IntentDetection",
                "description": "意图识别",
                "parameters": {
                    "conditions": [
                        {"branch": "branch_1", "description": "查询", "next": "node_llm"},
                        {"branch": "branch_2", "description": "闲聊", "next": "node_end"}
                    ]
                }
            },
            {
                "id": "node_llm",
                "type": "LLM",
                "description": "大模型处理",
                "next": "node_end"
            },
            {
                "id": "node_end",
                "type": "End",
                "description": "结束"
            }
        ]
        
        result = SimpleIrToMermaid.transform_to_mermaid(nodes)
        
        assert "graph TD" in result
        assert "node_start" in result
        assert "node_intent" in result
        assert "node_llm" in result
        assert "node_end" in result
