# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from unittest.mock import Mock, patch

import pytest

from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converters.base import BaseConverter
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models import (
    NodeType,
    Position,
)


class ConcreteConverter(BaseConverter):
    """Concrete converter for testing."""
    
    def _convert_specific_config(self):
        pass


class TestBaseConverterInit:
    """Test BaseConverter initialization."""

    @staticmethod
    def test_init_success():
        """Test successful initialization."""
        node_data = {
            "id": "node_1",
            "type": "Start",
            "description": "Test Node"
        }
        nodes_dict = {}
        
        converter = ConcreteConverter(node_data, nodes_dict)
        
        assert converter.node_data == node_data
        assert converter.nodes_dict == nodes_dict
        assert converter.resource is None
        assert converter.edges == []

    @staticmethod
    def test_init_with_resource():
        """Test initialization with resource."""
        node_data = {
            "id": "node_1",
            "type": "Start",
            "description": "Test Node"
        }
        resource = {"plugins": []}
        
        converter = ConcreteConverter(node_data, {}, resource=resource)
        
        assert converter.resource == resource

    @staticmethod
    def test_init_with_position():
        """Test initialization with position."""
        node_data = {
            "id": "node_1",
            "type": "Start",
            "description": "Test Node"
        }
        position = Position(100, 200)
        
        converter = ConcreteConverter(node_data, {}, position=position)
        
        assert converter.position == position

    @staticmethod
    def test_init_creates_node():
        """Test initialization creates node."""
        node_data = {
            "id": "node_1",
            "type": "Start",
            "description": "Test Node"
        }
        
        converter = ConcreteConverter(node_data, {})
        
        assert converter.node.id == "node_1"
        assert converter.node.type == NodeType.Start.dsl_type


class TestBaseConverterConvert:
    """Test BaseConverter.convert method."""

    @staticmethod
    def test_convert_calls_methods():
        """Test convert calls all methods."""
        node_data = {
            "id": "node_1",
            "type": "Start",
            "description": "Test Node",
            "next": "node_2"
        }
        
        converter = ConcreteConverter(node_data, {})
        converter.convert()
        
        assert converter.node.id == "node_1"
        assert len(converter.edges) == 1


class TestBaseConverterConvertCommonConfig:
    """Test BaseConverter.convert_common_config method."""

    @staticmethod
    def test_convert_common_config_sets_id():
        """Test common config sets node id."""
        node_data = {
            "id": "node_1",
            "type": "Start",
            "description": "Test Node"
        }
        
        converter = ConcreteConverter(node_data, {})
        converter.convert_common_config()
        
        assert converter.node.id == "node_1"

    @staticmethod
    def test_convert_common_config_sets_meta():
        """Test common config sets meta."""
        node_data = {
            "id": "node_1",
            "type": "Start",
            "description": "Test Node"
        }
        position = Position(100, 200)
        
        converter = ConcreteConverter(node_data, {}, position=position)
        converter.convert_common_config()
        
        assert "position" in converter.node.meta
        assert converter.node.meta["position"]["x"] == 100
        assert converter.node.meta["position"]["y"] == 200

    @staticmethod
    def test_convert_common_config_sets_title():
        """Test common config sets title."""
        node_data = {
            "id": "node_1",
            "type": "Start",
            "description": "Test Node Description"
        }
        
        converter = ConcreteConverter(node_data, {})
        converter.convert_common_config()
        
        assert converter.node.data.title == "Test Node Description"


class TestBaseConverterConvertEdges:
    """Test BaseConverter.convert_edges method."""

    @staticmethod
    def test_convert_edges_with_next():
        """Test convert edges with next field."""
        node_data = {
            "id": "node_1",
            "type": "Start",
            "next": "node_2"
        }
        
        converter = ConcreteConverter(node_data, {})
        converter.convert_edges()
        
        assert len(converter.edges) == 1
        assert converter.edges[0].source_node_id == "node_1"
        assert converter.edges[0].target_node_id == "node_2"

    @staticmethod
    def test_convert_edges_without_next():
        """Test convert edges without next field."""
        node_data = {
            "id": "node_1",
            "type": "End"
        }
        
        converter = ConcreteConverter(node_data, {})
        converter.convert_edges()
        
        assert len(converter.edges) == 0

    @staticmethod
    def test_convert_edges_with_empty_next():
        """Test convert edges with empty next."""
        node_data = {
            "id": "node_1",
            "type": "Start",
            "next": ""
        }
        
        converter = ConcreteConverter(node_data, {})
        converter.convert_edges()
        
        assert len(converter.edges) == 0
