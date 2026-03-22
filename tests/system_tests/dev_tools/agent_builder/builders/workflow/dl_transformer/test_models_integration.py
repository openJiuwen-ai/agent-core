# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
System tests for DL Transformer Models module.

Tests DL transformer models integration.
"""
import pytest

from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models import (
    DataConfig,
    Edge,
    InputsField,
    InputVariable,
    Node,
    NodeType,
    OutputPropertySpec,
    OutputsField,
    Position,
    SourceType,
    Workflow,
)


class TestNodeTypeIntegration:
    """Test NodeType enum integration."""

    @staticmethod
    def test_all_node_types_have_dl_type():
        """Test all node types have dl_type."""
        node_types = [
            NodeType.Start, NodeType.End, NodeType.LLM,
            NodeType.IntentDetection, NodeType.Questioner,
            NodeType.Code, NodeType.Plugin, NodeType.Output, NodeType.Branch
        ]
        
        for node_type in node_types:
            assert hasattr(node_type, 'dl_type')
            assert node_type.dl_type is not None

    @staticmethod
    def test_all_node_types_have_dsl_type():
        """Test all node types have dsl_type."""
        node_types = [
            NodeType.Start, NodeType.End, NodeType.LLM,
            NodeType.IntentDetection, NodeType.Questioner,
            NodeType.Code, NodeType.Plugin, NodeType.Output, NodeType.Branch
        ]
        
        for node_type in node_types:
            assert hasattr(node_type, 'dsl_type')
            assert node_type.dsl_type is not None

    @staticmethod
    def test_node_type_mapping_consistency():
        """Test node type mapping consistency."""
        assert NodeType.Start.dl_type == "Start"
        assert NodeType.Start.dsl_type == "1"
        
        assert NodeType.End.dl_type == "End"
        assert NodeType.End.dsl_type == "2"
        
        assert NodeType.LLM.dl_type == "LLM"
        assert NodeType.LLM.dsl_type == "3"


class TestPositionIntegration:
    """Test Position dataclass integration."""

    @staticmethod
    def test_position_creation():
        """Test Position creation."""
        position = Position(x=100.0, y=200.0)
        
        assert position.x == 100.0
        assert position.y == 200.0

    @staticmethod
    def test_position_attributes():
        """Test Position attributes."""
        position = Position(x=100.0, y=200.0)
        
        assert hasattr(position, 'x')
        assert hasattr(position, 'y')


class TestInputVariableIntegration:
    """Test InputVariable dataclass integration."""

    @staticmethod
    def test_input_variable_ref_type():
        """Test InputVariable with ref type."""
        var = InputVariable(
            type="ref",
            content=["node_start", "query"],
            extra={}
        )
        
        assert var.type == "ref"
        assert var.content == ["node_start", "query"]

    @staticmethod
    def test_input_variable_constant_type():
        """Test InputVariable with constant type."""
        var = InputVariable(
            type="constant",
            content="test value",
            extra={}
        )
        
        assert var.type == "constant"
        assert var.content == "test value"


class TestOutputsFieldIntegration:
    """Test OutputsField dataclass integration."""

    @staticmethod
    def test_outputs_field_creation():
        """Test OutputsField creation."""
        outputs = OutputsField()
        
        assert outputs.type == "object"
        assert outputs.properties is None

    @staticmethod
    def test_outputs_field_add_property():
        """Test OutputsField add_property."""
        outputs = OutputsField()
        outputs.add_property(OutputPropertySpec(
            variable_names=["output"],
            description="output description",
            index=0,
            var_type="string",
        ))
        
        assert outputs.properties is not None
        assert "output" in outputs.properties
        assert outputs.properties["output"].type == "string"

    @staticmethod
    def test_outputs_field_add_nested_property():
        """Test OutputsField add nested property."""
        outputs = OutputsField()
        outputs.add_property(OutputPropertySpec(
            variable_names=["data", "name"],
            description="name description",
            index=0,
            var_type="string",
        ))
        
        assert outputs.properties is not None
        assert "data" in outputs.properties
        assert outputs.properties["data"].type == "object"


class TestNodeIntegration:
    """Test Node dataclass integration."""

    @staticmethod
    def test_node_creation():
        """Test Node creation."""
        node = Node(id="node_1", type="1")
        
        assert node.id == "node_1"
        assert node.type == "1"
        assert node.meta == {}
        assert node.data.title == ""

    @staticmethod
    def test_node_with_meta():
        """Test Node with meta."""
        node = Node(
            id="node_1",
            type="1",
            meta={"position": {"x": 100, "y": 200}}
        )
        
        assert node.meta["position"]["x"] == 100
        assert node.meta["position"]["y"] == 200


class TestEdgeIntegration:
    """Test Edge dataclass integration."""

    @staticmethod
    def test_edge_creation():
        """Test Edge creation."""
        edge = Edge(
            source_node_id="node_1",
            target_node_id="node_2"
        )
        
        assert edge.source_node_id == "node_1"
        assert edge.target_node_id == "node_2"

    @staticmethod
    def test_edge_with_source_port():
        """Test Edge with source port."""
        edge = Edge(
            source_node_id="node_1",
            target_node_id="node_2",
            source_port_id="output_1"
        )
        
        assert edge.source_port_id == "output_1"


class TestWorkflowIntegration:
    """Test Workflow dataclass integration."""

    @staticmethod
    def test_workflow_creation():
        """Test Workflow creation."""
        workflow = Workflow()
        
        assert hasattr(workflow, '__dataclass_fields__')
