# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for openjiuwen.harness.a2ui.tools.weather_tools."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.harness.a2ui.core import genui
from openjiuwen.harness.a2ui.tools import weather_tools


def _config_get(values):
    return lambda key, default=None: values.get(key, default)


_GEOCODE_BODY = json.dumps(
    {"places": [{"location": {"latitude": 1.4491, "longitude": 103.8185}, "formattedAddress": "Sembawang, Singapore"}]}
).encode("utf-8")

_DAILY_CONDITION = {
    "iconBaseUri": "https://maps.gstatic.com/weather/v1/partly_cloudy",
    "description": {"text": "Partly sunny", "languageCode": "en"},
    "type": "PARTLY_CLOUDY",
}

_DAILY_DAY = {
    "displayDate": {"year": 2026, "month": 8, "day": 31},
    "daytimeForecast": {"weatherCondition": _DAILY_CONDITION},
    "maxTemperature": {"degrees": 34, "unit": "CELSIUS"},
    "minTemperature": {"degrees": 26, "unit": "CELSIUS"},
}

_HOURLY_CONDITION = {
    "iconBaseUri": "https://maps.gstatic.com/weather/v1/sunny",
    "description": {"text": "Sunny", "languageCode": "en"},
}

_FORECAST_HOUR = {
    "displayDateTime": {"year": 2026, "month": 8, "day": 31, "hours": 15},
    "isDaytime": True,
    "weatherCondition": _HOURLY_CONDITION,
    "temperature": {"degrees": 32, "unit": "CELSIUS"},
}

_HISTORY_HOUR = {
    "displayDateTime": {"year": 2026, "month": 8, "day": 31, "hours": 9},
    "isDaytime": True,
    "weatherCondition": _HOURLY_CONDITION,
    "temperature": {"degrees": 29, "unit": "CELSIUS"},
}


def _body(payload):
    return json.dumps(payload).encode("utf-8")


def _ok(payload):
    return (200, {"Content-Type": "application/json"}, _body(payload), "url", False)


class TestSearchWeatherForecast:
    @pytest.mark.asyncio
    async def test_returns_error_when_api_key_not_configured(self):
        with patch.object(weather_tools.config, "get", side_effect=_config_get({})):
            result = await weather_tools.search_weather_forecast.invoke({"location": "Sembawang, Singapore"})
        assert "error" in result
        assert "GOOGLE_MAPS_API_KEY" in result["error"]

    @pytest.mark.asyncio
    async def test_returns_forecast_on_success(self):
        mock_request = AsyncMock(
            side_effect=[
                (200, {"Content-Type": "application/json"}, _GEOCODE_BODY, "url", False),
                _ok({"forecastDays": [_DAILY_DAY], "timeZone": {"id": "Asia/Singapore"}}),
                _ok({"forecastHours": [_FORECAST_HOUR], "timeZone": {"id": "Asia/Singapore"}}),
            ]
        )
        with (
            patch.object(weather_tools.config, "get", side_effect=_config_get({"GOOGLE_MAPS_API_KEY": "test-key"})),
            patch.object(weather_tools._http, "request", mock_request),
        ):
            result = await weather_tools.search_weather_forecast.invoke({"location": "Sembawang, Singapore"})
        assert result["current_temp"] == 32
        assert result["current_condition"] == "Sunny"
        assert result["current_icon_url"] == "https://maps.gstatic.com/weather/v1/sunny.svg"
        assert result["daily"] == [
            {
                # First entry's day_label is always overridden to "Today".
                "day_label": "Today",
                "date_label": "08-31",
                "icon_url": "https://maps.gstatic.com/weather/v1/partly_cloudy.svg",
                "condition": "Partly sunny",
                "max_temp": 34,
                "min_temp": 26,
                # This day's own hourly bucket, grouped from forecastHours
                # by matching calendar date.
                "hourly": [{"hour_label": "3pm", "temp": 32}],
            }
        ]
        # Top-level `hourly` mirrors today's (daily[0]'s) bucket.
        assert result["hourly"] == [{"hour_label": "3pm", "temp": 32}]
        # A forecast_token is minted and cached so show_weather_forecast can
        # recover the full result even if the model doesn't relay it whole.
        assert result["forecast_token"] in weather_tools._SEARCH_CACHE
        assert weather_tools._SEARCH_CACHE[result["forecast_token"]]["daily"] == result["daily"]

    @pytest.mark.asyncio
    async def test_hourly_points_grouped_by_their_own_calendar_date(self):
        tomorrow_day = {
            "displayDate": {"year": 2026, "month": 9, "day": 1},
            "daytimeForecast": {"weatherCondition": _DAILY_CONDITION},
            "maxTemperature": {"degrees": 33, "unit": "CELSIUS"},
            "minTemperature": {"degrees": 25, "unit": "CELSIUS"},
        }
        tomorrow_hour = {
            "displayDateTime": {"year": 2026, "month": 9, "day": 1, "hours": 9},
            "isDaytime": True,
            "weatherCondition": _HOURLY_CONDITION,
            "temperature": {"degrees": 28, "unit": "CELSIUS"},
        }
        mock_request = AsyncMock(
            side_effect=[
                (200, {"Content-Type": "application/json"}, _GEOCODE_BODY, "url", False),
                _ok({"forecastDays": [_DAILY_DAY, tomorrow_day]}),
                _ok({"forecastHours": [_FORECAST_HOUR, tomorrow_hour]}),
            ]
        )
        with (
            patch.object(weather_tools.config, "get", side_effect=_config_get({"GOOGLE_MAPS_API_KEY": "test-key"})),
            patch.object(weather_tools._http, "request", mock_request),
        ):
            result = await weather_tools.search_weather_forecast.invoke({"location": "Sembawang, Singapore"})
        assert result["daily"][0]["hourly"] == [{"hour_label": "3pm", "temp": 32}]
        assert result["daily"][1]["hourly"] == [{"hour_label": "9am", "temp": 28}]
        assert result["daily"][1]["day_label"] == "Tue"  # not overridden -- only the first entry becomes "Today"

    @pytest.mark.asyncio
    async def test_falls_back_to_daily_when_hourly_call_fails(self):
        mock_request = AsyncMock(
            side_effect=[
                (200, {"Content-Type": "application/json"}, _GEOCODE_BODY, "url", False),
                _ok({"forecastDays": [_DAILY_DAY], "timeZone": {"id": "Asia/Singapore"}}),
                (500, {}, b"", "url", False),
            ]
        )
        with (
            patch.object(weather_tools.config, "get", side_effect=_config_get({"GOOGLE_MAPS_API_KEY": "test-key"})),
            patch.object(weather_tools._http, "request", mock_request),
        ):
            result = await weather_tools.search_weather_forecast.invoke({"location": "Sembawang, Singapore"})
        # Hourly failure doesn't fail the whole call -- daily strip still renders,
        # and "current" falls back to today's daytime forecast.
        assert "error" not in result
        assert result["daily"]
        assert result["hourly"] == []
        assert result["current_temp"] == 34
        assert result["current_condition"] == "Partly sunny"

    @pytest.mark.asyncio
    async def test_returns_error_when_location_not_found(self):
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, _body({"places": []}), "url", False))
        with (
            patch.object(weather_tools.config, "get", side_effect=_config_get({"GOOGLE_MAPS_API_KEY": "test-key"})),
            patch.object(weather_tools._http, "request", mock_request),
        ):
            result = await weather_tools.search_weather_forecast.invoke({"location": "nowhere at all"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_returns_error_when_daily_forecast_call_fails(self):
        mock_request = AsyncMock(
            side_effect=[
                (200, {"Content-Type": "application/json"}, _GEOCODE_BODY, "url", False),
                (403, {}, b"", "url", False),
            ]
        )
        with (
            patch.object(weather_tools.config, "get", side_effect=_config_get({"GOOGLE_MAPS_API_KEY": "test-key"})),
            patch.object(weather_tools._http, "request", mock_request),
        ):
            result = await weather_tools.search_weather_forecast.invoke({"location": "Sembawang, Singapore"})
        assert "error" in result
        assert "403" in result["error"]

    @pytest.mark.asyncio
    async def test_clamps_days_to_api_max(self):
        mock_request = AsyncMock(
            side_effect=[
                (200, {"Content-Type": "application/json"}, _GEOCODE_BODY, "url", False),
                _ok({"forecastDays": [_DAILY_DAY]}),
                _ok({"forecastHours": [_FORECAST_HOUR]}),
            ]
        )
        with (
            patch.object(weather_tools.config, "get", side_effect=_config_get({"GOOGLE_MAPS_API_KEY": "test-key"})),
            patch.object(weather_tools._http, "request", mock_request),
        ):
            await weather_tools.search_weather_forecast.invoke({"location": "Sembawang, Singapore", "days": 99})
        _session, _method, url = mock_request.await_args_list[1].args
        assert f"days={weather_tools.MAX_FORECAST_DAYS}" in url


class TestSearchWeatherHistory:
    @pytest.mark.asyncio
    async def test_returns_error_when_api_key_not_configured(self):
        with patch.object(weather_tools.config, "get", side_effect=_config_get({})):
            result = await weather_tools.search_weather_history.invoke({"location": "Woodlands, Singapore"})
        assert "error" in result
        assert "GOOGLE_MAPS_API_KEY" in result["error"]

    @pytest.mark.asyncio
    async def test_returns_history_on_success(self):
        mock_request = AsyncMock(
            side_effect=[
                (200, {"Content-Type": "application/json"}, _GEOCODE_BODY, "url", False),
                _ok({"historyHours": [_HISTORY_HOUR], "timeZone": {"id": "Asia/Singapore"}}),
            ]
        )
        with (
            patch.object(weather_tools.config, "get", side_effect=_config_get({"GOOGLE_MAPS_API_KEY": "test-key"})),
            patch.object(weather_tools._http, "request", mock_request),
        ):
            result = await weather_tools.search_weather_history.invoke({"location": "Woodlands, Singapore"})
        assert result["as_of"] == "9am"
        assert result["latest_temp"] == 29
        assert result["latest_condition"] == "Sunny"
        assert result["latest_icon_url"] == "https://maps.gstatic.com/weather/v1/sunny.svg"
        assert result["hourly"] == [{"hour_label": "9am", "temp": 29}]

    @pytest.mark.asyncio
    async def test_uses_last_entry_as_latest_reading(self):
        earlier_hour = {**_HISTORY_HOUR, "displayDateTime": {"hours": 6}, "temperature": {"degrees": 24}}
        later_hour = {**_HISTORY_HOUR, "displayDateTime": {"hours": 9}, "temperature": {"degrees": 29}}
        mock_request = AsyncMock(
            side_effect=[
                (200, {"Content-Type": "application/json"}, _GEOCODE_BODY, "url", False),
                _ok({"historyHours": [earlier_hour, later_hour]}),
            ]
        )
        with (
            patch.object(weather_tools.config, "get", side_effect=_config_get({"GOOGLE_MAPS_API_KEY": "test-key"})),
            patch.object(weather_tools._http, "request", mock_request),
        ):
            result = await weather_tools.search_weather_history.invoke({"location": "Woodlands, Singapore"})
        assert result["latest_temp"] == 29
        assert result["as_of"] == "9am"
        assert len(result["hourly"]) == 2

    @pytest.mark.asyncio
    async def test_returns_error_when_no_history_hours(self):
        mock_request = AsyncMock(
            side_effect=[
                (200, {"Content-Type": "application/json"}, _GEOCODE_BODY, "url", False),
                _ok({"historyHours": []}),
            ]
        )
        with (
            patch.object(weather_tools.config, "get", side_effect=_config_get({"GOOGLE_MAPS_API_KEY": "test-key"})),
            patch.object(weather_tools._http, "request", mock_request),
        ):
            result = await weather_tools.search_weather_history.invoke({"location": "Woodlands, Singapore"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_clamps_hours_to_api_max(self):
        mock_request = AsyncMock(
            side_effect=[
                (200, {"Content-Type": "application/json"}, _GEOCODE_BODY, "url", False),
                _ok({"historyHours": [_HISTORY_HOUR]}),
            ]
        )
        with (
            patch.object(weather_tools.config, "get", side_effect=_config_get({"GOOGLE_MAPS_API_KEY": "test-key"})),
            patch.object(weather_tools._http, "request", mock_request),
        ):
            await weather_tools.search_weather_history.invoke({"location": "Woodlands, Singapore", "hours": 999})
        _session, _method, url = mock_request.await_args_list[1].args
        assert f"hours={weather_tools.MAX_HOURLY_POINTS}" in url


class TestShowWeatherForecast:
    @pytest.mark.asyncio
    async def test_renders_forecast_card_with_summary(self):
        result = await weather_tools.show_weather_forecast.invoke(
            {
                "location": "Sembawang, Singapore",
                "current_temp": 32,
                "current_condition": "Sunny",
                "current_icon_url": "https://maps.gstatic.com/weather/v1/sunny.svg",
                "daily": [{"day_label": "Today", "date_label": "08-31", "max_temp": 34, "min_temp": 26}],
                "hourly": [{"hour_label": "3pm", "temp": 32}],
            }
        )
        assert "Sembawang, Singapore" in result["text"]
        assert "32°C" in result["text"]
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert components["location"]["text"] == "Sembawang, Singapore"
        assert components["currentTemp"]["text"] == "32°C"
        assert components["day0Label"]["text"] == "Today"
        assert components["day0Date"]["text"] == "08-31"
        assert components["day0High"]["text"] == "34°"
        assert components["day0Low"]["text"] == "26°"
        assert components["hourlyChart_0"]["component"] == "Chart"
        assert components["hourlyChart_0"]["chartType"] == "line"

    @pytest.mark.asyncio
    async def test_omits_optional_sections_when_absent(self):
        result = await weather_tools.show_weather_forecast.invoke({"location": "Sembawang, Singapore"})
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert components["location"]["text"] == "Sembawang, Singapore"
        assert "currentRow" not in components
        assert "dailyList" not in components
        assert "hourlyChart" not in components

    @pytest.mark.asyncio
    async def test_dailyList_is_a_full_width_row_when_three_or_fewer_days(self):
        # A Row is safe here specifically when there are few enough pills
        # that equal-share flex-grow always fits -- contrast
        # map_places_list, whose place count is open-ended and where a Row
        # silently overlaps once there's more than ~3 items.
        result = await weather_tools.show_weather_forecast.invoke(
            {
                "location": "Sembawang, Singapore",
                "daily": [{"day_label": d} for d in ["Today", "Tue", "Wed"]],
            }
        )
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert components["dailyList"]["component"] == "Row"
        assert components["day0"]["weight"] == 1
        assert components["day1"]["weight"] == 1
        assert components["day2"]["weight"] == 1

    @pytest.mark.asyncio
    async def test_dailyList_becomes_a_scrollable_list_with_more_than_three_days(self):
        # Regression guard: a forecast longer than 3 days must stay
        # reachable by swiping, not silently truncated or overlapping like
        # a plain Row would.
        result = await weather_tools.show_weather_forecast.invoke(
            {
                "location": "Sembawang, Singapore",
                "daily": [{"day_label": d} for d in ["Today", "Tue", "Wed", "Thu", "Fri"]],
            }
        )
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert components["dailyList"]["component"] == "List"
        assert components["dailyList"]["direction"] == "horizontal"
        assert components["dailyList"]["children"] == ["day0", "day1", "day2", "day3", "day4"]
        assert "weight" not in components["day0"]
        assert components["day0"]["styles"]["width"] == genui._WEATHER_DAILY_PILL_WIDTH

    @pytest.mark.asyncio
    async def test_day_pills_are_tappable_buttons_with_select_action(self):
        result = await weather_tools.show_weather_forecast.invoke(
            {
                "location": "Sembawang, Singapore",
                "daily": [{"day_label": d} for d in ["Today", "Tue", "Wed"]],
            }
        )
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert components["day0"]["component"] == "Button"
        assert components["day0"]["action"]["event"]["name"] == "select_forecast_day_0"
        assert components["day1"]["action"]["event"]["name"] == "select_forecast_day_1"
        assert components["day2"]["action"]["event"]["name"] == "select_forecast_day_2"

    @pytest.mark.asyncio
    async def test_first_day_pill_is_selected_by_default(self):
        result = await weather_tools.show_weather_forecast.invoke(
            {"location": "Sembawang, Singapore", "daily": [{"day_label": "Today"}, {"day_label": "Tue"}]}
        )
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert components["day0"]["styles"]["background-color"] == genui._WEATHER_PILL_SELECTED_COLOR
        assert components["day1"]["styles"]["background-color"] == genui._WEATHER_PILL_COLOR

    @pytest.mark.asyncio
    async def test_selected_day_index_highlights_that_pill_and_swaps_chart(self):
        result = await weather_tools.show_weather_forecast.invoke(
            {
                "location": "Sembawang, Singapore",
                "daily": [
                    {"day_label": "Today", "hourly": [{"hour_label": "3pm", "temp": 32}]},
                    {"day_label": "Tue", "hourly": [{"hour_label": "9am", "temp": 27}]},
                ],
                "hourly": [{"hour_label": "3pm", "temp": 32}],
                "selected_day_index": 1,
            }
        )
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert components["day1"]["styles"]["background-color"] == genui._WEATHER_PILL_SELECTED_COLOR
        assert components["day0"]["styles"]["background-color"] == genui._WEATHER_PILL_COLOR
        # The chart now reflects Tuesday's own hourly points, not today's --
        # under a fresh id (see hourly_chart_id's own docstring note for
        # why), not a reused "hourlyChart".
        chart_values = components["hourlyChart_1"]["data"]["series"][0]["data"]
        assert chart_values == [{"value": 27}]

    @pytest.mark.asyncio
    async def test_selected_day_index_out_of_range_clamps_to_last_day(self):
        result = await weather_tools.show_weather_forecast.invoke(
            {
                "location": "Sembawang, Singapore",
                "daily": [{"day_label": "Today"}, {"day_label": "Tue"}],
                "selected_day_index": 99,
            }
        )
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert components["day1"]["styles"]["background-color"] == genui._WEATHER_PILL_SELECTED_COLOR

    @pytest.mark.asyncio
    async def test_first_render_creates_a_new_surface_and_returns_its_id(self):
        result = await weather_tools.show_weather_forecast.invoke({"location": "Sembawang, Singapore"})
        assert "createSurface" in result["genui"][0]
        assert len(result["genui"]) == 2
        assert result["surface_id"] == result["genui"][0]["createSurface"]["surfaceId"]

    @pytest.mark.asyncio
    async def test_passing_surface_id_updates_in_place_without_a_new_surface(self):
        # Regression test: tapping a day pill used to spawn a whole new
        # duplicate card below the original one -- passing the earlier
        # call's own surface_id back must instead send only an
        # updateComponents for that same surface.
        first = await weather_tools.show_weather_forecast.invoke(
            {
                "location": "Sembawang, Singapore",
                "daily": [
                    {"day_label": "Today", "hourly": [{"hour_label": "3pm", "temp": 32}]},
                    {"day_label": "Tue", "hourly": [{"hour_label": "9am", "temp": 27}]},
                ],
            }
        )
        second = await weather_tools.show_weather_forecast.invoke(
            {
                "location": "Sembawang, Singapore",
                "daily": [
                    {"day_label": "Today", "hourly": [{"hour_label": "3pm", "temp": 32}]},
                    {"day_label": "Tue", "hourly": [{"hour_label": "9am", "temp": 27}]},
                ],
                "selected_day_index": 1,
                "surface_id": first["surface_id"],
            }
        )
        assert len(second["genui"]) == 1
        assert "createSurface" not in second["genui"][0]
        assert second["genui"][0]["updateComponents"]["surfaceId"] == first["surface_id"]
        assert second["surface_id"] == first["surface_id"]

    @pytest.mark.asyncio
    async def test_tap_can_omit_everything_but_surface_id_and_selected_day_index(self):
        # Regression test: a day-pill tap used to require the model to
        # replay the entire original daily/hourly payload verbatim, which
        # it would unreliably drop after a couple of taps (falsely
        # reporting "hourly detail isn't available"). The server must
        # remember the original render's data by surface_id instead.
        first = await weather_tools.show_weather_forecast.invoke(
            {
                "location": "Sembawang, Singapore",
                "current_temp": 30,
                "current_condition": "Sunny",
                "daily": [
                    {"day_label": "Today", "hourly": [{"hour_label": "3pm", "temp": 32}]},
                    {"day_label": "Tue", "hourly": [{"hour_label": "9am", "temp": 27}]},
                ],
            }
        )
        second = await weather_tools.show_weather_forecast.invoke(
            {"selected_day_index": 1, "surface_id": first["surface_id"]}
        )
        assert len(second["genui"]) == 1
        assert "createSurface" not in second["genui"][0]
        assert second["genui"][0]["updateComponents"]["surfaceId"] == first["surface_id"]
        components = {c["id"]: c for c in second["genui"][0]["updateComponents"]["components"]}
        assert components["day1"]["styles"]["background-color"] == genui._WEATHER_PILL_SELECTED_COLOR
        chart_values = components["hourlyChart_1"]["data"]["series"][0]["data"]
        assert chart_values == [{"value": 27}]
        # A day-pill tap's own text summarizes only the newly-selected day --
        # see test_pill_tap_returns_selected_day_summary_not_full_rundown.
        assert second["text"] == "Tue"

    @pytest.mark.asyncio
    async def test_pill_tap_returns_selected_day_summary_not_full_rundown(self):
        # Regression test: a day-pill-tap update used to return either the
        # whole multi-day rundown again (repeating what the pill highlight
        # and swapped chart already show) or an empty string (which some
        # clients fall back to showing as an empty/near-empty chat bubble
        # anyway when the model's own reply also comes back empty -- see
        # agent.py's weather flow, step 5). It should instead describe just
        # the day now selected, so whichever text ends up shown is useful.
        first = await weather_tools.show_weather_forecast.invoke(
            {
                "location": "Sembawang, Singapore",
                "current_temp": 30,
                "current_condition": "Sunny",
                "daily": [
                    {"day_label": "Today", "date_label": "08-31", "max_temp": 32, "min_temp": 26},
                    {
                        "day_label": "Tue",
                        "date_label": "09-01",
                        "max_temp": 31,
                        "min_temp": 25,
                        "condition": "Cloudy",
                        "hourly": [{"hour_label": "9am", "temp": 27}],
                    },
                ],
            }
        )
        # First render's text is the whole rundown (every day, including Tue).
        assert "Today" in first["text"]

        second = await weather_tools.show_weather_forecast.invoke(
            {"selected_day_index": 1, "surface_id": first["surface_id"]}
        )
        # Update's text describes only the newly-selected day -- not the
        # whole rundown repeated, and not "Today" (no longer selected).
        assert second["text"] == "Tue (09-01) - 31°/25° - Cloudy"
        assert "Today" not in second["text"]

    @pytest.mark.asyncio
    async def test_forecast_token_supplies_full_daily_data_even_if_model_only_sent_todays_hourly(self):
        # Regression test: relaying a multi-day forecast's per-day hourly
        # curves through the model's own tool-call arguments proved
        # unreliable -- it would tend to only carry today's hourly through
        # and drop later days'. A forecast_token (as returned by
        # search_weather_forecast) must let the card recover the full data
        # server-side regardless of what daily/hourly the model passed.
        weather_tools._SEARCH_CACHE["tok-1"] = {
            "location": "Sembawang, Singapore",
            "current_temp": 30,
            "current_condition": "Sunny",
            "current_icon_url": None,
            "daily": [
                {"day_label": "Today", "hourly": [{"hour_label": "3pm", "temp": 32}]},
                {"day_label": "Wed", "hourly": [{"hour_label": "9am", "temp": 27}]},
            ],
            "hourly": [{"hour_label": "3pm", "temp": 32}],
        }
        result = await weather_tools.show_weather_forecast.invoke(
            {
                # A stand-in for the model only faithfully relaying today's
                # data -- day 1 ("Wed") has no hourly of its own here.
                "location": "Sembawang, Singapore",
                "daily": [{"day_label": "Today"}, {"day_label": "Wed"}],
                "hourly": [{"hour_label": "3pm", "temp": 32}],
                "selected_day_index": 1,
                "forecast_token": "tok-1",
            }
        )
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        chart_values = components["hourlyChart_1"]["data"]["series"][0]["data"]
        assert chart_values == [{"value": 27}]


class TestShowWeatherHistory:
    @pytest.mark.asyncio
    async def test_renders_history_card_with_summary(self):
        result = await weather_tools.show_weather_history.invoke(
            {
                "location": "Woodlands, Singapore",
                "as_of": "9am",
                "latest_temp": 29,
                "latest_condition": "Sunny",
                "latest_icon_url": "https://maps.gstatic.com/weather/v1/sunny.svg",
                "hourly": [{"hour_label": "9am", "temp": 29}],
            }
        )
        assert "Woodlands, Singapore" in result["text"]
        assert "29°C" in result["text"]
        assert "as of 9am" in result["text"]
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert components["location"]["text"] == "Woodlands, Singapore"
        assert components["latestTemp"]["text"] == "29°C"
        assert "9am" in components["latestSubtitle"]["text"]
        assert components["hourlyChart"]["component"] == "Chart"

    @pytest.mark.asyncio
    async def test_omits_optional_sections_when_absent(self):
        result = await weather_tools.show_weather_history.invoke({"location": "Woodlands, Singapore"})
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert "latestRow" not in components
        assert "hourlyChart" not in components
