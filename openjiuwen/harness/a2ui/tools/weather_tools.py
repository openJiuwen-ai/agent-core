# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Weather tools for the ReAct agent: real forecast/history via Google's
Weather API, rendered as a day-theme weather card (a multi-day strip plus
an hourly temperature line chart).

Location strings are geocoded via the same Google Places Text Search call
``map_tools.geocode_place`` uses (``GOOGLE_MAPS_API_KEY``), but only
lat/lng is needed here -- kept as a small local helper rather than
depending on ``map_tools``'s own ``@tool``-wrapped function. No other tool
module in this app calls another tool's `.invoke()` internally (that's a
test-harness-only calling convention); each tool module stays
self-contained the same way.
"""

import json
import uuid
from datetime import date
from typing import Any, NamedTuple, Optional
from urllib.parse import urlencode

from pydantic import BaseModel, Field

from openjiuwen.core.foundation.tool import tool
from openjiuwen.harness.tools.web import _http
from openjiuwen.harness.tools.web._common import _REQUEST_HEADERS
from openjiuwen.harness.tools.web._decode import _decode_response_text

from ..core import config, genui

_PLACES_SEARCH_ENDPOINT = "https://places.googleapis.com/v1/places:searchText"
_WEATHER_BASE = "https://weather.googleapis.com/v1"
_DAILY_FORECAST_ENDPOINT = f"{_WEATHER_BASE}/forecast/days:lookup"
_HOURLY_FORECAST_ENDPOINT = f"{_WEATHER_BASE}/forecast/hours:lookup"
_HOURLY_HISTORY_ENDPOINT = f"{_WEATHER_BASE}/history/hours:lookup"

# The API's own documented ranges -- days:lookup accepts 1-10,
# history/hours:lookup caps at 24 for this card's single-period hourly
# curve, forecast/hours:lookup itself allows up to 240. Every displayed
# day is tappable to swap in its own hourly curve (see
# show_weather_forecast), so enough hours are fetched to cover every
# requested day, not just the first few (the card's own layout -- see
# genui.weather_forecast_card's _WEATHER_DAILY_FILL_THRESHOLD -- switches
# to a scrollable strip once there are more days than fit edge to edge).
MAX_FORECAST_DAYS = 10
MAX_HOURLY_POINTS = 24
MAX_FORECAST_HOURLY_HOURS = 240

# Keyed by the `forecast_token` search_weather_forecast hands back, holds
# that exact call's full result (every day's own hourly curve included).
# show_weather_forecast prefers this over whatever daily/hourly the model
# passes it directly -- relaying a multi-day forecast's full nested hourly
# data through the model's own arguments proved unreliable in practice (it
# would tend to only carry today's hourly through and leave later days'
# hourly empty), so the server keeps its own copy of what it already fetched
# rather than trusting a lossy replay of it.
_SEARCH_CACHE: dict[str, dict[str, Any]] = {}


async def _geocode(location: str) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """Resolve `location` to (lat, lng, error) via Places Text Search --
    only lat/lng is needed here, unlike map_tools.geocode_place's fuller
    rating/photo/category extraction.
    """
    api_key = config.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return None, None, "GOOGLE_MAPS_API_KEY is not configured on the server."

    headers = {
        **_REQUEST_HEADERS,
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.location,places.formattedAddress",
    }
    try:
        async with _http.new_session() as session:
            status, resp_headers, body, _final_url, _truncated = await _http.request(
                session,
                "POST",
                _PLACES_SEARCH_ENDPOINT,
                headers=headers,
                json_body={"textQuery": location},
                timeout_seconds=15,
                max_bytes=1_000_000,
            )
    except Exception as exc:  # noqa: BLE001 -- report the failure, don't crash the tool call
        return None, None, str(exc)

    text = _decode_response_text(body, content_type=resp_headers.get("Content-Type", ""))
    if status >= 400:
        return None, None, f"Places API returned HTTP {status}: {text[:300]}"
    try:
        data = json.loads(text)
    except Exception as exc:  # noqa: BLE001
        return None, None, f"Could not parse Places API response: {exc}"

    places = data.get("places") or []
    if not places:
        return None, None, "Places API found no results for this location."
    location_obj = places[0].get("location") or {}
    if "latitude" not in location_obj or "longitude" not in location_obj:
        return None, None, "Places API result had no location."
    return location_obj["latitude"], location_obj["longitude"], None


async def _weather_get(
    endpoint: str, lat: float, lng: float, extra_params: dict[str, Any]
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    api_key = config.get("GOOGLE_MAPS_API_KEY")
    params: dict[str, Any] = {
        "key": api_key,
        "location.latitude": lat,
        "location.longitude": lng,
        **extra_params,
    }
    url = endpoint + "?" + urlencode(params)
    try:
        async with _http.new_session() as session:
            status, headers, body, _final_url, _truncated = await _http.request(
                session, "GET", url, headers=_REQUEST_HEADERS, timeout_seconds=20, max_bytes=2_000_000
            )
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)

    text = _decode_response_text(body, content_type=headers.get("Content-Type", ""))
    if status >= 400:
        return None, f"Weather API returned HTTP {status}: {text[:300]}"
    try:
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001
        return None, f"Could not parse Weather API response: {exc}"


class LatLng(NamedTuple):
    lat: float
    lng: float


async def _weather_get_paginated(
    endpoint: str, location: LatLng, extra_params: dict[str, Any], items_key: str, want: int
) -> tuple[list[dict[str, Any]], Optional[str]]:
    """Like `_weather_get`, but follows `nextPageToken` until at least
    `want` items are collected (or the API runs out of pages). Both
    forecast endpoints paginate by default -- 5 days/page for
    forecast/days:lookup, 24 hours/page for forecast/hours:lookup -- a
    single `_weather_get` call silently returns only the first page even
    when `days`/`hours` asks for more, which used to make a >5-day
    forecast quietly truncate to 5 days, and any day beyond the first
    silently carry no hourly curve at all.
    """
    items: list[dict[str, Any]] = []
    page_token: Optional[str] = None
    while len(items) < want:
        params = {**extra_params, "pageSize": want}
        if page_token:
            params["pageToken"] = page_token
        data, error = await _weather_get(endpoint, location.lat, location.lng, params)
        if error:
            return items, (None if items else error)
        page_items = (data or {}).get(items_key) or []
        items.extend(page_items)
        page_token = (data or {}).get("nextPageToken")
        if not page_token or not page_items:
            break
    return items, None


def _day_label(display_date: dict[str, Any]) -> str:
    try:
        return date(display_date["year"], display_date["month"], display_date["day"]).strftime("%a")
    except (KeyError, TypeError, ValueError):
        return ""


def _date_label(display_date: dict[str, Any]) -> str:
    try:
        return f"{display_date['month']:02d}-{display_date['day']:02d}"
    except (KeyError, TypeError, ValueError):
        return ""


def _hour_label(hour: Optional[int]) -> str:
    if hour is None:
        return ""
    period = "am" if hour < 12 else "pm"
    display_hour = hour % 12 or 12
    return f"{display_hour}{period}"


def _icon_url(condition: dict[str, Any]) -> Optional[str]:
    # Day (light) theme -- no "_dark" suffix -- per
    # https://developers.google.com/maps/documentation/weather/weather-condition-icons:
    # append a theme and file extension to iconBaseUri yourself, light is
    # the bare/no-suffix form.
    base = condition.get("iconBaseUri")
    return f"{base}.svg" if base else None


def _condition_text(condition: dict[str, Any]) -> Optional[str]:
    return (condition.get("description") or {}).get("text")


@tool(
    description=(
        "Get a real multi-day weather forecast plus an hourly temperature "
        "curve per day for a specific place, via Google's Weather API (not "
        "guessed from memory) -- use this for 'what's the weather in X' / "
        "'will it rain in X this week' requests. `location` should be a "
        "specific place (e.g. 'Sembawang, Singapore', not just 'nearby'). "
        "`days` (1-10) controls how many days of data to fetch AND how "
        "many day cards `show_weather_forecast` displays -- pass the "
        "number of days the user actually asked about (default 7 if they "
        "didn't say). Returns `current_temp`/`current_condition`/"
        "`current_icon_url` (right "
        "now), `daily` (a list of `day_label`/`date_label`/`icon_url`/"
        "`condition`/`max_temp`/`min_temp`/`hourly`, one per day -- the "
        "first entry's `day_label` is already 'Today', don't relabel it), "
        "`hourly` (today's `hour_label`/`temp` list, same as "
        "`daily[0]['hourly']`), and a `forecast_token` -- pass all of "
        "these straight into `show_weather_forecast` (including "
        "`forecast_token`, so its card has the complete per-day data even "
        "if you don't retype every day's own `hourly` list yourself). Any field can come back missing/empty, "
        "which is normal; never invent a replacement. An `error` (no API "
        "key configured, location not found, or no forecast data) means "
        "tell the user the lookup failed instead of fabricating a "
        "forecast."
    )
)
async def search_weather_forecast(location: str, days: int = 7) -> dict[str, Any]:
    lat, lng, geocode_error = await _geocode(location)
    if geocode_error:
        return {"location": location, "error": geocode_error}

    days = min(max(days, 1), MAX_FORECAST_DAYS)
    # Both forecast endpoints paginate by default (5 days/page, 24
    # hours/page) -- _weather_get_paginated follows nextPageToken so a
    # >5-day or >24-hour request actually comes back in full instead of
    # silently truncating to the first page.
    forecast_days, daily_error = await _weather_get_paginated(
        _DAILY_FORECAST_ENDPOINT, LatLng(lat, lng), {"days": days}, "forecastDays", days
    )
    if daily_error:
        return {"location": location, "error": daily_error}

    # Hourly data backs both the "right now" summary and each displayed
    # day's own tap-to-view curve (see show_weather_forecast's
    # selected_day_index) -- fetch enough hours to cover every requested
    # day, not just the first few. A failure here shouldn't fail the whole
    # call -- the daily strip alone is still useful.
    hours_needed = min(days * 24, MAX_FORECAST_HOURLY_HOURS)
    forecast_hours, _hourly_error = await _weather_get_paginated(
        _HOURLY_FORECAST_ENDPOINT, LatLng(lat, lng), {"hours": hours_needed}, "forecastHours", hours_needed
    )

    # Group hourly points by calendar date so each daily card can carry
    # its own hourly curve.
    hourly_by_date: dict[tuple[Any, Any, Any], list[dict[str, Any]]] = {}
    for hour in forecast_hours:
        display = hour.get("displayDateTime") or {}
        key = (display.get("year"), display.get("month"), display.get("day"))
        hourly_by_date.setdefault(key, []).append(
            {
                "hour_label": _hour_label(display.get("hours")),
                "temp": (hour.get("temperature") or {}).get("degrees"),
            }
        )

    daily: list[dict[str, Any]] = []
    for day in forecast_days[:MAX_FORECAST_DAYS]:
        display_date = day.get("displayDate") or {}
        daytime = day.get("daytimeForecast") or {}
        condition = daytime.get("weatherCondition") or {}
        key = (display_date.get("year"), display_date.get("month"), display_date.get("day"))
        daily.append(
            {
                "day_label": _day_label(display_date),
                "date_label": _date_label(display_date),
                "icon_url": _icon_url(condition),
                "condition": _condition_text(condition),
                "max_temp": (day.get("maxTemperature") or {}).get("degrees"),
                "min_temp": (day.get("minTemperature") or {}).get("degrees"),
                "hourly": hourly_by_date.get(key, []),
            }
        )
    if daily:
        # First entry is always today -- "Today" reads better right next
        # to "now"'s summary above it than repeating its own weekday name.
        daily[0]["day_label"] = "Today"

    current_temp = current_condition = current_icon_url = None
    if forecast_hours:
        first_hour = forecast_hours[0]
        current_temp = (first_hour.get("temperature") or {}).get("degrees")
        first_condition = first_hour.get("weatherCondition") or {}
        current_condition = _condition_text(first_condition)
        current_icon_url = _icon_url(first_condition)
    elif daily:
        # No hourly data at all -- fall back to today's daytime forecast
        # for the "current" summary rather than leaving it empty.
        current_temp = daily[0].get("max_temp")
        current_condition = daily[0].get("condition")
        current_icon_url = daily[0].get("icon_url")

    hourly = daily[0]["hourly"] if daily else []
    if not daily and not hourly:
        return {"location": location, "error": "No forecast data available for this location."}

    forecast_token = uuid.uuid4().hex[:12]
    result = {
        "location": location,
        "current_temp": current_temp,
        "current_condition": current_condition,
        "current_icon_url": current_icon_url,
        "daily": daily,
        "hourly": hourly,
    }
    _SEARCH_CACHE[forecast_token] = result
    return {**result, "forecast_token": forecast_token}


@tool(
    description=(
        "Get real recent hourly weather history for a specific place, via "
        "Google's Weather API (not guessed from memory) -- use this for "
        "'what was the weather like in X' / 'how hot was it in X today' "
        "requests, as opposed to a forecast. `location` should be a "
        "specific place. `hours` (1-24) controls how many past hours to "
        "return. Returns `as_of`/`latest_temp`/`latest_condition`/"
        "`latest_icon_url` (the most recent recorded reading) and `hourly` "
        "(a list of `hour_label`/`temp` for the requested past hours) -- "
        "pass these straight into `show_weather_history`. Any field can "
        "come back missing/empty, which is normal; never invent a "
        "replacement. An `error` (no API key configured, location not "
        "found, or no historical data) means tell the user the lookup "
        "failed instead of fabricating a reading."
    )
)
async def search_weather_history(location: str, hours: int = 24) -> dict[str, Any]:
    lat, lng, geocode_error = await _geocode(location)
    if geocode_error:
        return {"location": location, "error": geocode_error}

    hours = min(max(hours, 1), MAX_HOURLY_POINTS)
    history_data, history_error = await _weather_get(_HOURLY_HISTORY_ENDPOINT, lat, lng, {"hours": hours})
    if history_error:
        return {"location": location, "error": history_error}

    history_hours = (history_data or {}).get("historyHours") or []
    if not history_hours:
        return {"location": location, "error": "No historical weather data available for this location."}

    hourly: list[dict[str, Any]] = []
    for hour in history_hours[:MAX_HOURLY_POINTS]:
        display = hour.get("displayDateTime") or {}
        hourly.append(
            {
                "hour_label": _hour_label(display.get("hours")),
                "temp": (hour.get("temperature") or {}).get("degrees"),
            }
        )

    # historyHours is chronological (oldest first) per the API -- the last
    # entry is the most recent reading, used as the card's headline value.
    latest = history_hours[-1]
    latest_condition = latest.get("weatherCondition") or {}
    latest_display = latest.get("displayDateTime") or {}

    return {
        "location": location,
        "as_of": _hour_label(latest_display.get("hours")),
        "latest_temp": (latest.get("temperature") or {}).get("degrees"),
        "latest_condition": _condition_text(latest_condition),
        "latest_icon_url": _icon_url(latest_condition),
        "hourly": hourly,
    }


# Keyed by surface_id, holds the full args from the render that created
# each forecast card. A day-pill tap only needs to send back
# surface_id + selected_day_index (see show_weather_forecast) -- asking the
# model to instead replay the whole daily/hourly payload (potentially
# 10 days x 24 hours of nested data) on every tap proved unreliable in
# practice: it would silently drop or misremember a day's hourly list
# after a couple of taps and claim "hourly detail isn't available" even
# though the original search had it. The server remembering its own
# output sidesteps that entirely.
_FORECAST_STATE: dict[str, dict[str, Any]] = {}


class HourlyPoint(BaseModel):
    hour_label: str = Field(description="Short hour label (e.g. '3pm'), from a prior search_weather_* call.")
    temp: Optional[float] = Field(default=None, description="Temperature in degrees at this hour. Omit if null.")


class DailyForecast(BaseModel):
    day_label: str = Field(
        description="Short day label (e.g. 'Mon', or 'Today' for the first entry), from search_weather_forecast."
    )
    date_label: Optional[str] = Field(default=None, description="Real MM-DD date label. Omit if null.")
    icon_url: Optional[str] = Field(default=None, description="Real weather icon URL. Omit if null.")
    condition: Optional[str] = Field(default=None, description="Real short condition text. Omit if null.")
    max_temp: Optional[float] = Field(default=None, description="Real high temperature in degrees. Omit if null.")
    min_temp: Optional[float] = Field(default=None, description="Real low temperature in degrees. Omit if null.")
    hourly: Optional[list[HourlyPoint]] = Field(
        default=None,
        description=(
            "This day's own real hour_label/temp list from search_weather_forecast -- shown as the chart "
            "when this day's card is the selected one. Omit if search_weather_forecast returned none for it."
        ),
    )


@tool(
    description=(
        "Render a day-theme weather forecast card as an A2UI surface: "
        "current conditions, a strip of tappable day cards (icon + "
        "high/low per day -- shows every day in `daily`, scrollable if "
        "there are more than fit edge to edge), and an hourly temperature "
        "chart for whichever day is selected (today, by default). Every "
        "value must come from a prior `search_weather_forecast` call -- "
        "never invent a temperature, condition, or icon. Pass through "
        "what that tool returned, including missing/null fields (just "
        "leave a field out if it was null, don't invent a replacement "
        "value), and its `forecast_token` -- the card's full per-day data "
        "comes from that token server-side, so it's fine if you don't "
        "manage to retype every day's own `hourly` list yourself. Call "
        "this once per forecast request, not once per day.\n"
        "This tool's own result includes a `surface_id` -- remember it. "
        "Tapping a day card sends a `select_forecast_day_<N>` UI action "
        "back to you (N is the tapped card's 0-indexed position) -- "
        "respond by calling this again with ONLY `surface_id` (the id you "
        "remembered) and `selected_day_index` set to N; leave everything "
        "else out, including `forecast_token` (don't call "
        "`search_weather_forecast` again either) -- the server remembers "
        "the full forecast from the original call and reuses it, so the "
        "*same* card updates in place (chart swaps to that day's hourly "
        "curve, that pill highlights) instead of a duplicate card "
        "appearing below it. Omit `surface_id` only for the first render "
        "of a new forecast."
    )
)
def show_weather_forecast(  # pylint: disable=huawei-too-many-arguments -- flat params are the tool's LLM schema
    location: Optional[str] = None,
    current_temp: Optional[float] = None,
    current_condition: Optional[str] = None,
    current_icon_url: Optional[str] = None,
    daily: Optional[list[DailyForecast]] = None,
    hourly: Optional[list[HourlyPoint]] = None,
    selected_day_index: int = 0,
    surface_id: Optional[str] = None,
    forecast_token: Optional[str] = None,
) -> dict[str, Any]:
    is_update = surface_id is not None
    # Prefer server-held state over whatever the model passed: a
    # forecast_token (present on a first render) points at exactly what
    # search_weather_forecast fetched, including every day's own hourly
    # curve -- more trustworthy than the model retyping that whole nested
    # structure itself, which tends to only carry today's hourly through.
    # Failing that, a day-pill tap (surface_id, no forecast_token) reuses
    # what was cached from this same surface's own original render.
    authoritative = _SEARCH_CACHE.get(forecast_token) if forecast_token else None
    if authoritative is None and is_update:
        authoritative = _FORECAST_STATE.get(surface_id)
    if authoritative:
        location = authoritative["location"]
        current_temp = authoritative["current_temp"]
        current_condition = authoritative["current_condition"]
        current_icon_url = authoritative["current_icon_url"]
        daily = authoritative["daily"]
        hourly = authoritative["hourly"]

    parsed_daily = [d if isinstance(d, DailyForecast) else DailyForecast(**d) for d in (daily or [])]
    parsed_hourly = [h if isinstance(h, HourlyPoint) else HourlyPoint(**h) for h in (hourly or [])]

    selected_day_index = max(0, min(selected_day_index, len(parsed_daily) - 1)) if parsed_daily else 0
    selected_day = parsed_daily[selected_day_index] if parsed_daily else None
    # The tapped day's own hourly curve takes over the chart; fall back to
    # the top-level `hourly` arg (today's) if that day has none of its own.
    chart_hourly = (selected_day.hourly if selected_day and selected_day.hourly else None) or parsed_hourly

    if surface_id is None:
        surface_id = genui.new_surface_id("weather")

    # Remember this render's full args so a later day-pill tap on this
    # surface can omit them and still get the right data back.
    _FORECAST_STATE[surface_id] = {
        "location": location,
        "current_temp": current_temp,
        "current_condition": current_condition,
        "current_icon_url": current_icon_url,
        "daily": [d.model_dump(exclude_none=True) for d in parsed_daily],
        "hourly": [h.model_dump(exclude_none=True) for h in parsed_hourly],
    }

    # A day-pill tap (is_update) re-renders this same card in place -- its
    # own pill highlight and swapped chart already show what changed, so
    # the model is told (see agent.py's weather flow) to add no reply text
    # of its own for that action. This `text` is what a client falls back
    # to displaying if the model's own final reply comes back empty, so it
    # still needs to be a real, useful description of the day now
    # selected (not the whole multi-day rundown again, and not blank --
    # blank was tried and still left a chat bubble showing up some of the
    # time, just an empty/near-empty one instead of a helpful one).
    if is_update:
        if selected_day:
            day_heading = (
                f"{selected_day.day_label} ({selected_day.date_label})"
                if selected_day.date_label
                else selected_day.day_label
            )
            summary_parts = [day_heading]
            temps = []
            if selected_day.max_temp is not None:
                temps.append(f"{round(selected_day.max_temp)}°")
            if selected_day.min_temp is not None:
                temps.append(f"{round(selected_day.min_temp)}°")
            if temps:
                summary_parts.append("/".join(temps))
            if selected_day.condition:
                summary_parts.append(selected_day.condition)
            summary = " - ".join(summary_parts)
        else:
            summary = ""
    else:
        summary_parts = [location] if location else []
        if current_temp is not None:
            summary_parts.append(f"{round(current_temp)}°C")
        if current_condition:
            summary_parts.append(current_condition)
        summary = " - ".join(summary_parts)
        if parsed_daily:
            summary += "\n" + "\n".join(
                f"- {d.day_label} ({d.date_label}): {round(d.max_temp) if d.max_temp is not None else '?'}°/"
                f"{round(d.min_temp) if d.min_temp is not None else '?'}°"
                for d in parsed_daily
            )

    return {
        "text": summary,
        "surface_id": surface_id,
        "genui": genui.weather_forecast_card(
            surface_id,
            location,
            current_temp=current_temp,
            current_condition=current_condition,
            current_icon_url=current_icon_url,
            daily=[d.model_dump(exclude_none=True) for d in parsed_daily],
            hourly=[h.model_dump(exclude_none=True) for h in chart_hourly],
            selected_day_index=selected_day_index,
            is_update=is_update,
        ),
    }


@tool(
    description=(
        "Render a day-theme weather history card as an A2UI surface: the "
        "most recent recorded reading and an hourly temperature chart for "
        "the requested past period. Every value must come from a prior "
        "`search_weather_history` call -- never invent a temperature, "
        "condition, or icon. Pass through exactly what that tool returned, "
        "including missing/null fields (just leave those out, don't "
        "invent a replacement value). Call this once per history request."
    )
)
def show_weather_history(  # pylint: disable=huawei-too-many-arguments -- flat params are the tool's LLM schema
    location: str,
    as_of: Optional[str] = None,
    latest_temp: Optional[float] = None,
    latest_condition: Optional[str] = None,
    latest_icon_url: Optional[str] = None,
    hourly: Optional[list[HourlyPoint]] = None,
) -> dict[str, Any]:
    parsed_hourly = [h if isinstance(h, HourlyPoint) else HourlyPoint(**h) for h in (hourly or [])]

    surface_id = genui.new_surface_id("weather")
    summary_parts = [location]
    if latest_temp is not None:
        summary_parts.append(f"{round(latest_temp)}°C")
    if latest_condition:
        summary_parts.append(latest_condition)
    if as_of:
        summary_parts.append(f"as of {as_of}")
    summary = " - ".join(summary_parts)

    return {
        "text": summary,
        "genui": genui.weather_history_card(
            surface_id,
            location,
            as_of=as_of,
            latest_temp=latest_temp,
            latest_condition=latest_condition,
            latest_icon_url=latest_icon_url,
            hourly=[h.model_dump(exclude_none=True) for h in parsed_hourly],
        ),
    }
