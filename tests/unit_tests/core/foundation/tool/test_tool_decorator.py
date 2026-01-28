#!/usr/bin/python3.11
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved

from typing import Annotated, List

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


@pytest.mark.asyncio
class TestToolDecorator:
    def assertEqual(self, left, right):
        assert left == right

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
