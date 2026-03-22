# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import pytest

from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.simpleir_to_mermaid import SimpleIrToMermaid


class TestSimpleIrToMermaidEdgeTransform:
    """Test SimpleIrToMermaid.edge_transform method."""

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
        assert edges[0]["branch"] == "branch_1"
        assert edges[1]["target"] == "node_3"

    @staticmethod
    def test_edge_transform_empty_nodes():
        """Test edge transform with empty nodes."""
        edges = SimpleIrToMermaid.edge_transform([])
        
        assert len(edges) == 0

    @staticmethod
    def test_edge_transform_end_node_no_edge():
        """Test edge transform with End node."""
        nodes = [
            {"id": "node_1", "type": "End"}
        ]
        
        edges = SimpleIrToMermaid.edge_transform(nodes)
        
        assert len(edges) == 0


class TestSimpleIrToMermaidTransToMermaid:
    """Test SimpleIrToMermaid.trans_to_mermaid method."""

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
    def test_trans_to_mermaid_with_branch():
        """Test mermaid transformation with branch."""
        data = {
            "nodes": [
                {"id": "node_1", "description": "Branch Node"},
                {"id": "node_2", "description": "End 1"},
                {"id": "node_3", "description": "End 2"}
            ],
            "edges": [
                {"source": "node_1", "target": "node_2", "branch": "branch_1", "description": "cond1"},
                {"source": "node_1", "target": "node_3", "branch": "branch_2", "description": "cond2"}
            ]
        }
        
        result = SimpleIrToMermaid.trans_to_mermaid(data)
        
        assert "graph TD" in result
        assert result.count("node_1") >= 1

    @staticmethod
    def test_trans_to_mermaid_empty_data():
        """Test mermaid transformation with empty data."""
        data = {"nodes": [], "edges": []}
        
        result = SimpleIrToMermaid.trans_to_mermaid(data)
        
        assert "graph TD" in result


class TestSimpleIrToMermaidTransformToMermaid:
    """Test SimpleIrToMermaid.transform_to_mermaid method."""

    @staticmethod
    def test_transform_to_mermaid_success():
        """Test successful transformation."""
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
        """Test transformation with LLM node."""
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
        """Test transformation with empty nodes."""
        result = SimpleIrToMermaid.transform_to_mermaid([])
        
        assert "graph TD" in result

    @staticmethod
    def test_transform_to_mermaid_special_characters():
        """Test transformation with special characters in description."""
        nodes = [
            {
                "id": "node_1",
                "type": "Start",
                "description": "Test `special` \"chars\"",
                "next": "node_2"
            },
            {
                "id": "node_2",
                "type": "End",
                "description": "End"
            }
        ]
        
        result = SimpleIrToMermaid.transform_to_mermaid(nodes)
        
        assert "graph TD" in result
        assert "`" not in result or "'" in result
