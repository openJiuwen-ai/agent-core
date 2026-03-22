# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
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


class TestNodeType:
    """Test NodeType enum."""

    @staticmethod
    def test_start_type():
        """Test Start node type."""
        assert NodeType.Start.dl_type == "Start"
        assert NodeType.Start.dsl_type == "1"

    @staticmethod
    def test_end_type():
        """Test End node type."""
        assert NodeType.End.dl_type == "End"
        assert NodeType.End.dsl_type == "2"

    @staticmethod
    def test_llm_type():
        """Test LLM node type."""
        assert NodeType.LLM.dl_type == "LLM"
        assert NodeType.LLM.dsl_type == "3"

    @staticmethod
    def test_intent_detection_type():
        """Test IntentDetection node type."""
        assert NodeType.IntentDetection.dl_type == "IntentDetection"
        assert NodeType.IntentDetection.dsl_type == "6"

    @staticmethod
    def test_questioner_type():
        """Test Questioner node type."""
        assert NodeType.Questioner.dl_type == "Questioner"
        assert NodeType.Questioner.dsl_type == "7"

    @staticmethod
    def test_code_type():
        """Test Code node type."""
        assert NodeType.Code.dl_type == "Code"
        assert NodeType.Code.dsl_type == "10"

    @staticmethod
    def test_plugin_type():
        """Test Plugin node type."""
        assert NodeType.Plugin.dl_type == "Plugin"
        assert NodeType.Plugin.dsl_type == "19"

    @staticmethod
    def test_output_type():
        """Test Output node type."""
        assert NodeType.Output.dl_type == "Output"
        assert NodeType.Output.dsl_type == "9"

    @staticmethod
    def test_branch_type():
        """Test Branch node type."""
        assert NodeType.Branch.dl_type == "Branch"
        assert NodeType.Branch.dsl_type == "4"


class TestSourceType:
    """Test SourceType enum."""

    @staticmethod
    def test_ref_type():
        """Test ref source type."""
        assert SourceType.ref.value == "ref"

    @staticmethod
    def test_constant_type():
        """Test constant source type."""
        assert SourceType.constant.value == "constant"


class TestPosition:
    """Test Position dataclass."""

    @staticmethod
    def test_init_success():
        """Test successful initialization."""
        position = Position(x=100.0, y=200.0)
        
        assert position.x == 100.0
        assert position.y == 200.0

    @staticmethod
    def test_init_with_integers():
        """Test initialization with integers."""
        position = Position(x=100, y=200)
        
        assert position.x == 100
        assert position.y == 200

    @staticmethod
    def test_init_with_zero():
        """Test initialization with zero."""
        position = Position(x=0, y=0)
        
        assert position.x == 0
        assert position.y == 0


class TestInputVariable:
    """Test InputVariable dataclass."""

    @staticmethod
    def test_init_success():
        """Test successful initialization."""
        var = InputVariable(
            type="ref",
            content=["node_start", "query"],
            extra={}
        )
        
        assert var.type == "ref"
        assert var.content == ["node_start", "query"]
        assert var.extra == {}

    @staticmethod
    def test_init_with_schema():
        """Test initialization with schema."""
        var = InputVariable(
            type="constant",
            content="test value",
            extra={},
            schema={"type": "string"}
        )
        
        assert var.schema == {"type": "string"}


class TestInputsField:
    """Test InputsField dataclass."""

    @staticmethod
    def test_init_success():
        """Test successful initialization."""
        inputs = InputsField()
        
        assert inputs.input_parameters == {}
        assert inputs.llm_param is None
        assert inputs.system_prompt is None
        assert inputs.intents is None
        assert inputs.language is None
        assert inputs.code is None
        assert inputs.plugin_param is None
        assert inputs.content is None
        assert inputs.history_enable is None
        assert inputs.max_response is None

    @staticmethod
    def test_init_with_llm_param():
        """Test initialization with LLM param."""
        inputs = InputsField(llm_param={"system_prompt": "test"})
        
        assert inputs.llm_param == {"system_prompt": "test"}


class TestOutputsField:
    """Test OutputsField dataclass."""

    @staticmethod
    def test_init_success():
        """Test successful initialization."""
        outputs = OutputsField()
        
        assert outputs.type == "object"
        assert outputs.properties is None
        assert outputs.required is None
        assert outputs.description is None
        assert outputs.default is None
        assert outputs.extra is None
        assert outputs.items is None

    @staticmethod
    def test_init_with_type():
        """Test initialization with type."""
        outputs = OutputsField(type="string")
        
        assert outputs.type == "string"

    @staticmethod
    def test_add_property_simple():
        """Test add_property with simple variable."""
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
        assert outputs.properties["output"].description == "output description"

    @staticmethod
    def test_add_property_nested():
        """Test add_property with nested variable."""
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
        assert outputs.properties["data"].properties is not None
        assert "name" in outputs.properties["data"].properties

    @staticmethod
    def test_add_property_with_items():
        """Test add_property with items for array type."""
        outputs = OutputsField()
        outputs.add_property(OutputPropertySpec(
            variable_names=["list"],
            description="list description",
            index=0,
            var_type="array",
            items={"type": "string"},
        ))
        
        assert outputs.properties["list"].type == "array"
        assert outputs.properties["list"].items == {"type": "string"}

    @staticmethod
    def test_add_property_object_type():
        """Test add_property with object type."""
        outputs = OutputsField()
        outputs.add_property(OutputPropertySpec(
            variable_names=["config"],
            description="config description",
            index=0,
            var_type="object",
            properties={"key": {}},
            required=["key"],
        ))
        
        assert outputs.properties["config"].type == "object"
        assert outputs.properties["config"].properties == {"key": {}}
        assert outputs.properties["config"].required == ["key"]

    @staticmethod
    def test_add_property_empty_variable_names():
        """Test add_property with empty variable names."""
        outputs = OutputsField()
        outputs.add_property(OutputPropertySpec(
            variable_names=[],
            description="description",
            index=0,
            var_type="string",
        ))
        
        assert outputs.properties is None


class TestDataConfig:
    """Test DataConfig dataclass."""

    @staticmethod
    def test_init_success():
        """Test successful initialization."""
        config = DataConfig()
        
        assert config.title == ""
        assert config.inputs is None
        assert config.outputs is None
        assert config.branches is None
        assert config.exception_config is None

    @staticmethod
    def test_init_with_title():
        """Test initialization with title."""
        config = DataConfig(title="Test Node")
        
        assert config.title == "Test Node"


class TestNode:
    """Test Node dataclass."""

    @staticmethod
    def test_init_success():
        """Test successful initialization."""
        node = Node(id="node_1", type="1")
        
        assert node.id == "node_1"
        assert node.type == "1"
        assert node.meta == {}
        assert node.data.title == ""

    @staticmethod
    def test_init_with_meta():
        """Test initialization with meta."""
        node = Node(
            id="node_1",
            type="1",
            meta={"position": {"x": 100, "y": 200}}
        )
        
        assert node.meta == {"position": {"x": 100, "y": 200}}


class TestEdge:
    """Test Edge dataclass."""

    @staticmethod
    def test_init_success():
        """Test successful initialization."""
        edge = Edge(
            source_node_id="node_1",
            target_node_id="node_2"
        )
        
        assert edge.source_node_id == "node_1"
        assert edge.target_node_id == "node_2"
        assert edge.source_port_id is None

    @staticmethod
    def test_init_with_source_port():
        """Test initialization with source port."""
        edge = Edge(
            source_node_id="node_1",
            target_node_id="node_2",
            source_port_id="output_1"
        )
        
        assert edge.source_port_id == "output_1"


class TestWorkflow:
    """Test Workflow dataclass."""

    @staticmethod
    def test_init_success():
        """Test successful initialization."""
        workflow = Workflow()
        
        assert hasattr(workflow, '__dataclass_fields__')
