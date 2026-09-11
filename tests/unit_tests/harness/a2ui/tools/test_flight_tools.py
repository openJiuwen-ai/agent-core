# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for openjiuwen.harness.a2ui.tools.flight_tools."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.harness.a2ui.tools import flight_tools


def _config_get(values):
    return lambda key, default=None: values.get(key, default)


_SAMPLE_ITINERARY = {
    "flights": [
        {
            "airline": "Singapore Airlines",
            "airline_logo": "https://example.com/sq-logo.png",
            "travel_class": "Economy",
            "departure_airport": {"id": "SIN", "name": "Singapore Changi", "time": "2026-09-10 22:05"},
            "arrival_airport": {"id": "NRT", "name": "Narita", "time": "2026-09-11 06:15"},
        }
    ],
    "total_duration": 450,
    "price": 412,
}


class TestSearchFlights:
    @pytest.mark.asyncio
    async def test_returns_error_when_api_key_not_configured(self):
        with patch.object(flight_tools.config, "get", side_effect=_config_get({})):
            result = await flight_tools.search_flights.invoke(
                {"departure_id": "SIN", "arrival_id": "NRT", "outbound_date": "2026-09-10"}
            )
        assert result["flights"] == []
        assert "SERPAPI_API_KEY" in result["error"]

    @pytest.mark.asyncio
    async def test_returns_flights_on_success(self):
        body = json.dumps({"best_flights": [_SAMPLE_ITINERARY]}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(flight_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(flight_tools._http, "request", mock_request),
        ):
            result = await flight_tools.search_flights.invoke(
                {"departure_id": "SIN", "arrival_id": "NRT", "outbound_date": "2026-09-10"}
            )
        assert result["flights"] == [
            {
                "airline": "Singapore Airlines",
                "airline_logo": "https://example.com/sq-logo.png",
                "price": "$412",
                "stops_label": "Nonstop",
                "duration": "7h 30m",
                "travel_class": "Economy",
                "departure_airport": "SIN",
                "departure_time": "Sep 10, 22:05",
                "arrival_airport": "NRT",
                "arrival_time": "Sep 11, 06:15",
                "link": (
                    "https://www.google.com/travel/flights?"
                    "q=Flights+from+SIN+to+NRT+on+2026-09-10&curr=USD"
                ),
            }
        ]

    @pytest.mark.asyncio
    async def test_combines_best_and_other_flights_and_caps_at_max_results(self):
        itineraries = [
            {**_SAMPLE_ITINERARY, "flights": [{**_SAMPLE_ITINERARY["flights"][0], "airline": f"Airline {i}"}]}
            for i in range(8)
        ]
        body = json.dumps({"best_flights": itineraries[:3], "other_flights": itineraries[3:]}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(flight_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(flight_tools._http, "request", mock_request),
        ):
            result = await flight_tools.search_flights.invoke(
                {"departure_id": "SIN", "arrival_id": "NRT", "outbound_date": "2026-09-10"}
            )
        assert len(result["flights"]) == 8
        assert result["flights"][0]["airline"] == "Airline 0"
        assert result["flights"][-1]["airline"] == "Airline 7"

    @pytest.mark.asyncio
    async def test_caps_results_at_max_flight_results(self):
        itineraries = [
            {**_SAMPLE_ITINERARY, "flights": [{**_SAMPLE_ITINERARY["flights"][0], "airline": f"Airline {i}"}]}
            for i in range(15)
        ]
        body = json.dumps({"best_flights": itineraries}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(flight_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(flight_tools._http, "request", mock_request),
        ):
            result = await flight_tools.search_flights.invoke(
                {"departure_id": "SIN", "arrival_id": "NRT", "outbound_date": "2026-09-10"}
            )
        assert len(result["flights"]) == flight_tools.MAX_FLIGHT_RESULTS

    @pytest.mark.asyncio
    async def test_stops_label_reflects_connecting_legs(self):
        itinerary = {
            **_SAMPLE_ITINERARY,
            "flights": [
                _SAMPLE_ITINERARY["flights"][0],
                {
                    "airline": "Singapore Airlines",
                    "departure_airport": {"id": "NRT", "time": "2026-09-11 08:00"},
                    "arrival_airport": {"id": "LAX", "time": "2026-09-11 12:00"},
                },
            ],
        }
        body = json.dumps({"best_flights": [itinerary]}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(flight_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(flight_tools._http, "request", mock_request),
        ):
            result = await flight_tools.search_flights.invoke(
                {"departure_id": "SIN", "arrival_id": "LAX", "outbound_date": "2026-09-10"}
            )
        assert result["flights"][0]["stops_label"] == "1 stop"
        assert result["flights"][0]["arrival_airport"] == "LAX"

    @pytest.mark.asyncio
    async def test_returns_error_when_no_itineraries(self):
        body = json.dumps({"best_flights": [], "other_flights": []}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(flight_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(flight_tools._http, "request", mock_request),
        ):
            result = await flight_tools.search_flights.invoke(
                {"departure_id": "SIN", "arrival_id": "ZZZ", "outbound_date": "2026-09-10"}
            )
        assert result["flights"] == []
        assert "error" in result

    @pytest.mark.asyncio
    async def test_returns_error_on_serpapi_error_status(self):
        body = json.dumps({"search_metadata": {"status": "Error"}, "error": "Invalid airport code"}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(flight_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(flight_tools._http, "request", mock_request),
        ):
            result = await flight_tools.search_flights.invoke(
                {"departure_id": "ZZZ", "arrival_id": "NRT", "outbound_date": "2026-09-10"}
            )
        assert result["flights"] == []
        assert "Invalid airport code" in result["error"]

    @pytest.mark.asyncio
    async def test_http_error_returns_error_field(self):
        mock_request = AsyncMock(return_value=(403, {}, b"", "url", False))
        with (
            patch.object(flight_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(flight_tools._http, "request", mock_request),
        ):
            result = await flight_tools.search_flights.invoke(
                {"departure_id": "SIN", "arrival_id": "NRT", "outbound_date": "2026-09-10"}
            )
        assert result["flights"] == []
        assert "403" in result["error"]

    @pytest.mark.asyncio
    async def test_skips_itineraries_with_no_flights_or_airline(self):
        body = json.dumps(
            {"best_flights": [{"flights": [], "price": 100}, {"flights": [{}], "price": 50}, _SAMPLE_ITINERARY]}
        ).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(flight_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(flight_tools._http, "request", mock_request),
        ):
            result = await flight_tools.search_flights.invoke(
                {"departure_id": "SIN", "arrival_id": "NRT", "outbound_date": "2026-09-10"}
            )
        assert len(result["flights"]) == 1
        assert result["flights"][0]["airline"] == "Singapore Airlines"

    @pytest.mark.asyncio
    async def test_sends_expected_query_params_for_round_trip(self):
        body = json.dumps({"best_flights": [_SAMPLE_ITINERARY]}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(flight_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(flight_tools._http, "request", mock_request),
        ):
            await flight_tools.search_flights.invoke(
                {
                    "departure_id": "SIN",
                    "arrival_id": "NRT",
                    "outbound_date": "2026-09-10",
                    "return_date": "2026-09-17",
                    "adults": 2,
                    "children": 1,
                    "travel_class": "business",
                    "currency": "SGD",
                }
            )
        _session, method, url = mock_request.await_args.args
        assert method == "GET"
        assert "engine=google_flights" in url
        assert "departure_id=SIN" in url
        assert "arrival_id=NRT" in url
        assert "outbound_date=2026-09-10" in url
        assert "return_date=2026-09-17" in url
        assert "type=1" in url
        assert "adults=2" in url
        assert "children=1" in url
        assert "travel_class=3" in url
        assert "currency=SGD" in url
        assert "api_key=test-key" in url

    @pytest.mark.asyncio
    async def test_one_way_search_omits_return_date_and_sets_type_two(self):
        body = json.dumps({"best_flights": [_SAMPLE_ITINERARY]}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(flight_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(flight_tools._http, "request", mock_request),
        ):
            await flight_tools.search_flights.invoke(
                {"departure_id": "SIN", "arrival_id": "NRT", "outbound_date": "2026-09-10"}
            )
        _session, method, url = mock_request.await_args.args
        assert "type=2" in url
        assert "return_date" not in url


class TestShowFlightResults:
    @pytest.mark.asyncio
    async def test_returns_no_genui_for_empty_flights(self):
        result = await flight_tools.show_flight_results.invoke({"title": "Tokyo flights", "flights": []})
        assert result["genui"] == []

    @pytest.mark.asyncio
    async def test_renders_flight_gallery_with_summary(self):
        result = await flight_tools.show_flight_results.invoke(
            {
                "title": "Tokyo flights",
                "flights": [
                    {
                        "airline": "Singapore Airlines",
                        "airline_logo": "https://example.com/sq-logo.png",
                        "price": "$412",
                        "stops_label": "Nonstop",
                        "duration": "7h 30m",
                        "travel_class": "Economy",
                        "departure_airport": "SIN",
                        "departure_time": "Sep 10, 22:05",
                        "arrival_airport": "NRT",
                        "arrival_time": "Sep 11, 06:15",
                        "link": "https://www.google.com/travel/flights?q=test",
                    }
                ],
            }
        )
        assert "Singapore Airlines" in result["text"]
        assert "$412" in result["text"]
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert components["flight0Name"]["text"] == "Singapore Airlines"
        assert components["flight0Logo"]["url"] == "https://example.com/sq-logo.png"
        assert "Nonstop" in components["flight0Subtitle"]["text"]
        assert "SIN" in components["flight0Route"]["text"]
        assert "NRT" in components["flight0Route"]["text"]
        assert components["flight0Button"]["action"]["functionCall"]["args"]["url"] == (
            "https://www.google.com/travel/flights?q=test"
        )

    @pytest.mark.asyncio
    async def test_omits_logo_row_when_no_logo(self):
        result = await flight_tools.show_flight_results.invoke(
            {"title": "Tokyo flights", "flights": [{"airline": "Mystery Air"}]}
        )
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert "flight0Logo" not in components
        assert components["flight0Name"]["text"] == "Mystery Air"

    @pytest.mark.asyncio
    async def test_omits_button_when_no_link(self):
        result = await flight_tools.show_flight_results.invoke(
            {"title": "Tokyo flights", "flights": [{"airline": "Mystery Air"}]}
        )
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert "flight0Button" not in components

    @pytest.mark.asyncio
    async def test_more_count_adds_show_more_button_and_summary_line(self):
        result = await flight_tools.show_flight_results.invoke(
            {"title": "Tokyo flights", "flights": [{"airline": "Airline A"}], "more_count": 5}
        )
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert components["moreButtonText"]["text"] == "Show more..."
        assert components["moreButton"]["action"]["event"]["name"] == "show_more_flights"
        assert "...and 5 more" in result["text"]

    @pytest.mark.asyncio
    async def test_no_more_count_omits_show_more_button(self):
        result = await flight_tools.show_flight_results.invoke(
            {"title": "Tokyo flights", "flights": [{"airline": "Airline A"}]}
        )
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert "moreButton" not in components
