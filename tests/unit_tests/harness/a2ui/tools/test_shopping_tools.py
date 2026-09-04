# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for openjiuwen.harness.a2ui.tools.shopping_tools."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.harness.a2ui.tools import shopping_tools


def _config_get(values):
    return lambda key, default=None: values.get(key, default)


_SAMPLE_RESULT = {
    "title": "Sony WH-1000XM5 Wireless Noise Cancelling Headphones",
    "asin": "B09XS7JWHH",
    "link": "https://www.amazon.com/dp/B09XS7JWHH?tag=serpapi-tracking",
    "link_clean": "https://www.amazon.com/dp/B09XS7JWHH",
    "thumbnail": "https://example.com/headphones.jpg",
    "rating": 4.7,
    "reviews": 12345,
    "price": "$328.00",
    "extracted_price": 328.0,
    "is_prime": True,
}


class TestSearchProducts:
    @pytest.mark.asyncio
    async def test_returns_error_when_api_key_not_configured(self):
        with patch.object(shopping_tools.config, "get", side_effect=_config_get({})):
            result = await shopping_tools.search_products.invoke({"query": "wireless headphones"})
        assert result["products"] == []
        assert "SERPAPI_API_KEY" in result["error"]

    @pytest.mark.asyncio
    async def test_returns_products_on_success(self):
        body = json.dumps({"organic_results": [_SAMPLE_RESULT]}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(shopping_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(shopping_tools._http, "request", mock_request),
        ):
            result = await shopping_tools.search_products.invoke({"query": "wireless headphones"})
        assert result["products"] == [
            {
                "title": "Sony WH-1000XM5 Wireless Noise Cancelling Headphones",
                "price": "$328.00",
                "rating": 4.7,
                "reviews": 12345,
                "image_url": "https://example.com/headphones.jpg",
                # link_clean preferred over the tracking-param link.
                "link": "https://www.amazon.com/dp/B09XS7JWHH",
                "is_prime": True,
            }
        ]

    @pytest.mark.asyncio
    async def test_falls_back_to_link_when_link_clean_missing(self):
        result_without_clean_link = {k: v for k, v in _SAMPLE_RESULT.items() if k != "link_clean"}
        body = json.dumps({"organic_results": [result_without_clean_link]}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(shopping_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(shopping_tools._http, "request", mock_request),
        ):
            result = await shopping_tools.search_products.invoke({"query": "wireless headphones"})
        assert result["products"][0]["link"] == "https://www.amazon.com/dp/B09XS7JWHH?tag=serpapi-tracking"

    @pytest.mark.asyncio
    async def test_caps_results_at_max_product_results(self):
        results = [{**_SAMPLE_RESULT, "title": f"Product {i}"} for i in range(15)]
        body = json.dumps({"organic_results": results}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(shopping_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(shopping_tools._http, "request", mock_request),
        ):
            result = await shopping_tools.search_products.invoke({"query": "wireless headphones"})
        assert len(result["products"]) == shopping_tools.MAX_PRODUCT_RESULTS

    @pytest.mark.asyncio
    async def test_skips_results_with_no_title(self):
        body = json.dumps({"organic_results": [{"price": "$1"}, _SAMPLE_RESULT]}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(shopping_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(shopping_tools._http, "request", mock_request),
        ):
            result = await shopping_tools.search_products.invoke({"query": "wireless headphones"})
        assert len(result["products"]) == 1
        assert result["products"][0]["title"] == "Sony WH-1000XM5 Wireless Noise Cancelling Headphones"

    @pytest.mark.asyncio
    async def test_returns_error_when_no_results(self):
        body = json.dumps({"organic_results": []}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(shopping_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(shopping_tools._http, "request", mock_request),
        ):
            result = await shopping_tools.search_products.invoke({"query": "something that doesn't exist at all"})
        assert result["products"] == []
        assert "error" in result

    @pytest.mark.asyncio
    async def test_returns_error_on_serpapi_error_status(self):
        body = json.dumps({"search_metadata": {"status": "Error"}, "error": "Invalid API key"}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(shopping_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(shopping_tools._http, "request", mock_request),
        ):
            result = await shopping_tools.search_products.invoke({"query": "wireless headphones"})
        assert result["products"] == []
        assert "Invalid API key" in result["error"]

    @pytest.mark.asyncio
    async def test_http_error_returns_error_field(self):
        mock_request = AsyncMock(return_value=(403, {}, b"", "url", False))
        with (
            patch.object(shopping_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(shopping_tools._http, "request", mock_request),
        ):
            result = await shopping_tools.search_products.invoke({"query": "wireless headphones"})
        assert result["products"] == []
        assert "403" in result["error"]

    @pytest.mark.asyncio
    async def test_sends_expected_query_params(self):
        body = json.dumps({"organic_results": [_SAMPLE_RESULT]}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(shopping_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(shopping_tools._http, "request", mock_request),
        ):
            await shopping_tools.search_products.invoke({"query": "wireless headphones"})
        _session, method, url = mock_request.await_args.args
        assert method == "GET"
        assert "engine=amazon" in url
        assert "k=wireless+headphones" in url
        # Defaults to the Singapore marketplace.
        assert "amazon_domain=amazon.sg" in url
        assert "api_key=test-key" in url

    @pytest.mark.asyncio
    async def test_custom_amazon_domain_is_sent(self):
        body = json.dumps({"organic_results": [_SAMPLE_RESULT]}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(shopping_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(shopping_tools._http, "request", mock_request),
        ):
            await shopping_tools.search_products.invoke(
                {"query": "wireless headphones", "amazon_domain": "amazon.co.uk"}
            )
        _session, _method, url = mock_request.await_args.args
        assert "amazon_domain=amazon.co.uk" in url


class TestShowShoppingResults:
    @pytest.mark.asyncio
    async def test_returns_no_genui_for_empty_products(self):
        result = await shopping_tools.show_shopping_results.invoke({"title": "Headphones", "products": []})
        assert result["genui"] == []

    @pytest.mark.asyncio
    async def test_renders_product_gallery_with_summary(self):
        result = await shopping_tools.show_shopping_results.invoke(
            {
                "title": "Headphones",
                "products": [
                    {
                        "title": "Sony WH-1000XM5",
                        "price": "$328.00",
                        "rating": 4.7,
                        "reviews": 12345,
                        "image_url": "https://example.com/headphones.jpg",
                        "link": "https://www.amazon.com/dp/B09XS7JWHH",
                        "is_prime": True,
                    }
                ],
            }
        )
        assert "Sony WH-1000XM5" in result["text"]
        assert "$328.00" in result["text"]
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert components["product0Name"]["text"] == "Sony WH-1000XM5"
        assert components["product0Media"]["component"] == "Image"
        assert components["product0Media"]["url"] == "https://example.com/headphones.jpg"
        assert "★ 4.7" in components["product0Subtitle"]["text"]
        assert "Prime" in components["product0Subtitle"]["text"]
        assert (
            components["product0Button"]["action"]["functionCall"]["args"]["url"]
            == "https://www.amazon.com/dp/B09XS7JWHH"
        )

    @pytest.mark.asyncio
    async def test_buy_now_button_text(self):
        result = await shopping_tools.show_shopping_results.invoke(
            {
                "title": "Headphones",
                "products": [{"title": "Sony WH-1000XM5", "link": "https://www.amazon.com/dp/B09XS7JWHH"}],
            }
        )
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert components["product0ButtonText"]["text"] == "Buy Now"

    @pytest.mark.asyncio
    async def test_omits_button_when_no_link(self):
        result = await shopping_tools.show_shopping_results.invoke(
            {"title": "Headphones", "products": [{"title": "Mystery Product"}]}
        )
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert "product0Button" not in components

    @pytest.mark.asyncio
    async def test_omits_media_when_no_image(self):
        result = await shopping_tools.show_shopping_results.invoke(
            {"title": "Headphones", "products": [{"title": "Mystery Product"}]}
        )
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert "product0Media" not in components

    @pytest.mark.asyncio
    async def test_more_count_adds_show_more_button_and_summary_line(self):
        result = await shopping_tools.show_shopping_results.invoke(
            {"title": "Headphones", "products": [{"title": "Product A"}], "more_count": 5}
        )
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert components["moreButtonText"]["text"] == "Show more..."
        assert components["moreButton"]["action"]["event"]["name"] == "show_more_products"
        assert "...and 5 more" in result["text"]

    @pytest.mark.asyncio
    async def test_no_more_count_omits_show_more_button(self):
        result = await shopping_tools.show_shopping_results.invoke(
            {"title": "Headphones", "products": [{"title": "Product A"}]}
        )
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert "moreButton" not in components
        assert "more" not in result["text"]
