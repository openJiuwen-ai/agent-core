# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for openjiuwen.extensions.app.tools.finance_tools."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.extensions.app.tools import finance_tools


def _config_get(values):
    return lambda key, default=None: values.get(key, default)


_SAMPLE_RESPONSE = {
    "summary": {
        "title": "Apple Inc",
        "stock": "AAPL",
        "exchange": "NASDAQ",
        "price": "$150.23",
        "currency": "USD",
        "price_movement": {"percentage": 1.58, "value": 2.34, "movement": "Up"},
        "date": "Aug 17 2026, 09:30 AM UTC-05:00",
    },
    "graph": [
        {"price": 150.1, "date": "Aug 17 2026, 09:30 AM UTC-05:00", "volume": 1000},
        {"price": 151.2, "date": "Aug 17 2026, 10:00 AM UTC-05:00", "volume": 1200},
        {"price": 150.23, "date": "Aug 17 2026, 10:30 AM UTC-05:00", "volume": 900},
    ],
    "knowledge_graph": {"about": {"snippets": ["Apple Inc. designs, manufactures, and markets smartphones."]}},
}


class TestSearchFinance:
    @pytest.mark.asyncio
    async def test_returns_error_when_api_key_not_configured(self):
        with patch.object(finance_tools.config, "get", side_effect=_config_get({})):
            result = await finance_tools.search_finance.invoke({"query": "Apple"})
        assert "SERPAPI_API_KEY" in result["error"]

    @pytest.mark.asyncio
    async def test_returns_finance_data_on_success(self):
        body = json.dumps(_SAMPLE_RESPONSE).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(finance_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(finance_tools._http, "request", mock_request),
        ):
            result = await finance_tools.search_finance.invoke({"query": "Apple"})
        assert result["title"] == "Apple Inc"
        assert result["stock"] == "AAPL"
        assert result["exchange"] == "NASDAQ"
        assert result["price"] == "$150.23"
        assert result["currency"] == "USD"
        assert result["change_text"] == "+2.34 (+1.58%) today"
        assert result["movement"] == "Up"
        assert result["as_of"] == "Aug 17 2026, 09:30 AM UTC-05:00"
        assert result["window"] == "1D"
        assert result["description"] == "Apple Inc. designs, manufactures, and markets smartphones."
        assert result["link"] == "https://www.google.com/finance/quote/AAPL:NASDAQ"
        assert result["chart_values"] == [150.1, 151.2, 150.23]
        assert len(result["chart_x_axis"]) == 3

    @pytest.mark.asyncio
    async def test_chart_points_use_real_graph_prices(self):
        body = json.dumps(_SAMPLE_RESPONSE).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(finance_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(finance_tools._http, "request", mock_request),
        ):
            result = await finance_tools.search_finance.invoke({"query": "Apple"})
        assert result["chart_values"] == [150.1, 151.2, 150.23]
        # window defaults to "1D" (intraday) -- labels are times, not dates
        assert result["chart_x_axis"] == ["09:30 AM", "10:00 AM", "10:30 AM"]

    @pytest.mark.asyncio
    async def test_chart_points_downsample_to_max_points(self):
        response = json.loads(json.dumps(_SAMPLE_RESPONSE))
        response["graph"] = [
            {"price": 100 + i * 0.1, "date": f"Aug 17 2026, {9 + i // 60:02d}:{i % 60:02d} AM UTC-05:00"}
            for i in range(500)
        ]
        body = json.dumps(response).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(finance_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(finance_tools._http, "request", mock_request),
        ):
            result = await finance_tools.search_finance.invoke({"query": "Apple"})
        assert len(result["chart_values"]) == finance_tools.MAX_CHART_POINTS
        # the very last real price point is always kept, even after sampling
        assert result["chart_values"][-1] == response["graph"][-1]["price"]

    @pytest.mark.asyncio
    async def test_chart_points_are_none_when_no_graph_points(self):
        response = json.loads(json.dumps(_SAMPLE_RESPONSE))
        response["graph"] = []
        body = json.dumps(response).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(finance_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(finance_tools._http, "request", mock_request),
        ):
            result = await finance_tools.search_finance.invoke({"query": "Apple"})
        assert result["chart_x_axis"] is None
        assert result["chart_values"] is None

    @pytest.mark.asyncio
    async def test_returns_error_when_no_summary_title(self):
        body = json.dumps({"summary": {}}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(finance_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(finance_tools._http, "request", mock_request),
        ):
            result = await finance_tools.search_finance.invoke({"query": "Nonexistent Corp"})
        assert "error" in result
        assert "title" not in result

    @pytest.mark.asyncio
    async def test_returns_error_on_serpapi_error_status(self):
        body = json.dumps({"search_metadata": {"status": "Error"}, "error": "Unknown ticker"}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(finance_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(finance_tools._http, "request", mock_request),
        ):
            result = await finance_tools.search_finance.invoke({"query": "ZZZZ"})
        assert "Unknown ticker" in result["error"]

    @pytest.mark.asyncio
    async def test_http_error_returns_error_field(self):
        mock_request = AsyncMock(return_value=(403, {}, b"", "url", False))
        with (
            patch.object(finance_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(finance_tools._http, "request", mock_request),
        ):
            result = await finance_tools.search_finance.invoke({"query": "Apple"})
        assert "403" in result["error"]

    @pytest.mark.asyncio
    async def test_invalid_window_falls_back_to_1d(self):
        body = json.dumps(_SAMPLE_RESPONSE).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(finance_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(finance_tools._http, "request", mock_request),
        ):
            result = await finance_tools.search_finance.invoke({"query": "Apple", "window": "3W"})
        assert result["window"] == "1D"

    @pytest.mark.asyncio
    async def test_sends_expected_query_params(self):
        body = json.dumps(_SAMPLE_RESPONSE).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(finance_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(finance_tools._http, "request", mock_request),
        ):
            await finance_tools.search_finance.invoke({"query": "Apple", "window": "1M"})
        _session, method, url = mock_request.await_args.args
        assert method == "GET"
        assert "engine=google_finance" in url
        assert "q=Apple" in url
        assert "window=1M" in url
        assert "api_key=test-key" in url


class TestShowFinanceResults:
    @pytest.mark.asyncio
    async def test_returns_no_genui_for_empty_items(self):
        result = await finance_tools.show_finance_results.invoke({"title": "Markets", "items": []})
        assert result["genui"] == []

    @pytest.mark.asyncio
    async def test_renders_finance_gallery_with_summary(self):
        result = await finance_tools.show_finance_results.invoke(
            {
                "title": "Markets",
                "items": [
                    {
                        "title": "Apple Inc",
                        "stock": "AAPL",
                        "exchange": "NASDAQ",
                        "price": "$150.23",
                        "change_text": "+2.34 (+1.58%) today",
                        "movement": "Up",
                        "as_of": "Aug 17 2026, 09:30 AM UTC-05:00",
                        "window": "1D",
                        "description": "Apple Inc. designs, manufactures, and markets smartphones.",
                        "chart_x_axis": ["Aug 17", "Aug 17", "Aug 17"],
                        "chart_values": [150.1, 151.2, 150.23],
                        "link": "https://www.google.com/finance/quote/AAPL:NASDAQ",
                    }
                ],
            }
        )
        assert "Apple Inc" in result["text"]
        assert "$150.23" in result["text"]
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert components["finance0Name"]["text"] == "Apple Inc (AAPL)"
        assert components["finance0Price"]["text"] == "$150.23"
        assert "+2.34" in components["finance0Change"]["text"]
        assert components["finance0Change"]["styles"]["color"] == "#16A34A"
        chart = components["finance0Chart"]
        assert chart["component"] == "Chart"
        assert chart["chartType"] == "line"
        assert chart["data"]["xAxis"] == ["Aug 17", "Aug 17", "Aug 17"]
        assert chart["data"]["series"] == [
            {"name": "Apple Inc", "data": [{"value": 150.1}, {"value": 151.2}, {"value": 150.23}]}
        ]
        assert chart["styles"]["chartConfig"]["colors"] == ["#16A34A"]  # Up == green
        assert components["finance0Button"]["action"]["functionCall"]["args"]["url"] == (
            "https://www.google.com/finance/quote/AAPL:NASDAQ"
        )

    @pytest.mark.asyncio
    async def test_chart_is_red_for_down_movement(self):
        result = await finance_tools.show_finance_results.invoke(
            {
                "title": "Markets",
                "items": [
                    {
                        "title": "Apple Inc",
                        "movement": "Down",
                        "chart_x_axis": ["Aug 17"],
                        "chart_values": [150.1],
                    }
                ],
            }
        )
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert components["finance0Chart"]["styles"]["chartConfig"]["colors"] == ["#DC2626"]

    @pytest.mark.asyncio
    async def test_omits_optional_fields_when_absent(self):
        result = await finance_tools.show_finance_results.invoke(
            {"title": "Markets", "items": [{"title": "Mystery Corp"}]}
        )
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert components["finance0Name"]["text"] == "Mystery Corp"
        assert "finance0Price" not in components
        assert "finance0Change" not in components
        assert "finance0Chart" not in components
        assert "finance0Button" not in components

    @pytest.mark.asyncio
    async def test_omits_chart_when_only_one_of_the_paired_fields_present(self):
        result = await finance_tools.show_finance_results.invoke(
            {"title": "Markets", "items": [{"title": "Mystery Corp", "chart_x_axis": ["Aug 17"]}]}
        )
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert "finance0Chart" not in components
