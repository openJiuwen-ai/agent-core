# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for openjiuwen.harness.a2ui.tools.hotel_tools."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.harness.a2ui.tools import hotel_tools


def _config_get(values):
    return lambda key, default=None: values.get(key, default)


_SAMPLE_PROPERTY = {
    "name": "The Ritz-Carlton, Bali",
    "description": "Zen-like quarters in an upscale property offering refined dining & a spa.",
    "link": "https://www.ritzcarlton.com/en/hotels/dpssw-the-ritz-carlton-bali/overview/",
    "hotel_class": "5-star hotel",
    "overall_rating": 4.6,
    "reviews": 4547,
    "rate_per_night": {"lowest": "$548", "extracted_lowest": 548},
    "images": [
        {"thumbnail": f"https://example.com/thumb{i}.jpg", "original_image": f"https://example.com/full{i}.jpg"}
        for i in range(5)
    ],
}


class TestSearchHotels:
    @pytest.mark.asyncio
    async def test_returns_error_when_api_key_not_configured(self):
        with patch.object(hotel_tools.config, "get", side_effect=_config_get({})):
            result = await hotel_tools.search_hotels.invoke(
                {"location": "Bali", "check_in_date": "2026-09-10", "check_out_date": "2026-09-13"}
            )
        assert result["hotels"] == []
        assert "SERPAPI_API_KEY" in result["error"]

    @pytest.mark.asyncio
    async def test_returns_hotels_on_success(self):
        body = json.dumps({"properties": [_SAMPLE_PROPERTY]}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(hotel_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(hotel_tools._http, "request", mock_request),
        ):
            result = await hotel_tools.search_hotels.invoke(
                {"location": "Bali", "check_in_date": "2026-09-10", "check_out_date": "2026-09-13"}
            )
        assert result["hotels"] == [
            {
                "name": "The Ritz-Carlton, Bali",
                "price_per_night": "$548",
                "rating": 4.6,
                "reviews": 4547,
                "hotel_class": "5-star hotel",
                "image_urls": [
                    "https://example.com/thumb0.jpg",
                    "https://example.com/thumb1.jpg",
                    "https://example.com/thumb2.jpg",
                ],
                "link": "https://www.ritzcarlton.com/en/hotels/dpssw-the-ritz-carlton-bali/overview/",
                "description": "Zen-like quarters in an upscale property offering refined dining & a spa.",
            }
        ]

    @pytest.mark.asyncio
    async def test_caps_images_at_max_hotel_images_per_hotel(self):
        body = json.dumps({"properties": [_SAMPLE_PROPERTY]}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(hotel_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(hotel_tools._http, "request", mock_request),
        ):
            result = await hotel_tools.search_hotels.invoke(
                {"location": "Bali", "check_in_date": "2026-09-10", "check_out_date": "2026-09-13"}
            )
        assert len(result["hotels"][0]["image_urls"]) == hotel_tools.MAX_HOTEL_IMAGES

    @pytest.mark.asyncio
    async def test_image_urls_is_none_when_property_has_no_images(self):
        property_without_images = {**_SAMPLE_PROPERTY, "images": []}
        body = json.dumps({"properties": [property_without_images]}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(hotel_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(hotel_tools._http, "request", mock_request),
        ):
            result = await hotel_tools.search_hotels.invoke(
                {"location": "Bali", "check_in_date": "2026-09-10", "check_out_date": "2026-09-13"}
            )
        assert result["hotels"][0]["image_urls"] is None

    @pytest.mark.asyncio
    async def test_caps_results_at_max_hotel_results(self):
        properties = [{**_SAMPLE_PROPERTY, "name": f"Hotel {i}"} for i in range(15)]
        body = json.dumps({"properties": properties}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(hotel_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(hotel_tools._http, "request", mock_request),
        ):
            result = await hotel_tools.search_hotels.invoke(
                {"location": "Bali", "check_in_date": "2026-09-10", "check_out_date": "2026-09-13"}
            )
        assert len(result["hotels"]) == hotel_tools.MAX_HOTEL_RESULTS

    @pytest.mark.asyncio
    async def test_returns_error_when_no_properties(self):
        body = json.dumps({"properties": []}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(hotel_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(hotel_tools._http, "request", mock_request),
        ):
            result = await hotel_tools.search_hotels.invoke(
                {"location": "nowhere at all", "check_in_date": "2026-09-10", "check_out_date": "2026-09-13"}
            )
        assert result["hotels"] == []
        assert "error" in result

    @pytest.mark.asyncio
    async def test_returns_error_on_serpapi_error_status(self):
        body = json.dumps({"search_metadata": {"status": "Error"}, "error": "Invalid date"}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(hotel_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(hotel_tools._http, "request", mock_request),
        ):
            result = await hotel_tools.search_hotels.invoke(
                {"location": "Bali", "check_in_date": "bad-date", "check_out_date": "2026-09-13"}
            )
        assert result["hotels"] == []
        assert "Invalid date" in result["error"]

    @pytest.mark.asyncio
    async def test_http_error_returns_error_field(self):
        mock_request = AsyncMock(return_value=(403, {}, b"", "url", False))
        with (
            patch.object(hotel_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(hotel_tools._http, "request", mock_request),
        ):
            result = await hotel_tools.search_hotels.invoke(
                {"location": "Bali", "check_in_date": "2026-09-10", "check_out_date": "2026-09-13"}
            )
        assert result["hotels"] == []
        assert "403" in result["error"]

    @pytest.mark.asyncio
    async def test_skips_properties_with_no_name(self):
        body = json.dumps({"properties": [{"description": "no name here"}, _SAMPLE_PROPERTY]}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(hotel_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(hotel_tools._http, "request", mock_request),
        ):
            result = await hotel_tools.search_hotels.invoke(
                {"location": "Bali", "check_in_date": "2026-09-10", "check_out_date": "2026-09-13"}
            )
        assert len(result["hotels"]) == 1
        assert result["hotels"][0]["name"] == "The Ritz-Carlton, Bali"

    @pytest.mark.asyncio
    async def test_sends_expected_query_params(self):
        body = json.dumps({"properties": [_SAMPLE_PROPERTY]}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(hotel_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(hotel_tools._http, "request", mock_request),
        ):
            await hotel_tools.search_hotels.invoke(
                {
                    "location": "Bali",
                    "check_in_date": "2026-09-10",
                    "check_out_date": "2026-09-13",
                    "adults": 3,
                    "children": 1,
                    "currency": "SGD",
                }
            )
        _session, method, url = mock_request.await_args.args
        assert method == "GET"
        assert "engine=google_hotels" in url
        assert "q=Bali" in url
        assert "check_in_date=2026-09-10" in url
        assert "check_out_date=2026-09-13" in url
        assert "adults=3" in url
        assert "children=1" in url
        assert "currency=SGD" in url
        assert "key=test-key" in url

    @pytest.mark.asyncio
    async def test_children_without_ages_default_to_age_12(self):
        # Regression test: Google Hotels rejects children > 0 with no age
        # per child, and that error used to surface back through the LLM as
        # a prompt asking the user to supply ages -- this tool must default
        # every child to 12 itself instead, so search_hotels always
        # succeeds on the first call.
        body = json.dumps({"properties": [_SAMPLE_PROPERTY]}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(hotel_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(hotel_tools._http, "request", mock_request),
        ):
            await hotel_tools.search_hotels.invoke(
                {"location": "Bali", "check_in_date": "2026-09-10", "check_out_date": "2026-09-13", "children": 2}
            )
        _session, _method, url = mock_request.await_args.args
        assert "children_ages=12%2C12" in url

    @pytest.mark.asyncio
    async def test_children_ages_shorter_than_children_count_padded_with_12(self):
        body = json.dumps({"properties": [_SAMPLE_PROPERTY]}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(hotel_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(hotel_tools._http, "request", mock_request),
        ):
            await hotel_tools.search_hotels.invoke(
                {
                    "location": "Bali",
                    "check_in_date": "2026-09-10",
                    "check_out_date": "2026-09-13",
                    "children": 3,
                    "children_ages": [8],
                }
            )
        _session, _method, url = mock_request.await_args.args
        assert "children_ages=8%2C12%2C12" in url

    @pytest.mark.asyncio
    async def test_provided_children_ages_are_respected(self):
        body = json.dumps({"properties": [_SAMPLE_PROPERTY]}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(hotel_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(hotel_tools._http, "request", mock_request),
        ):
            await hotel_tools.search_hotels.invoke(
                {
                    "location": "Bali",
                    "check_in_date": "2026-09-10",
                    "check_out_date": "2026-09-13",
                    "children": 2,
                    "children_ages": [5, 9],
                }
            )
        _session, _method, url = mock_request.await_args.args
        assert "children_ages=5%2C9" in url

    @pytest.mark.asyncio
    async def test_no_children_ages_param_when_children_is_zero(self):
        body = json.dumps({"properties": [_SAMPLE_PROPERTY]}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(hotel_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(hotel_tools._http, "request", mock_request),
        ):
            await hotel_tools.search_hotels.invoke(
                {"location": "Bali", "check_in_date": "2026-09-10", "check_out_date": "2026-09-13"}
            )
        _session, _method, url = mock_request.await_args.args
        assert "children_ages" not in url


class TestShowHotelResults:
    @pytest.mark.asyncio
    async def test_returns_no_genui_for_empty_hotels(self):
        result = await hotel_tools.show_hotel_results.invoke({"title": "Bali hotels", "hotels": []})
        assert result["genui"] == []

    @pytest.mark.asyncio
    async def test_renders_hotel_gallery_with_summary(self):
        result = await hotel_tools.show_hotel_results.invoke(
            {
                "title": "Bali hotels",
                "hotels": [
                    {
                        "name": "The Ritz-Carlton, Bali",
                        "price_per_night": "$548",
                        "rating": 4.6,
                        "reviews": 4547,
                        "hotel_class": "5-star hotel",
                        "image_urls": ["https://example.com/a.jpg", "https://example.com/b.jpg"],
                        "link": "https://example.com/ritz",
                        "description": "Upscale property with a spa.",
                    }
                ],
            }
        )
        assert "The Ritz-Carlton, Bali" in result["text"]
        assert "$548/night" in result["text"]
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert components["hotel0Name"]["text"] == "The Ritz-Carlton, Bali"
        assert components["hotel0Media"]["component"] == "Carousel"
        assert components["hotel0Media"]["content"] == ["https://example.com/a.jpg", "https://example.com/b.jpg"]
        assert components["hotel0Button"]["action"]["functionCall"]["args"]["url"] == "https://example.com/ritz"

    @pytest.mark.asyncio
    async def test_renders_plain_image_for_a_single_photo_hotel(self):
        result = await hotel_tools.show_hotel_results.invoke(
            {
                "title": "Bali hotels",
                "hotels": [{"name": "Solo Photo Hotel", "image_urls": ["https://example.com/only.jpg"]}],
            }
        )
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert components["hotel0Media"]["component"] == "Image"
        assert components["hotel0Media"]["url"] == "https://example.com/only.jpg"

    @pytest.mark.asyncio
    async def test_omits_button_when_no_link(self):
        result = await hotel_tools.show_hotel_results.invoke(
            {"title": "Bali hotels", "hotels": [{"name": "Mystery Hotel"}]}
        )
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert "hotel0Button" not in components

    @pytest.mark.asyncio
    async def test_more_count_adds_show_more_button_and_summary_line(self):
        result = await hotel_tools.show_hotel_results.invoke(
            {"title": "Bali hotels", "hotels": [{"name": "Hotel A"}], "more_count": 5}
        )
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert components["moreButtonText"]["text"] == "Show more..."
        assert "...and 5 more" in result["text"]

    @pytest.mark.asyncio
    async def test_no_more_count_omits_show_more_button(self):
        result = await hotel_tools.show_hotel_results.invoke({"title": "Bali hotels", "hotels": [{"name": "Hotel A"}]})
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert "moreButton" not in components
        assert "more" not in result["text"]
