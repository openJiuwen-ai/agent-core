# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.


"""Data frame data model definitions.

This module defines data models related to data frames (``DataFrame``),
which are used to transfer heterogeneous data types inside the controller.

Supported data frame types:
- TextDataFrame: text data.
- FileDataFrame: file data (supports both bytes and URI).
- JsonDataFrame: JSON data.

``DataFrame`` is the basic unit for data exchange in the controller, used in
events, task inputs/outputs and other scenarios.
"""
from typing import Literal, Optional, Dict, Any, Union

from pydantic import BaseModel


class BaseDataFrame(BaseModel):
    """Base class for all data frames.

    Defines the common structure for data frames, which can be text, file or
    JSON. All concrete data frame types inherit from this base class.

    Attributes:
        type: Data frame type, must be one of ``"text"``, ``"file"`` or
            ``"json"``.
    """
    type: Literal["text", "file", "json"]


class TextDataFrame(BaseDataFrame):
    """Text data frame.

    Used to transport plain text content (e.g. user input, task descriptions).

    Attributes:
        type: Data frame type, fixed to ``"text"``.
        text: Text content.
    """
    type: Literal["text", "file", "json"] = "text"
    text: str


class FileDataFrame(BaseDataFrame):
    """File data frame.

    Used to transport file contents and metadata, supporting either inline
    bytes or an external URI.

    Attributes:
        type: Data frame type, fixed to ``"file"``.
        name: File name.
        mimeType: MIME type such as ``"image/png"`` or ``"application/pdf"``.
        bytes: Raw file bytes (optional, mutually exclusive with ``uri``).
        uri: File URI (optional, mutually exclusive with ``bytes``).
    """
    type: Literal["text", "file", "json"] = "file"
    name: str
    mimeType: str
    bytes: Optional[bytes] = None
    uri: Optional[str] = None


class JsonDataFrame(BaseDataFrame):
    """JSON data frame.

    Used to transport structured JSON data (e.g. configuration, API
    responses).

    Attributes:
        type: Data frame type, fixed to ``"json"``.
        data: JSON payload as a dictionary.
    """
    type: Literal["text", "file", "json"] = "json"
    data: Dict[str, Any]


DataFrame = Union[TextDataFrame, FileDataFrame, JsonDataFrame]




