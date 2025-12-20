#!/usr/bin/python3.11
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved

import unittest
from typing import Annotated, Any, List, Dict
from pydantic import Field, BaseModel

from openjiuwen.core.utils.tool.tool import tool
from openjiuwen.core.utils.tool.param import Param
from openjiuwen.core.utils.tool.schema import Parameters, ToolInfo


@tool(
    name="local_sub",
    description="local function for sub",
    params=[
        Param(name="a", description="first arg", param_type="int", required=True),
        Param(name="b", description="second arg", param_type="int", required=True),
    ],
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


class TestToolDecorator:
    def assertEqual(self, left, right):
        assert left == right

    def test_tool(self):
        # invoke
        sub_result = sub.invoke({"a": 5, "b": 1})
        self.assertEqual(sub.name, "local_sub")
        self.assertEqual(sub.description, "local function for sub")
        self.assertEqual(sub_result, 4)

        # get_tool_info
        sub_res = sub.get_tool_info()
        sub_too_info = ToolInfo(
            name="local_sub",
            description="local function for sub",
            parameters=Parameters(
                type="object",
                properties={
                    "a": {"description": "first arg", "type": "integer"},
                    "b": {"description": "second arg", "type": "integer"},
                },
                required=["a", "b"],
            )
        )
        self.assertEqual(sub_res, sub_too_info)

    def test_annotated(self):
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
                    "note": {"备注": "苹果不好卖"},
                },
                {
                    "name": "香蕉",
                    "sales": 4,
                    "price": 1,
                    "is_season": False,
                    "color": ["yellow"],
                    "note": {"备注": "香蕉好卖"},
                },
            ],
        }
        summarize_result = summarize.invoke(input)
        self.assertEqual(summarize.name, "summarize")
        self.assertEqual(summarize.description, "汇总商品信息")
        self.assertEqual(summarize_result, 7.0)

        # get_tool_info
        summarize_res = summarize.get_tool_info()
        summarize_tool_info = ToolInfo(
            name="summarize",
            description="汇总商品信息",
            parameters=Parameters(
                type="object",
                properties={
                    "title": {"description": "汇总标题", "type": "string"},
                    "products": {
                        "description": "商品列表",
                        "type": "array",
                        "items": {
                            "name": {"description": "商品名称", "type": "string"},
                            "required": ["name", "is_season", "color", "note"],
                            "sales": {"description": "销量", "type": "integer"},
                            "price": {"description": "价格必须大于0", "type": "number"},
                            "is_season": {"description": "是否当季", "type": "boolean"},
                            "color": {
                                "description": "颜色",
                                "type": "array",
                                "items": {"description": "颜色", "type": "string"},
                            },
                            "note": {
                                "description": "备注",
                                "type": "object",
                                "properties": {
                                    "key": {"description": "", "type": "string"},
                                    "required": ["key", "value"],
                                    "value": {"description": "", "type": "integer"},
                                },
                            },
                        },
                    },
                },
                required=["title", "products"],
            )
        )
        self.assertEqual(summarize_res, summarize_tool_info)
