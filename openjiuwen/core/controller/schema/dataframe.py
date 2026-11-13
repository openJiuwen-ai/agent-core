# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
DataFrame Data Model Definitions

This module defines DataFrame-related data models for transmitting
different types of data in the controller.

Supported Data Types:
- TextDataFrame: Text data
- FileDataFrame: File data (supports both bytes and URI methods)
- JsonDataFrame: JSON format data

DataFrame is the basic unit for data transmission in the controller,
used for passing data in scenarios such as events, task input/output, etc.
"""

import builtins
from typing import Literal, Optional, Dict, Any, Union
from pydantic import BaseModel, model_validator


class BaseDataFrame(BaseModel):
    """Base DataFrame Class

    Defines the basic structure of a DataFrame, supporting three types: text, file, and JSON.
    All specific DataFrame types inherit from this class.

    Attributes:
        type: DataFrame type, must be one of "text", "file", or "json"
    """
    type: Literal["text", "file", "json"]


class TextDataFrame(BaseDataFrame):
    """Text DataFrame

    Used for transmitting text-type data.
    Suitable for transmitting plain text content, such as user input, task descriptions, etc.

    Attributes:
        type: DataFrame type, fixed as "text"
        text: Text content
    """
    type: Literal["text"] = "text"
    text: str


class FileDataFrame(BaseDataFrame):
    """File DataFrame

    Used for transmitting file-type data, supporting both bytes and URI methods.
    Suitable for transmitting file content, such as images, documents, etc.

    Attributes:
        type: DataFrame type, fixed as "file"
        name: File name
        mimeType: MIME type, such as "image/png", "application/pdf", etc.
        bytes: Byte data of file content (optional, mutually exclusive with uri)
        uri: File URI (optional, mutually exclusive with bytes)
    """
    type: Literal["file"] = "file"
    name: str
    mimeType: str
    # ``bytes`` as a bare annotation resolves to the field name itself in the
    # class body (it becomes NoneType), making the field unable to hold bytes.
    bytes: Optional[builtins.bytes] = None
    uri: Optional[str] = None

    @model_validator(mode="after")
    def _exactly_one_source(self):
        """bytes and uri are documented as mutually exclusive alternatives."""
        if (self.bytes is None) == (self.uri is None):
            raise ValueError("FileDataFrame requires exactly one of bytes or uri")
        return self


class JsonDataFrame(BaseDataFrame):
    """JSON DataFrame

    Used for transmitting JSON format data.
    Suitable for transmitting structured data, such as configuration information, API responses, etc.

    Attributes:
        type: DataFrame type, fixed as "json"
        data: JSON data dictionary
    """
    type: Literal["json"] = "json"
    data: Dict[str, Any]


DataFrame = Union[TextDataFrame, FileDataFrame, JsonDataFrame]
