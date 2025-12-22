# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any


class NodeType(Enum):
    Start = ("Start", "1")
    End = ("End", "2")
    LLM = ("LLM", "3")
    IntentDetection = ("IntentDetection", "6")
    Questioner = ("Questioner", "7")
    Code = ("Code", "10")
    Plugin = ("Plugin", "19")
    Output = ("Output", "9")
    Branch = ("Branch", "4")

    def __init__(self, dl_type, dsl_type):
        self.dl_type = dl_type
        self.dsl_type = dsl_type


class SourceType(Enum):
    ref: str = "ref"
    constant: str = "constant"


@dataclass
class Position:
    x: float
    y: float


@dataclass
class InputVariable:
    type: str
    content: str
    extra: Dict[str, str]
    schema: Optional[Dict[str, str]] = None


@dataclass
class InputsField:
    inputParameters: Dict[str, InputVariable] = field(default_factory=dict)
    llmParam: Optional[Dict[str, dict]] = None
    systemPrompt: Optional[Dict[str, str]] = None
    intents: Optional[List[Dict[str, str]]] = None
    language: Optional[str] = None
    code: Optional[str] = None
    pluginParam: Optional[Dict[str, str]] = None
    content: Optional[Dict[str, str]] = None


@dataclass
class OutputsField:
    type: str = "object"
    properties: Optional[Dict[str, "OutputsField"]] = None
    required: Optional[list] = None
    description: Optional[str] = None
    default: Optional[str] = None
    extra: Optional[Dict[str, int]] = None

    def add_property(self, variable_names: List[str], desc: str, index: int, var_type: Optional[str] = None):
        if not variable_names:
            return

        key = variable_names[0]
        is_leaf = len(variable_names) == 1
        if key not in self.properties:
            if is_leaf:
                self.properties[key] = OutputsField(
                    type=var_type or "string",
                    description=desc,
                    extra={"index": index}
                )
            else:
                self.properties[key] = OutputsField(type="object", properties={}, required=[])

        output_field = self.properties[key]
        output_field.add_property(variable_names[1:], desc, index, var_type)


@dataclass
class DataConfig:
    title: str = ""
    inputs: Optional[InputsField] = None
    outputs: Optional[OutputsField] = None
    branches: Optional[List[Dict[str, Any]]] = None
    exceptionConfig: Optional[Dict[str, Any]] = None


@dataclass
class Node:
    id: str
    type: str
    meta: Dict[str, Any] = field(default_factory=dict)
    data: DataConfig = field(default_factory=DataConfig)


@dataclass
class Edge:
    sourceNodeID: str
    targetNodeID: str
    sourcePortID: Optional[str] = None


@dataclass
class Workflow:
    nodes: List[Node] = field(default_factory=list)
    edges: List[Edge] = field(default_factory=list)