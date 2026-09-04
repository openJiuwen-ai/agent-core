# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Map tools for the ReAct agent: geocoding real places and rendering them as
an interactive map with custom rating-pin markers.

Unlike a static map image, real tap-to-see-info markers need a live Google
Maps JavaScript API page, not a server-rendered PNG (see the Static Maps API
alternative this replaced). ``render_map_embed_html()`` builds that page;
``server.py`` serves it at ``MAP_EMBED_ROUTE_PATH`` over this app's own
(self-signed) HTTPS, and the client's dedicated ``MapWeb`` custom component
(mirroring ``YouTubeWebComponent`` -- see that component's own docstring for
why a *custom* component is needed here, not the generic ``Web`` one) loads
it, exactly the same pattern already proven for ``/youtube-embed``.
"""

import asyncio
import json
import re
import time
from typing import Any, Optional
from urllib.parse import quote, urlencode

import aiohttp
from pydantic import BaseModel, Field, field_validator

from openjiuwen.core.foundation.tool import tool
from openjiuwen.harness.tools.web import _http
from openjiuwen.harness.tools.web._common import _REQUEST_HEADERS
from openjiuwen.harness.tools.web._decode import _decode_response_text

from ..core import config, genui

# Places API (New) -- unlike the legacy Geocoding API, one Text Search call
# here also returns a place's real star rating and a real photo when Google
# has them, so `geocode_place` can offer both without a second API/call.
_PLACES_SEARCH_ENDPOINT = "https://places.googleapis.com/v1/places:searchText"
_PLACES_PHOTO_ENDPOINT_PREFIX = "https://places.googleapis.com/v1/"
_PLACES_FIELD_MASK = (
    "places.location,places.formattedAddress,places.rating,places.userRatingCount,"
    "places.photos,places.primaryTypeDisplayName,places.priceLevel,places.currentOpeningHours.openNow"
)
_PLACE_PHOTO_MAX_WIDTH_PX = 640

# Places API (New) returns priceLevel as one of these enum names -- map to the
# short "$"-style text shown on a place's card.
_PRICE_LEVEL_TEXT = {
    "PRICE_LEVEL_FREE": "Free",
    "PRICE_LEVEL_INEXPENSIVE": "$",
    "PRICE_LEVEL_MODERATE": "$$",
    "PRICE_LEVEL_EXPENSIVE": "$$$",
    "PRICE_LEVEL_VERY_EXPENSIVE": "$$$$",
}

MAP_EMBED_ROUTE_PATH = "/map-embed"

# `geocode_place` is normally called once per place in a map request, and a
# fresh `_http.new_session()` per call pays a new TLS handshake to
# places.googleapis.com every time -- a single long-lived session (module
# scope, matching this process's lifetime) reuses pooled connections across
# calls instead, which matters most once several places are geocoded
# concurrently (see agent.py's instruction to batch them in one turn).
_shared_session: Optional[aiohttp.ClientSession] = None
_shared_session_lock = asyncio.Lock()


async def _get_shared_session() -> aiohttp.ClientSession:
    global _shared_session
    if _shared_session is None or _shared_session.closed:
        async with _shared_session_lock:
            if _shared_session is None or _shared_session.closed:
                _shared_session = aiohttp.ClientSession(trust_env=True, connector=_http.make_connector())
    return _shared_session


# Same place asked about more than once in a session (a popular landmark, a
# user refining their request) shouldn't re-hit the Places API -- results
# rarely change within this window, so a short TTL cache trades a little
# staleness for skipping the network round-trip entirely on a repeat.
_GEOCODE_CACHE_TTL_SECONDS = 1800
_geocode_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _geocode_cache_get(query: str) -> Optional[dict[str, Any]]:
    entry = _geocode_cache.get(query)
    if entry is None:
        return None
    cached_at, result = entry
    if time.monotonic() - cached_at > _GEOCODE_CACHE_TTL_SECONDS:
        del _geocode_cache[query]
        return None
    return dict(result)


def _geocode_cache_put(query: str, result: dict[str, Any]) -> None:
    _geocode_cache[query] = (time.monotonic(), dict(result))


# A model that mis-transcribes `geocode_place`'s `image_url` when passing it
# through to `show_map` (truncates it, wraps it in markdown, drops the
# trailing `key=` param) produces a syntactically valid but broken URL --
# nothing else in this pipeline would catch that before it reaches the
# client as a silently-broken image. Requiring the real Places photo URL
# shape here means a garbled one is dropped (falls back to the no-photo
# placeholder) instead of failing on-screen.
_PLACE_PHOTO_URL_RE = re.compile(
    r"^https://places\.googleapis\.com/v1/[^\s?]+/media\?[^\s]*\bkey=[^&\s]+"
)


@tool(
    description=(
        "Resolve a real place name or address into map coordinates, via the "
        "actual Google Places API (not guessed from memory) -- call this "
        "once per place before calling `show_map`, so its pin lands on the "
        "correct real-world location. Pass a specific, unambiguous query "
        "(e.g. 'Grand Palace, Bangkok, Thailand' rather than just 'the "
        "palace'). Returns `lat`/`lng` and `formatted_address` on success, "
        "plus `rating`/`user_ratings_total`/`image_url`/`category`/`price_level`/"
        "`open_now` whenever Google has them for that place -- pass those "
        "straight into `show_map` too so each place's card can show a real "
        "photo, star rating, category, price, and open/closed status, not "
        "just a name. Any of those fields can come back `null` (a place with "
        "no photos yet, or no price data, is normal) -- in that case just "
        "omit that field on `show_map`'s place rather than inventing one. An "
        "`error` (e.g. no match found, or the API key isn't configured) "
        "means don't fabricate coordinates for that place -- leave it out of "
        "the map instead."
    )
)
async def geocode_place(query: str) -> dict[str, Any]:
    api_key = config.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return {
            "query": query,
            "lat": None,
            "lng": None,
            "error": "GOOGLE_MAPS_API_KEY is not configured on the server.",
        }

    cached = _geocode_cache_get(query)
    if cached is not None:
        return cached

    headers = {**_REQUEST_HEADERS, "X-Goog-Api-Key": api_key, "X-Goog-FieldMask": _PLACES_FIELD_MASK}
    try:
        session = await _get_shared_session()
        status, resp_headers, body, _final_url, _truncated = await _http.request(
            session,
            "POST",
            _PLACES_SEARCH_ENDPOINT,
            headers=headers,
            json_body={"textQuery": query},
            timeout_seconds=15,
            max_bytes=1_000_000,
        )
    except Exception as exc:  # noqa: BLE001 -- report the failure, don't crash the tool call
        return {"query": query, "lat": None, "lng": None, "error": str(exc)}

    text = _decode_response_text(body, content_type=resp_headers.get("Content-Type", ""))
    if status >= 400:
        return {"query": query, "lat": None, "lng": None, "error": f"Places API returned HTTP {status}: {text[:300]}"}
    try:
        data = json.loads(text)
    except Exception as exc:  # noqa: BLE001
        return {"query": query, "lat": None, "lng": None, "error": f"Could not parse Places API response: {exc}"}

    places = data.get("places") or []
    if not places:
        return {"query": query, "lat": None, "lng": None, "error": "Places API found no results for this query."}

    place = places[0]
    location = place.get("location") or {}
    if "latitude" not in location or "longitude" not in location:
        return {"query": query, "lat": None, "lng": None, "error": "Places API result had no location."}

    image_url = None
    photos = place.get("photos") or []
    if photos:
        photo_name = photos[0].get("name")
        if photo_name:
            photo_params = urlencode({"maxWidthPx": _PLACE_PHOTO_MAX_WIDTH_PX, "key": api_key})
            image_url = f"{_PLACES_PHOTO_ENDPOINT_PREFIX}{photo_name}/media?{photo_params}"

    category = ((place.get("primaryTypeDisplayName") or {}).get("text")) or None
    price_level = _PRICE_LEVEL_TEXT.get(place.get("priceLevel") or "")
    opening_hours = place.get("currentOpeningHours")
    open_now = opening_hours.get("openNow") if isinstance(opening_hours, dict) else None

    result = {
        "query": query,
        "lat": location["latitude"],
        "lng": location["longitude"],
        "formatted_address": place.get("formattedAddress", query),
        "rating": place.get("rating"),
        "user_ratings_total": place.get("userRatingCount"),
        "image_url": image_url,
        "category": category,
        "price_level": price_level,
        "open_now": open_now,
    }
    _geocode_cache_put(query, result)
    return result


class MapPlace(BaseModel):
    label: str = Field(description="Short name shown for this place, e.g. 'Grand Palace'.")
    lat: float = Field(description="Latitude from a prior `geocode_place` call for this place.")
    lng: float = Field(description="Longitude from a prior `geocode_place` call for this place.")
    image_url: Optional[str] = Field(
        default=None,
        description=(
            "Real photo URL from a prior `geocode_place` call for this place, shown in its info "
            "window when tapped. Omit if `geocode_place` returned `null` -- never invent one."
        ),
    )
    rating: Optional[float] = Field(
        default=None,
        description=(
            "Real star rating (e.g. 4.6) from a prior `geocode_place` call for this place, shown in "
            "its info window when tapped. Omit if `geocode_place` returned `null` -- never invent one."
        ),
    )
    user_ratings_total: Optional[int] = Field(
        default=None,
        description="Real review count from a prior `geocode_place` call, shown alongside `rating` if given.",
    )
    category: Optional[str] = Field(
        default=None,
        description=(
            "Real place type from a prior `geocode_place` call, e.g. 'Seafood restaurant' or "
            "'Hawker stall', shown on this place's card. Omit if `geocode_place` returned `null`."
        ),
    )
    price_level: Optional[str] = Field(
        default=None,
        description=(
            "Real price level from a prior `geocode_place` call, already formatted as '$'-'$$$$' "
            "(or 'Free'), shown on this place's card. Omit if `geocode_place` returned `null`."
        ),
    )
    open_now: Optional[bool] = Field(
        default=None,
        description=(
            "Real current open/closed status from a prior `geocode_place` call, shown as an "
            "'Open'/'Closed' badge on this place's card. Omit if `geocode_place` returned `null` -- "
            "never guess this."
        ),
    )

    @field_validator("image_url")
    @classmethod
    def _drop_malformed_image_url(cls, value: Optional[str]) -> Optional[str]:
        """Null out anything that isn't a real Places photo URL rather than
        raising -- a model that truncates/re-quotes/wraps this in markdown
        when copying it from `geocode_place` produces a syntactically valid
        but broken string; silently dropping it here falls back to the
        no-photo placeholder card instead of a broken image reaching the
        client. See `_PLACE_PHOTO_URL_RE`.
        """
        if value is not None and not _PLACE_PHOTO_URL_RE.match(value):
            return None
        return value


_MAP_EMBED_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  html, body { height: 100%; margin: 0; padding: 0; overflow: hidden; background: #f5f5f5; font-family: -apple-system, Roboto, sans-serif; }
  #map { position: absolute; top: 0; left: 0; right: 0; bottom: 0; }
  /* Google injects this overlay ("Use two fingers to move the map" /
     "Use ctrl + scroll to zoom the map") when gestureHandling is
     "cooperative" -- there's no public API to turn the hint off while
     keeping cooperative's one-finger-scrolls-the-page behavior, only this
     long-stable internal class name to target it directly. */
  .gm-style-pbc { display: none !important; }

  .map-pin { position: absolute; transform: translate(-50%, -100%); cursor: pointer; text-align: center; z-index: 1; }
  .map-pin.active { z-index: 2; }
  .map-pin .pill {
    display: inline-flex; align-items: center; gap: 4px; padding: 4px 10px;
    background: rgba(20, 22, 30, 0.92); color: #fff; border-radius: 999px;
    font-size: 13px; font-weight: 700; white-space: nowrap;
    border: 1.5px solid transparent; box-shadow: 0 1px 4px rgba(0,0,0,0.35);
    transition: transform 0.15s ease, background 0.15s ease, border-color 0.15s ease;
  }
  .map-pin .pill .star { color: #ffd166; }
  .map-pin.active .pill {
    background: #2273f7; border-color: #ffffff; transform: scale(1.08);
  }
  .map-pin .pin-label {
    margin-top: 3px; font-size: 12px; font-weight: 700; color: #202124;
    text-shadow: 0 0 3px #fff, 0 0 3px #fff, 0 1px 2px #fff; max-width: 140px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .map-pin .dot {
    width: 10px; height: 10px; border-radius: 50%; background: #2273f7;
    border: 2px solid #fff; margin: 0 auto;
  }
</style>
</head>
<body>
<div id="map"></div>
<script>
  const PLACES = __PLACES_JSON__;
  let map;
  let markers = [];

  function ratingPillHtml(place) {
    // Built via textContent below, not innerHTML -- see the DOM-building
    // code in buildPin()/buildCard(). This function only decides content,
    // never assembles markup from place data.
    return place.rating != null ? "\\u2605 " + place.rating.toFixed(1) : null;
  }

  function buildPin(place, idx) {
    const el = document.createElement("div");
    el.className = "map-pin";
    if (place.rating != null) {
      const pill = document.createElement("div");
      pill.className = "pill";
      const star = document.createElement("span");
      star.className = "star";
      star.textContent = "\\u2605";
      pill.appendChild(star);
      pill.appendChild(document.createTextNode(" " + place.rating.toFixed(1)));
      el.appendChild(pill);
    } else {
      const dot = document.createElement("div");
      dot.className = "dot";
      el.appendChild(dot);
    }
    const label = document.createElement("div");
    label.className = "pin-label";
    label.textContent = place.label;
    el.appendChild(label);
    el.addEventListener("click", function () { selectPlace(idx); });
    return el;
  }

  // Called both by tapping a pin directly (below) and, via
  // webController.runJavaScript(), by tapping this place's native card in
  // the app's own UI outside this WebView -- see
  // MapWebComponent.highlightPlace() / HighlightMapPlaceFunction.ets.
  function selectPlace(idx) {
    const place = PLACES[idx];
    if (!place || !map) return;
    map.panTo({ lat: place.lat, lng: place.lng });
    if (map.getZoom() < 15) map.setZoom(15);
    markers.forEach(function (m, i) { m.el.classList.toggle("active", i === idx); });
  }

  // Belt-and-suspenders alongside the .gm-style-pbc CSS rule above: that
  // class name is an unofficial, unstable internal implementation detail
  // (confirmed on-device to no longer match -- the hint still showed with
  // only that rule in place). A MutationObserver watching for the hint's
  // container being added was tried next and *also* didn't reliably catch
  // it -- Chrome can insert the wrapper element and its text as separate
  // mutations, so a check keyed off "was this node just added" can run
  // before the text is actually there yet. Polling is slower to react but
  // can't miss it that way: every tick it just asks "is a short hint
  // phrase visible as some element's own text right now", independent of
  // how or when the DOM got into that state.
  function suppressGestureHint() {
    setInterval(function () {
      const candidates = document.body.querySelectorAll("div, span");
      for (let i = 0; i < candidates.length; i++) {
        const el = candidates[i];
        // Direct child text nodes only (not el.textContent) -- that avoids
        // matching a large ancestor (e.g. #map itself) whose subtree just
        // happens to contain the hint somewhere far below it, which would
        // hide far more than intended.
        let ownText = "";
        for (let j = 0; j < el.childNodes.length; j++) {
          if (el.childNodes[j].nodeType === 3) {
            ownText += el.childNodes[j].textContent;
          }
        }
        if (/two finger|ctrl\\s*\\+?\\s*scroll|use ctrl/i.test(ownText)) {
          el.style.setProperty("display", "none", "important");
        }
      }
    }, 400);
  }

  function initMap() {
    const bounds = new google.maps.LatLngBounds();
    const mapDiv = document.getElementById("map");
    map = new google.maps.Map(mapDiv, {
      zoom: 12,
      center: { lat: PLACES[0].lat, lng: PLACES[0].lng },
      // No custom `styles` here -- Google's own default day-mode tiles.
      disableDefaultUI: true,
      zoomControl: true,
      // This map sits inside a scrollable chat transcript, not a full page --
      // without this, a one-finger drag meant to scroll the chat instead pans
      // the map (the API's 'auto' default doesn't detect that context
      // correctly here). 'cooperative' scrolls the page on one finger and
      // only pans the map on a two-finger drag. The "use two fingers" hint
      // this normally shows is suppressed below (not by changing this
      // setting) -- the underlying gesture split is still needed for the
      // nested-scroll behavior with the outer chat.
      gestureHandling: "cooperative",
    });
    suppressGestureHint();
    PLACES.forEach(function (place, idx) {
      const position = { lat: place.lat, lng: place.lng };
      const overlay = new google.maps.OverlayView();
      overlay.onAdd = function () {
        const el = buildPin(place, idx);
        this.getPanes().overlayMouseTarget.appendChild(el);
        markers[idx] = { el: el, overlay: overlay };
      };
      overlay.draw = function () {
        const proj = this.getProjection();
        if (!proj || !markers[idx]) return;
        const point = proj.fromLatLngToDivPixel(new google.maps.LatLng(position.lat, position.lng));
        markers[idx].el.style.left = point.x + "px";
        markers[idx].el.style.top = point.y + "px";
      };
      overlay.onRemove = function () {
        if (markers[idx] && markers[idx].el.parentNode) {
          markers[idx].el.parentNode.removeChild(markers[idx].el);
        }
      };
      overlay.setMap(map);
      bounds.extend(position);
    });
    if (PLACES.length > 1) {
      map.fitBounds(bounds);
    } else {
      map.setZoom(15);
    }
  }
</script>
<script src="https://maps.googleapis.com/maps/api/js?key=__API_KEY__&callback=initMap" async defer></script>
</body>
</html>
"""


def _place_payload(p: MapPlace) -> dict[str, Any]:
    return {
        "label": p.label,
        "lat": p.lat,
        "lng": p.lng,
        "image_url": p.image_url,
        "rating": p.rating,
        "user_ratings_total": p.user_ratings_total,
        "category": p.category,
        "price_level": p.price_level,
        "open_now": p.open_now,
    }


def render_map_embed_html(places: list[MapPlace], api_key: str) -> str:
    """Render a small self-contained HTML page embedding the real Google Maps
    JavaScript API -- unlike a static map image, this gives a real
    interactive day-mode map with a rating pill over every place.
    ``selectPlace(idx)`` (called by tapping a pin, or from the native place
    cards below the map via ``MapWebComponent.highlightPlace()`` -- see
    ``genui.map_places_list()``) pans to and highlights that place's pin.
    Served by ``server.py`` at ``MAP_EMBED_ROUTE_PATH``.
    """
    places_payload = [_place_payload(p) for p in places]
    # Guard against a place label containing a literal "</script>" that would
    # otherwise break out of the surrounding <script> tag.
    places_json = json.dumps(places_payload).replace("</", "<\\/")
    html = _MAP_EMBED_TEMPLATE
    html = html.replace("__PLACES_JSON__", places_json)
    html = html.replace("__API_KEY__", quote(api_key, safe=""))
    return html


@tool(
    description=(
        "Render an interactive map with one or more real places highlighted "
        "as rating pins, as an A2UI surface -- below the map, each place also "
        "gets its own horizontally-scrollable card (photo, rating, category, "
        "price, open/closed status, whichever of those are available); "
        "tapping a place's card pans the map to and highlights its pin. "
        "Every place's `lat`/`lng` must come from a prior `geocode_place` call on that "
        "place -- never invent coordinates, ratings, or image URLs; pass "
        "through exactly what `geocode_place` returned (including `null` "
        "fields -- just leave those out of the place, don't invent a "
        "replacement value). This is the tool for \"show me X on a "
        "map\"/\"where is X located\" requests -- geocode every place you "
        "want to show first, then call this once with the full list; don't "
        "call it once per place. With multiple places, the map "
        "automatically frames all of them."
    )
)
def show_map(title: str, places: list[MapPlace]) -> dict[str, Any]:
    api_key = config.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return {"text": "Maps aren't configured on the server right now.", "genui": []}
    base_url = config.get("PUBLIC_BASE_URL")
    if not base_url:
        return {"text": "The interactive map isn't configured on the server right now.", "genui": []}
    parsed_places = [p if isinstance(p, MapPlace) else MapPlace(**p) for p in places]
    if not parsed_places:
        return {"text": "I couldn't find any of those places to put on a map.", "genui": []}

    surface_id = genui.new_surface_id("map")
    payloads = [_place_payload(p) for p in parsed_places]
    places_payload = json.dumps(payloads)
    embed_url = f"{base_url.rstrip('/')}{MAP_EMBED_ROUTE_PATH}?data={quote(places_payload, safe='')}"
    summary = "\n".join(f"- {p.label}" for p in parsed_places)
    return {
        "text": f"{title}\n{summary}",
        "genui": genui.map_card(surface_id, title, embed_url, places=payloads),
    }
