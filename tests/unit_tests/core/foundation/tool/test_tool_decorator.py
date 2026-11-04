#!/usr/bin/python3.11
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved

from typing import Annotated, List, Literal, Optional, Tuple
import pytest
from pydantic import Field, BaseModel

from openjiuwen.core.foundation.tool.schema import ToolInfo
from openjiuwen.core.foundation.tool.tool import tool, ToolCard


@tool(
    card=ToolCard(
        name="local_sub",
        description="local function for sub",
        input_params={
            "type": "object",
            "properties": {
                "a": {
                    "description": "first arg",
                    "type": "integer",
                },
                "b": {
                    "description": "second arg",
                    "type": "integer",
                },
            },
            "required": ["a", "b"],
        },
    )
)
def sub(a: int, b: int):
    return a - b


class Note(BaseModel):
    key: str
    value: int


class ProductInfo(BaseModel):
    name: Annotated[str, Field(description="商品名称")]
    sales: Annotated[int, Field(description="销量", default=0)]
    price: Annotated[float, Field(description="价格必须大于0", default=1.0)]
    is_season: Annotated[bool, Field(description="是否当季")]
    color: Annotated[List[str], Field(description="颜色")]
    note: Annotated[Note, Field(description="备注")]


@tool
def summarize(
        title: Annotated[str, Field(description="汇总标题")],
        products: Annotated[List[ProductInfo], Field(description="商品列表")],
) -> float:
    """汇总商品信息"""
    total = sum(product["price"] * product["sales"] for product in products)
    return total


@tool
def read_write_tool(
        path: str,
        *,
        mode: Literal['text', 'bytes'] = "text",
        head: Optional[int] = None,
        tail: Optional[int] = None,
        line_range: Optional[Tuple[int, int]] = None,
        content: str | bytes,
) -> str:
    """Test function to verify tool parameter fixes"""
    """
    Test function to verify tool parameter fixes
    
    Args:
        path: Path to the file
        mode: Reading mode - "text" (line-based, default) or "bytes" (raw bytes).
        head: Number of lines to read from the start (text mode only)
        tail: Number of lines to read from the end (text mode only)
        line_range: Specific line range to read (start, end) - 1-indexed, inclusive (text mode only)
        content: Data to write to the file (string for text mode, bytes for binary mode).
    
    Returns:
        Verification message
    """
    return f"Verified: path={path}, mode={mode}, head={head}, tail={tail}, line_range={line_range}"


@tool
def tool_with_only_with_var_positional(*args):
    result = 0
    for item in args:
        result += item
    return result


@tool
def tool_with_var_positional(a: int, b: int, *args):
    result = a + b
    for item in args:
        result += item
    return result


@tool
def tool_with_middle_with_var_positional(a: int, b: int, *args, d: int):
    result = a + b
    for item in args:
        result += item
    return result + d


@tool
def tool_with_var_keywords(a: int, b: int, **kwargs):
    return {'a': a, 'b': b, **kwargs}


@tool
def tool_with_only_with_var_keywords(**kwargs):
    return {**kwargs}


@tool
def tool_with_mix_var(a: int, *args, b: int, **kwargs):
    result = a + b
    for item in args:
        result += item
    for key, val in kwargs.items():
        result += val
    return result


@pytest.mark.asyncio
class TestToolDecorator:
    def assertEqual(self, left, right):
        assert left == right

    @staticmethod
    def assert_in(member, container):
        assert member in container

    @staticmethod
    def assert_not_in(member, container):
        assert member not in container

    async def test_tool_with_var_positional(self):
        result = await tool_with_only_with_var_positional.invoke(inputs={'args': [1, 2, 3]})
        assert result == 6

        result = await tool_with_var_positional.invoke(inputs={'a': 1, 'b': 2, 'args': [1, 2, 3]})
        assert result == 9

        result = await tool_with_middle_with_var_positional.invoke(inputs={'a': 1, 'b': 2, 'args': [1, 2, 3], 'd': 4})
        assert result == 13

    async def test_tool_with_var_keywords(self):
        result = await tool_with_var_keywords.invoke(inputs={'a': 1, 'b': 2, 'c': 3})
        assert result == {'a': 1, 'b': 2, 'c': 3}

        result = await tool_with_only_with_var_keywords.invoke(inputs={'a': 1, 'b': 2, 'c': 3})
        assert result == {'a': 1, 'b': 2, 'c': 3}

    async def test_tool_with_mix_var(self):
        result = await tool_with_mix_var.invoke(inputs={'a': 1, 'args': [1, 2, 3], 'b': 2, 'c': 3, 'd': 4})
        assert result == 16

    async def test_tool(self):
        # invoke
        sub_result = await sub.invoke({"a": 5, "b": 1})
        self.assertEqual(sub.card.name, "local_sub")
        self.assertEqual(sub.card.description, "local function for sub")
        self.assertEqual(sub_result, 4)

        # get_tool_info
        sub_res = sub.card.tool_info()
        sub_too_info = ToolInfo(
            name="local_sub",
            description="local function for sub",
            parameters={
                "type": "object",
                "properties": {
                    "a": {"description": "first arg", "type": "integer"},
                    "b": {"description": "second arg", "type": "integer"},
                },
                "required": ["a", "b"],
            },
        )
        self.assertEqual(sub_res, sub_too_info)

    async def test_annotated(self):
        # invoke
        input = {
            "title": "水果信息汇总",
            "products": [
                {
                    "name": "苹果",
                    "sales": 2,
                    "price": 1.5,
                    "is_season": True,
                    "color": ["red", "yellow"],
                    "note": {"key": "备注", "value": 10},
                },
                {
                    "name": "香蕉",
                    "sales": 4,
                    "price": 1,
                    "is_season": False,
                    "color": ["yellow"],
                    "note": {"key": "备注", "value": 20},
                },
            ],
        }
        summarize_result = await summarize.invoke(input)
        self.assertEqual(summarize.card.name, "summarize")
        self.assertEqual(summarize.card.description, "汇总商品信息")
        self.assertEqual(summarize_result, 7.0)

        # get_tool_info
        summarize_res = summarize.card.tool_info()
        summarize_tool_info = ToolInfo(
            type="function",
            name="summarize",
            description="汇总商品信息",
            parameters={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "title"
                    },
                    "products": {
                        "type": "array",
                        "items": {
                            "properties": {
                                "name": {
                                    "description": "商品名称",
                                    "title": "Name",
                                    "type": "string"
                                },
                                "sales": {
                                    "default": 0,
                                    "description": "销量",
                                    "title": "Sales",
                                    "type": "integer"
                                },
                                "price": {
                                    "default": 1.0,
                                    "description": "价格必须大于0",
                                    "title": "Price",
                                    "type": "number"
                                },
                                "is_season": {
                                    "description": "是否当季",
                                    "title": "Is Season",
                                    "type": "boolean"
                                },
                                "color": {
                                    "description": "颜色",
                                    "items": {
                                        "type": "string"
                                    },
                                    "title": "Color",
                                    "type": "array"
                                },
                                "note": {
                                    "properties": {
                                        "key": {
                                            "title": "Key",
                                            "type": "string",
                                            "description": "key"
                                        },
                                        "value": {
                                            "title": "Value",
                                            "type": "integer",
                                            "description": "value"
                                        }
                                    },
                                    "required": [
                                        "key",
                                        "value"
                                    ],
                                    "title": "Note",
                                    "type": "object",
                                    "description": "note"
                                }
                            },
                            "required": [
                                "name",
                                "is_season",
                                "color",
                                "note"
                            ],
                            "title": "ProductInfo",
                            "type": "object"
                        },
                        "description": "products"
                    }
                },
                "additionalProperties": False,
                "title": "summarize",
                "required": [
                    "title",
                    "products"
                ]
            }

        )
        self.assertEqual(summarize_res, summarize_tool_info)

    async def test_literal_mode_param(self):
        """Test Literal mode parameter parsing."""
        # Verify tool info for mode parameter
        tool_info = read_write_tool.card.tool_info()
        parameters = tool_info.parameters
        properties = parameters.get("properties", {})

        # Verify mode parameter (Literal type)
        mode_prop = properties.get("mode", {})
        self.assertEqual(mode_prop.get("type"), "string")
        self.assertEqual(mode_prop.get("enum"), ["text", "bytes"])

    async def test_optional_params_without_defaults(self):
        """Test optional parameters without defaults."""
        # Test invoke with only required parameters
        result = await read_write_tool.invoke({"path": "test.txt", "content": "test content"})
        self.assertEqual(read_write_tool.card.name, "read_write_tool")
        self.assert_in("Verified: path=test.txt", result)

        # Verify required parameters
        tool_info = read_write_tool.card.tool_info()
        parameters = tool_info.parameters
        required = parameters.get("required", [])
        self.assert_in("path", required)
        self.assert_in("content", required)
        self.assert_not_in("mode", required)
        self.assert_not_in("head", required)
        self.assert_not_in("tail", required)
        self.assert_not_in("line_range", required)

    async def test_union_str_bytes_param(self):
        """Test str | bytes union type annotation."""
        # Test invoke with string content
        result = await read_write_tool.invoke({"path": "test.txt", "content": "test content"})
        self.assert_in("Verified: path=test.txt", result)

        # Test invoke with binary content
        result = await read_write_tool.invoke({
            "path": "test.bin",
            "mode": "bytes",
            "content": b"\x00\x01\x02\x03"
        })
        self.assert_in("Verified: path=test.bin", result)
        self.assert_in("mode=bytes", result)

        # Verify content parameter (union type)
        tool_info = read_write_tool.card.tool_info()
        parameters = tool_info.parameters
        properties = parameters.get("properties", {})
        content_prop = properties.get("content", {})
        self.assert_in("anyOf", content_prop)
        any_of = content_prop.get("anyOf", [])
        self.assertEqual(len(any_of), 2)
        self.assertEqual(any_of[0], {"type": "string"})
        self.assertEqual(any_of[1], {"type": "string", "format": "binary"})

    async def test_tuple_line_range_param(self):
        """Test Tuple line_range parameter parsing."""
        # Test invoke with line_range parameter
        result = await read_write_tool.invoke({
            "path": "test.txt",
            "line_range": [1, 10],
            "content": "test content"
        })
        self.assert_in("Verified: path=test.txt", result)
        self.assert_in("line_range=[1, 10]", result)

        # Verify line_range parameter (tuple type)
        tool_info = read_write_tool.card.tool_info()
        parameters = tool_info.parameters
        properties = parameters.get("properties", {})
        line_range_prop = properties.get("line_range", {})
        self.assertEqual(line_range_prop.get("type"), "array")
        items = line_range_prop.get("items", {})
        self.assert_in("anyOf", items)
        items_any_of = items.get("anyOf", [])
        self.assertEqual(len(items_any_of), 2)
        self.assertEqual(items_any_of[0], {"type": "integer"})
        self.assertEqual(items_any_of[1], {"type": "integer"})
