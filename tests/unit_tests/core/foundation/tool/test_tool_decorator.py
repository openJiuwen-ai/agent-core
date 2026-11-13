#!/usr/bin/python3.11
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved

import inspect
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


class TestLocalFunctionPublicIdentity:
    """issue #2495: @tool 装饰对象应暴露公开的工具名与直接调用入口"""

    def test_name_reflects_card_name(self):
        @tool
        def greet(name: str) -> str:
            """Greet someone by name."""
            return f"hello, {name}"

        assert greet.name == "greet"
        # 与 ToolCard 保持一致（ToolCard.name 才是注册用的名字）
        assert greet.name == greet.card.name

    def test_name_reflects_explicit_override(self):
        @tool(name="greeter")
        def greet(name: str) -> str:
            """Greet someone by name."""
            return f"hello, {name}"

        assert greet.name == "greeter"

    def test_func_is_the_decorated_function(self):
        @tool
        def greet(name: str) -> str:
            """Greet someone by name."""
            return f"hello, {name}"

        assert greet.func is not None
        assert greet.func("world") == "hello, world"
        # 与被装饰的原始函数同名，而不是内部包装器
        assert greet.func.__name__ == "greet"

    def test_func_bypasses_var_positional_wrapper(self):
        """声明了 *args 时 _func 是内部包装器，func 必须仍是原函数"""

        @tool
        def total(base: int, *rest: int) -> int:
            """Sum a base with the remaining numbers."""
            return base + sum(rest)

        # 原函数签名保持不变，可以直接按 Python 语义调用
        assert str(__import__("inspect").signature(total.func)) == "(base: int, *rest: int) -> int"
        assert total.func(1, 2, 3) == 6

    @pytest.mark.asyncio
    async def test_invoke_path_is_unchanged(self):
        """新增 name / func 只是读属性，invoke 的 schema 校验与回调不受影响"""

        @tool
        def add(a: int, b: int) -> int:
            """Add two integers."""
            return a + b

        assert await add.invoke({"a": 2, "b": 3}) == 5
        # 非法入参仍应被 schema 拦下，不会绕过去
        with pytest.raises(Exception):
            await add.invoke({"a": "not-an-int", "b": 3})

    def test_name_does_not_live_on_tool_base(self):
        """name 只挂在 LocalFunction 上，不能做成 Tool 基类的 property。

        ``MockTool``（`tests/unit_tests/core/session/tracer/test_decorator.py:87`）
        这类子类一直在构造里 ``self.name = ...``；若基类提供只读 property，
        这一行会抛 ``AttributeError: property 'name' has no setter``。加 setter
        同样不行 —— 那会写穿到 ``card.name``，而 tracer 用 ``tool.card.name``
        作为上报的 ``class_name``（见 ``test_decorate_tool`` 的断言），
        name 会被一并改掉。
        """
        from openjiuwen.core.foundation.tool.base import Tool
        from openjiuwen.core.foundation.tool.function.function import LocalFunction

        assert inspect.getattr_static(Tool, "name", None) is None
        assert isinstance(inspect.getattr_static(LocalFunction, "name"), property)

    def test_a_subclass_may_still_assign_its_own_name(self):
        """回归：MockTool 式的 ``self.name = ...`` 必须继续可用"""
        from openjiuwen.core.foundation.tool.base import Tool
        from openjiuwen.core.foundation.tool.schema import ToolInfo

        class _NamedTool(Tool):
            def __init__(self, card):
                super().__init__(card)
                self.name = "mock tool"

            async def invoke(self, inputs, **kwargs):
                return {}

            async def stream(self, inputs, **kwargs):
                pass

            def get_tool_info(self) -> ToolInfo:
                pass

        t = _NamedTool(ToolCard(name="test_tool", description="test tool"))
        assert t.name == "mock tool"
        # card 不被写穿
        assert t.card.name == "test_tool"

    def test_name_follows_a_card_rename(self):
        """name 是 card.name 的实时视图，不是构造时拍下的快照"""

        @tool
        def greet(name: str) -> str:
            """Greet someone by name."""
            return f"hello, {name}"

        greet.card.name = "greeter"
        assert greet.name == "greeter"

    def test_name_is_read_only_on_local_function(self):
        """LocalFunction 上的 name 只读：改名必须走 card，否则两处会不一致"""

        @tool
        def greet(name: str) -> str:
            """Greet someone by name."""
            return f"hello, {name}"

        with pytest.raises(AttributeError):
            greet.name = "other"

    def test_the_other_tool_implementations_are_unaffected(self):
        """MCPTool / RestfulApi 不是 LocalFunction，本次改动不碰它们。

        issue #2495 报的是 ``@tool`` 装饰的对象，所以 name 只落在
        LocalFunction 上。其他工具实现维持原状 —— 这正是把属性放在
        LocalFunction 而不是 Tool 基类上的另一个理由：基类上多一个
        只读属性，对这些类的所有子类都是潜在的行为变化。
        """
        from openjiuwen.core.foundation.tool.function.function import LocalFunction
        from openjiuwen.core.foundation.tool.mcp.base import MCPTool
        from openjiuwen.core.foundation.tool.service_api.restful_api import RestfulApi

        for cls in (MCPTool, RestfulApi):
            assert not issubclass(cls, LocalFunction)
            # 没有从本次改动继承到任何 name 属性
            assert inspect.getattr_static(cls, "name", None) is None

    def test_subclass_of_local_function_keeps_name(self):
        """LocalFunction 自己的子类（如 symphony 的 _DirectoryFunction）继承 name"""
        from openjiuwen.core.foundation.tool.function.function import LocalFunction

        class _MyFunc(LocalFunction):
            pass

        def body(x: int) -> int:
            """Double it."""
            return x * 2

        f = _MyFunc(card=ToolCard(name="doubler", description="d"), func=body)
        assert isinstance(f, LocalFunction)
        assert f.name == "doubler"
        assert f.func is body
