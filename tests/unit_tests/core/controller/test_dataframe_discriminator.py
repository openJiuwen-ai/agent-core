# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""DataFrame union must honor its type discriminator and FileDataFrame source."""

import pytest
from pydantic import TypeAdapter, ValidationError

from openjiuwen.core.controller.schema.dataframe import (
    DataFrame,
    FileDataFrame,
    JsonDataFrame,
    TextDataFrame,
)

ADAPTER = TypeAdapter(DataFrame)


def test_wrong_type_field_is_rejected_instead_of_misclassified() -> None:
    with pytest.raises(ValidationError):
        ADAPTER.validate_python({"type": "file", "text": "x"})
    with pytest.raises(ValidationError):
        ADAPTER.validate_python({"type": "json", "text": "x"})
    with pytest.raises(ValidationError):
        ADAPTER.validate_python({"type": "text", "data": {"a": 1}})


def test_correct_frames_still_validate() -> None:
    assert isinstance(ADAPTER.validate_python({"type": "text", "text": "x"}), TextDataFrame)
    assert isinstance(
        ADAPTER.validate_python({"type": "json", "data": {"a": 1}}), JsonDataFrame
    )
    assert isinstance(
        ADAPTER.validate_python({"type": "file", "name": "a", "mimeType": "text/plain", "uri": "file:///a"}),
        FileDataFrame,
    )


def test_file_frame_requires_exactly_one_source() -> None:
    with pytest.raises(ValidationError, match="exactly one of bytes or uri"):
        FileDataFrame(name="a", mimeType="text/plain")
    with pytest.raises(ValidationError, match="exactly one of bytes or uri"):
        FileDataFrame(name="a", mimeType="text/plain", bytes=b"x", uri="file:///a")


def test_file_frame_can_actually_carry_bytes() -> None:
    frame = FileDataFrame(name="a", mimeType="application/octet-stream", bytes=b"payload")

    assert frame.bytes == b"payload"
    assert frame.uri is None
