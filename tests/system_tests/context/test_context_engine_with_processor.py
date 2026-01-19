#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import os
import pytest

from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.context_engine.token.tiktoken_counter import TiktokenCounter
from openjiuwen.core.foundation.llm import (
    UserMessage, AssistantMessage, ToolMessage, ToolCall,
    ModelRequestConfig, ModelClientConfig, Model
)
from openjiuwen.core.foundation.llm.inference_affinity_model import InferenceAffinityModel
from openjiuwen.core.context_engine.processor.offloader.message_offloader import (
    MessageOffloaderConfig
)
from openjiuwen.core.context_engine.processor.offloader.message_summary_offloader import (
    MessageSummaryOffloaderConfig
)
from openjiuwen.core.context_engine.processor.compressor.dialogue_compressor import (
    DialogueCompressorConfig
)
from openjiuwen.core.context_engine.processor.compressor.current_round_compressor import (
    CurrentRoundCompressorConfig
)


API_BASE = os.getenv("API_BASE", "")
API_KEY = os.getenv("API_KEY", "")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")


TEST_MESSAGES = [
    UserMessage(content="你好，你是谁"),
    AssistantMessage(content="你好，我是小助手，很高兴为你服务！"),

    UserMessage(content="今天天气怎么样？"),
    AssistantMessage(content="我目前无法实时获取天气，但你可以告诉我所在城市，我可以帮你查历史数据或预报模式。"),

    UserMessage(content="请帮我写一段快速排序的Python代码"),
    AssistantMessage(
        content="```python\ndef quicksort(arr):\n    if len(arr) <= 1:\n        return arr\n    "
                "pivot = arr[len(arr)//2]\n    left = [x for x in arr if x < pivot]\n    "
                "mid = [x for x in arr if x == pivot]\n    right = [x for x in arr if x > pivot]\n    "
                "return quicksort(left) + mid + quicksort(right)\nprint(quicksort([3,6,2,7,1]))\n```"),

    UserMessage(content="明天去上海出差，帮我查天气、把300欧元换成人民币、订一晚最便宜的酒店，还需要发票，谢谢！"),
    AssistantMessage(
        content="检测到工具调用",
        tool_calls=[
            ToolCall(id="call_w_01", type="function", name="get_weather",
                     arguments='{"city": "上海", "date": "tomorrow"}'),
            ToolCall(id="call_fx_01", type="function", name="fx_convert",
                     arguments='{"amount": 300, "from": "EUR", "to": "CNY"}'),
            ToolCall(id="call_hotel_01", type="function", name="search_hotel",
                     arguments='{"city": "上海", "check_in": "tomorrow", "sort": "price", "invoice": true}')
        ]
    ),
    ToolMessage(tool_call_id="call_w_01",
                content='{"city": "上海", "date": "2026-01-08", "temp": 8, "unit": "°C", "condition": "小雨", '
                        '"humidity": 85, "wind": 15, "forecast": "阴有小雨，早晚温差大", "hourly": '
                        '[{"time": "09:00", "temp": 6, "condition": "小雨"}, {"time": "18:00", "temp": 9, '
                        '"condition": "小雨"}], "air_quality": "轻度污染", "uv_index": 1, "suggestion": '
                        '"携带雨具，穿戴防风外套"}'),
    ToolMessage(tool_call_id="call_fx_01",
                content='{"amount": 300, "from": "EUR", "to": "CNY", "rate": 7.85, "result": 2355, '
                        '"currency": "CNY", "fee": 0, "location": "浦东机场T2实时牌价", "timestamp": '
                        '"2026-01-07T15:00:00+08:00", "daily_limit": 5000, "promotion": '
                        '"免手续费+赠地铁一日券二维码（5% off）", "note": "需出示护照，营业 06:00-23:00"}'),
    ToolMessage(tool_call_id="call_hotel_01",
                content='{"city": "上海", "check_in": "2026-01-08", "hotel": "如家精选南京路店", '
                        '"price": 198, "currency": "CNY", "room": "标准大床房", "breakfast": false, "wifi": '
                        '"免费", "cancellation": "当日18:00前免费", "invoice": "提供增值税普通发票", "address": '
                        '"南京东路300号", "distance": "距外滩步行5分钟", "booking_id": "bk_67890", '
                        '"return_discount": "连住2晚9折", "change_policy": "可免费改期一次", "refund": "18:00前全额退款"}'),
    AssistantMessage(content="明天上海小雨8℃，记得带伞穿防风外套；300欧元换2355元人民币零手续费，赠地铁券；"
                             "如家精选南京路店198元/晚含发票，距外滩步行5分钟，18:00前可免费取消。祝出差顺利！"),

    UserMessage(content="后天去深圳出差，帮我查天气、换300 USD、订便宜酒店，谢谢！"),
    AssistantMessage(
        content="正在查询天气与汇率",
        tool_calls=[
            ToolCall(id="w_0", type="function", name="get_weather", arguments='{"city": "深圳", "date": "2026-01-09"}'),
            ToolCall(id="fx_0", type="function", name="fx_convert",
                     arguments='{"amount": 300, "from": "USD", "to": "CNY"}')
        ]
    ),
    ToolMessage(tool_call_id="w_0",
                content='{"city": "深圳", "date": "2026-01-09", "temp": 20, "condition": "多云", "humidity": 70}'),
    ToolMessage(tool_call_id="fx_0",
                content='{"amount": 300, "rate": 7.2, "result": 2160, "currency": "CNY", "fee": 0}'),
    AssistantMessage(
        content="继续预订酒店",
        tool_calls=[
            ToolCall(id="hotel_0", type="function", name="search_hotel",
                     arguments='{"city": "深圳", "check_in": "2026-01-09", "sort": "price", "invoice": true}')
        ]
    ),
    ToolMessage(tool_call_id="hotel_0", content='{"hotel": "如家罗湖口岸店", "price": 188, "currency": '
                                                '"CNY", "invoice": "提供"}'),
    AssistantMessage(content="后天深圳多云20℃，300美元换2160元人民币免手续费；如家罗湖口岸店188元/晚含发票。出差顺利！"),

    UserMessage(content="今晚想去看《流浪地球3》，帮我查北京影城排片、订两张最便宜的电影票，再来一杯大爆米花套餐，谢谢！"),
    AssistantMessage(
        content="检测到工具调用",
        tool_calls=[
            ToolCall(id="call_cinema_02", type="function", name="list_movies",
                     arguments='{"city": "北京", "date": "today", "movie": "流浪地球3"}'),
            ToolCall(id="call_ticket_02", type="function", name="book_ticket",
                     arguments='{"city": "北京", "movie": "流浪地球3", "date": "today", "quantity": 2, "sort": "price"}'),
            ToolCall(id="call_snack_02", type="function", name="buy_snack",
                     arguments='{"cinema": "北京万达影城CBD店", "item": "大爆米花套餐", "quantity": 1}')
        ]
    ),
    ToolMessage(tool_call_id="call_cinema_02",
                content='{"city": "北京", "date": "2026-01-07", "movie": "流浪地球3", "cinema": "北京万达影城CBD店", '
                        '"language": "国语", "hall": "IMAX 2号厅", "showtimes": [{"time": "19:30", "price": 45}, '
                        '{"time": "21:00", "price": 40}, {"time": "22:30", "price": 38}], "duration": '
                        '"125min", "rating": "9.2/10"}'),
    ToolMessage(tool_call_id="call_ticket_02",
                content='{"cinema": "北京万达影城CBD店", "movie": "流浪地球3", "showtime": "22:30", "quantity": 2, '
                        '"price": 76, "currency": "CNY", "seats": ["I-12", "I-13"], "hall": "IMAX 2号厅", '
                        '"language": "国语", "ticket_id": "tk_112233", "qr_code": "https://qr.cinema.com/tk_112233", '
                        '"refund": "开场前2h可退", "change": "开场前1h可改"}'),
    ToolMessage(tool_call_id="call_snack_02",
                content='{"cinema": "北京万达影城CBD店", "item": "大爆米花套餐", "quantity": 1, "price": 25, "currency": '
                        '"CNY", "content": "大爆米花1桶+中可乐1杯", "pickup": "柜台扫码领取", "snack_id": "sn_445566", '
                        '"validity": "当日场次有效", "refund": "未领取可退"}'),
    AssistantMessage(content="今晚北京万达CBD店《流浪地球3》IMAX 22:30场次38元/张，已订2张I-12/13座位；大爆米花套餐25元柜台扫码领取；"
                             "开场前2小时可退票，1小时可改签。观影愉快！")
]


@pytest.mark.skip()
class TestContextProcessors:
    @staticmethod
    def context_engine():
        return ContextEngine(
            config=ContextEngineConfig(enable_kv_cache_release=False)
        )

    @staticmethod
    def print_messages(messages):
        token_counter = TiktokenCounter()
        original_count = token_counter.count_messages(TEST_MESSAGES)
        for i, message in enumerate(messages):
            role = message.role
            content = message.content.replace('\n', '\\n')
            logger.info(f"[{i}] {role}: {content}")
        processed_count = token_counter.count_messages(messages)
        logger.info(f"[SUMMARY] before {original_count}, after: {processed_count}")

    @pytest.mark.asyncio
    async def test_message_offloader(self):
        context = await self.context_engine().create_context(
            "test_context_id",
            processors=[
                (
                    "MessageOffloader",
                    MessageOffloaderConfig(
                        messages_threshold=10,
                        tokens_threshold=1000,
                        large_message_threshold=100,
                        trim_size=50,
                        offload_message_type=["user", "assistant", "tool"]
                    )
                ),
            ],
            token_counter=TiktokenCounter()
        )

        await context.add_messages(TEST_MESSAGES)

        processed_messages = context.get_messages()
        self.print_messages(processed_messages)

    @pytest.mark.asyncio
    async def test_message_summary_offloader(self):
        context = await self.context_engine().create_context(
            "test_context_id",
            processors=[
                (
                    "MessageSummaryOffloader",
                    MessageSummaryOffloaderConfig(
                        messages_threshold=10,
                        tokens_threshold=1000,
                        large_message_threshold=100,
                        offload_message_type=["user", "assistant", "tool"],
                        model=ModelRequestConfig(
                            model=MODEL_NAME
                        ),
                        model_client=ModelClientConfig(
                            client_id="123",
                            client_provider=MODEL_PROVIDER,
                            api_base=API_BASE,
                            api_key=API_KEY,
                            verify_ssl=False
                        )
                    )
                ),
            ],
            token_counter=TiktokenCounter()
        )

        await context.add_messages(TEST_MESSAGES)

        processed_messages = context.get_messages()
        self.print_messages(processed_messages)

    @pytest.mark.asyncio
    async def test_dialogue_compressor(self):
        model_config = ModelRequestConfig(
            model=MODEL_NAME
        )
        model_client_config = ModelClientConfig(
            client_id="123",
            client_provider=MODEL_PROVIDER,
            api_base=API_BASE,
            api_key=API_KEY,
            verify_ssl=False
        )
        model = InferenceAffinityModel(model_client_config, model_config)
        context = await self.context_engine().create_context(
            "test_context_id",
            processors=[
                (
                    "DialogueCompressor",
                    DialogueCompressorConfig(
                        messages_threshold=10,
                        tokens_threshold=1000,
                        model=ModelRequestConfig(
                            model=MODEL_NAME
                        ),
                        model_client=ModelClientConfig(
                            client_id="123",
                            client_provider=MODEL_PROVIDER,
                            api_base=API_BASE,
                            api_key=API_KEY,
                            verify_ssl=False
                        )
                    )
                ),
            ],
            token_counter=TiktokenCounter()
        )
        context.set_messages(TEST_MESSAGES)
        await context.get_context_window(model=model)
        await context.add_messages(UserMessage(content="你是谁"))
        await context.get_context_window(model=model)

        processed_messages = context.get_messages()
        self.print_messages(processed_messages)

    @pytest.mark.asyncio
    async def test_current_round_compressor(self):
        context = await self.context_engine().create_context(
            "test_context_id",
            processors=[
                (
                    "CurrentRoundCompressor",
                    CurrentRoundCompressorConfig(
                        messages_threshold=10,
                        tokens_threshold=1000,
                        single_multi_compression=True,
                        large_message_threshold=10,
                        model=ModelRequestConfig(
                            model=MODEL_NAME
                        ),
                        model_client=ModelClientConfig(
                            client_id="123",
                            client_provider=MODEL_PROVIDER,
                            api_base=API_BASE,
                            api_key=API_KEY,
                            verify_ssl=False
                        )
                    )
                ),
            ],
            token_counter=TiktokenCounter()
        )

        await context.add_messages(TEST_MESSAGES)

        processed_messages = context.get_messages()
        self.print_messages(processed_messages)