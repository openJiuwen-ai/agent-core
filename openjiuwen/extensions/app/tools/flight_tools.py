# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Flight search tools for the ReAct agent: real flight availability/pricing
via SerpApi's Google Flights engine, rendered as a gallery of flight cards.

Required search parameters (route, dates, passenger counts) are never
guessed -- the booking policy in ``agent.py``'s system prompt has the agent
collect them from the user first via `ask_preferences_form` (which already
auto-inserts `outbound_date`/`return_date` fields for flight-titled forms),
then pass them straight into `search_flights`.

If `search_flights`/`show_flight_results` are unavailable (no API key
configured) or return no flights, the system prompt's booking policy falls
back to `free_search`/`browser_inspect_page` to find and inspect a real
flight booking site instead -- see ``agent.py``.
"""

import json
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlencode

from pydantic import BaseModel, Field

from openjiuwen.core.foundation.tool import tool
from openjiuwen.harness.tools.web import _http
from openjiuwen.harness.tools.web._common import _REQUEST_HEADERS
from openjiuwen.harness.tools.web._decode import _decode_response_text

from .. import config, genui

_FLIGHTS_SEARCH_ENDPOINT = "https://serpapi.com/search.json"
_FLIGHTS_ENGINE = "google_flights"
MAX_FLIGHT_RESULTS = 10

_TRAVEL_CLASS_CODES = {"economy": 1, "premium_economy": 2, "business": 3, "first": 4}
_CURRENCY_SYMBOLS = {
    "USD": "$", "SGD": "S$", "EUR": "€", "GBP": "£", "JPY": "¥",
    "CNY": "¥", "AUD": "A$", "MYR": "RM", "HKD": "HK$", "THB": "฿",
}


def _format_price(price: Optional[int], currency: str) -> Optional[str]:
    if price is None:
        return None
    symbol = _CURRENCY_SYMBOLS.get(currency.upper())
    return f"{symbol}{price:,}" if symbol else f"{price:,} {currency}"


def _format_duration(minutes: Optional[int]) -> Optional[str]:
    if minutes is None:
        return None
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins}m" if hours else f"{mins}m"


def _format_time(raw: Optional[str]) -> Optional[str]:
    """SerpApi returns e.g. "2026-09-10 22:05" -- reformat to "Sep 10, 22:05"
    so an overnight arrival lands on a visibly different date than departure
    instead of just a bare time.
    """
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M").strftime("%b %d, %H:%M")
    except ValueError:
        return raw


def _flights_search_url(
    departure_id: str, arrival_id: str, outbound_date: str, return_date: Optional[str], currency: str
) -> str:
    """A real Google Flights search-results link for this exact route/date(s)
    (Google Flights' own search page accepts a natural-language `q` query) --
    opened externally so the user can pick a specific fare and finish booking
    there themselves, the same handoff pattern as ``hotel_tools``'s per-hotel
    `link`. This app never completes a booking on the user's behalf.
    """
    query = f"Flights from {departure_id} to {arrival_id} on {outbound_date}"
    if return_date:
        query += f" through {return_date}"
    return "https://www.google.com/travel/flights?" + urlencode({"q": query, "curr": currency})


@tool(
    description=(
        "Search for real, currently-bookable flights via SerpApi's Google "
        "Flights engine (not guessed from memory) -- use this whenever the "
        "user wants to book/find a flight. `departure_id`/`arrival_id` must "
        "be real 3-letter IATA airport codes (e.g. 'SIN', 'NRT') -- resolve a "
        "city name to its main airport code yourself if needed, don't guess "
        "an unfamiliar one. `outbound_date` must be a real date in "
        "YYYY-MM-DD form; add `return_date` (also YYYY-MM-DD) for a round "
        "trip, or omit it for a one-way search -- both must be real dates "
        "collected from the user first via `ask_preferences_form`, never "
        "invented. `travel_class` is optional: one of 'economy', "
        "'premium_economy', 'business', 'first'. Returns up to 10 real "
        "flight itineraries, each with `airline`, `airline_logo`, `price`, "
        "`stops_label`, `duration`, `travel_class`, `departure_airport`, "
        "`departure_time`, `arrival_airport`, `arrival_time`, and `link` -- "
        "any field besides `airline` can be missing for a given itinerary, "
        "which is normal; only pass fields that are actually present into "
        "`show_flight_results`, never invent a replacement value. An `error` "
        "(no API key configured, or no flights found) means don't fabricate "
        "a flight -- fall back to `free_search`/`browser_inspect_page` to "
        "find a real flight booking site instead, per the booking policy."
    )
)
async def search_flights(
    departure_id: str,
    arrival_id: str,
    outbound_date: str,
    return_date: Optional[str] = None,
    adults: int = 1,
    children: int = 0,
    travel_class: Optional[str] = None,
    currency: str = "USD",
) -> dict[str, Any]:
    api_key = config.get("SERPAPI_API_KEY")
    if not api_key:
        return {
            "departure_id": departure_id,
            "arrival_id": arrival_id,
            "flights": [],
            "error": "SERPAPI_API_KEY is not configured on the server.",
        }

    params: dict[str, Any] = {
        "engine": _FLIGHTS_ENGINE,
        "departure_id": departure_id,
        "arrival_id": arrival_id,
        "outbound_date": outbound_date,
        "type": 1 if return_date else 2,
        "adults": max(1, adults),
        "children": max(0, children),
        "currency": currency,
        "api_key": api_key,
    }
    if return_date:
        params["return_date"] = return_date
    if travel_class:
        code = _TRAVEL_CLASS_CODES.get(travel_class.lower().replace(" ", "_"))
        if code:
            params["travel_class"] = code
    gl = config.get("SERPAPI_GL")
    if gl:
        params["gl"] = gl
    hl = config.get("SERPAPI_HL")
    if hl:
        params["hl"] = hl

    url = _FLIGHTS_SEARCH_ENDPOINT + "?" + urlencode(params)
    try:
        async with _http.new_session() as session:
            status, headers, body, _final_url, _truncated = await _http.request(
                session, "GET", url, headers=_REQUEST_HEADERS, timeout_seconds=20, max_bytes=3_000_000
            )
    except Exception as exc:  # noqa: BLE001 -- report the failure, don't crash the tool call
        return {"departure_id": departure_id, "arrival_id": arrival_id, "flights": [], "error": str(exc)}

    text = _decode_response_text(body, content_type=headers.get("Content-Type", ""))
    if status >= 400:
        return {
            "departure_id": departure_id,
            "arrival_id": arrival_id,
            "flights": [],
            "error": f"SerpApi returned HTTP {status}: {text[:300]}",
        }
    try:
        data = json.loads(text)
    except Exception as exc:  # noqa: BLE001
        return {
            "departure_id": departure_id,
            "arrival_id": arrival_id,
            "flights": [],
            "error": f"Could not parse SerpApi response: {exc}",
        }

    if data.get("search_metadata", {}).get("status") == "Error":
        error_message = data.get("error") or data.get("search_metadata", {}).get("error") or "unknown error"
        return {
            "departure_id": departure_id,
            "arrival_id": arrival_id,
            "flights": [],
            "error": f"SerpApi Google Flights search failed: {error_message}",
        }

    itineraries = (data.get("best_flights") or []) + (data.get("other_flights") or [])
    if not itineraries:
        return {
            "departure_id": departure_id,
            "arrival_id": arrival_id,
            "flights": [],
            "error": "No flights found for this search.",
        }

    link = _flights_search_url(departure_id, arrival_id, outbound_date, return_date, currency)
    flights: list[dict[str, Any]] = []
    for itinerary in itineraries[:MAX_FLIGHT_RESULTS]:
        legs = itinerary.get("flights") or []
        if not legs:
            continue
        airline_names = list(dict.fromkeys(leg.get("airline") for leg in legs if leg.get("airline")))
        if not airline_names:
            continue
        first_leg, last_leg = legs[0], legs[-1]
        stops = len(legs) - 1
        flights.append(
            {
                "airline": ", ".join(airline_names),
                "airline_logo": first_leg.get("airline_logo"),
                "price": _format_price(itinerary.get("price"), currency),
                "stops_label": "Nonstop" if stops == 0 else f"{stops} stop" + ("s" if stops > 1 else ""),
                "duration": _format_duration(itinerary.get("total_duration")),
                "travel_class": first_leg.get("travel_class"),
                "departure_airport": (first_leg.get("departure_airport") or {}).get("id"),
                "departure_time": _format_time((first_leg.get("departure_airport") or {}).get("time")),
                "arrival_airport": (last_leg.get("arrival_airport") or {}).get("id"),
                "arrival_time": _format_time((last_leg.get("arrival_airport") or {}).get("time")),
                "link": link,
            }
        )

    if not flights:
        return {
            "departure_id": departure_id,
            "arrival_id": arrival_id,
            "flights": [],
            "error": "No flights found for this search.",
        }

    return {
        "departure_id": departure_id,
        "arrival_id": arrival_id,
        "outbound_date": outbound_date,
        "return_date": return_date,
        "flights": flights,
    }


class FlightResult(BaseModel):
    airline: str = Field(description="The real operating airline name(s), from a prior `search_flights` call.")
    airline_logo: Optional[str] = Field(
        default=None, description="Real airline logo URL from `search_flights`. Omit if it returned null."
    )
    price: Optional[str] = Field(
        default=None, description="Real formatted price (e.g. '$412') from `search_flights`. Omit if it returned null."
    )
    stops_label: Optional[str] = Field(
        default=None, description="Real stop-count label (e.g. 'Nonstop', '1 stop') from `search_flights`."
    )
    duration: Optional[str] = Field(
        default=None, description="Real total trip duration (e.g. '7h 30m') from `search_flights`."
    )
    travel_class: Optional[str] = Field(
        default=None, description="Real cabin class (e.g. 'Economy') from `search_flights`."
    )
    departure_airport: Optional[str] = Field(
        default=None, description="Real departure airport code from `search_flights`."
    )
    departure_time: Optional[str] = Field(default=None, description="Real departure date/time from `search_flights`.")
    arrival_airport: Optional[str] = Field(default=None, description="Real arrival airport code from `search_flights`.")
    arrival_time: Optional[str] = Field(default=None, description="Real arrival date/time from `search_flights`.")
    link: Optional[str] = Field(
        default=None,
        description="Real Google Flights search link from `search_flights` -- the 'View Flights' button target.",
    )


@tool(
    description=(
        "Render a gallery of real flight results as an A2UI surface, each in "
        "its own card with the airline, price/stops/duration/class, the "
        "departure/arrival route and times, and a 'View Flights' button that "
        "opens Google Flights externally for that exact route/date so the "
        "user can pick a fare and finish booking there themselves -- this "
        "agent never completes a booking on the user's behalf. Every flight "
        "must come from a prior `search_flights` call -- never invent an "
        "airline, price, time, or link. Pass through exactly what "
        "`search_flights` returned, including missing/null fields (just "
        "leave those out of the flight, don't invent a replacement value). "
        "To keep each response fast, show flights 3 at a time: pass only the "
        "next 3 flights from a `search_flights` result and set `more_count` "
        "to how many are left after this batch (e.g. showing flights 1-3 of "
        "10 -> `more_count=7`) -- this renders a 'Show more' button. Each "
        "time the user taps it, you'll see a `show_more_flights` UI action "
        "-- respond by calling this again with just the *next* 3 flights "
        "from that same earlier `search_flights` result (don't search "
        "again, and don't dump all the remaining flights at once), updating "
        "`more_count` to whatever is left after that batch. Repeat this "
        "one-batch-per-tap pattern until every flight has been shown, at "
        "which point the final batch's `more_count` is 0 and no button "
        "renders. If there were 3 or fewer flights to begin with, just show "
        "all of them with `more_count=0`."
    )
)
def show_flight_results(title: str, flights: list[FlightResult], more_count: int = 0) -> dict[str, Any]:
    parsed_flights = [f if isinstance(f, FlightResult) else FlightResult(**f) for f in flights]
    if not parsed_flights:
        return {"text": "I couldn't find any flights for that search.", "genui": []}

    surface_id = genui.new_surface_id("flights")
    summary_lines = []
    for f in parsed_flights:
        line = f"- {f.airline}"
        if f.price:
            line += f" ({f.price})"
        summary_lines.append(line)
    if more_count > 0:
        summary_lines.append(f"...and {more_count} more")
    summary = "\n".join(summary_lines)
    flight_dicts = [f.model_dump(exclude_none=True) for f in parsed_flights]
    return {
        "text": f"{title}\n{summary}",
        "genui": genui.flight_gallery_card(surface_id, title, flight_dicts, more_count=more_count),
    }
