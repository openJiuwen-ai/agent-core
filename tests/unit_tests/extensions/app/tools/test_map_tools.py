# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for openjiuwen.extensions.app.tools.map_tools."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.extensions.app.tools import map_tools


def _config_get(values):
    return lambda key, default=None: values.get(key, default)


class TestGeocodePlace:
    @pytest.mark.asyncio
    async def test_returns_error_when_api_key_not_configured(self):
        with patch.object(map_tools.config, "get", side_effect=_config_get({})):
            result = await map_tools.geocode_place.invoke({"query": "Grand Palace, Bangkok"})
        assert result["lat"] is None
        assert "GOOGLE_MAPS_API_KEY" in result["error"]

    @pytest.mark.asyncio
    async def test_returns_coordinates_rating_and_photo_on_success(self):
        body = json.dumps(
            {
                "places": [
                    {
                        "formattedAddress": "Na Phra Lan Rd, Bangkok, Thailand",
                        "location": {"latitude": 13.75, "longitude": 100.4913},
                        "rating": 4.6,
                        "userRatingCount": 12345,
                        "photos": [{"name": "places/abc123/photos/xyz789", "widthPx": 4032, "heightPx": 3024}],
                    }
                ]
            }
        ).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(map_tools.config, "get", side_effect=_config_get({"GOOGLE_MAPS_API_KEY": "test-key"})),
            patch.object(map_tools._http, "request", mock_request),
        ):
            result = await map_tools.geocode_place.invoke({"query": "Grand Palace, Bangkok"})
        assert result["lat"] == 13.75
        assert result["lng"] == 100.4913
        assert result["formatted_address"] == "Na Phra Lan Rd, Bangkok, Thailand"
        assert result["rating"] == 4.6
        assert result["user_ratings_total"] == 12345
        assert result["image_url"] == (
            "https://places.googleapis.com/v1/places/abc123/photos/xyz789/media?maxWidthPx=640&key=test-key"
        )

    @pytest.mark.asyncio
    async def test_returns_category_price_and_open_now_when_present(self):
        body = json.dumps(
            {
                "places": [
                    {
                        "formattedAddress": "2 Orchard Turn, Singapore",
                        "location": {"latitude": 1.3006, "longitude": 103.8368},
                        "primaryTypeDisplayName": {"text": "Seafood restaurant", "languageCode": "en"},
                        "priceLevel": "PRICE_LEVEL_EXPENSIVE",
                        "currentOpeningHours": {"openNow": False},
                    }
                ]
            }
        ).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(map_tools.config, "get", side_effect=_config_get({"GOOGLE_MAPS_API_KEY": "test-key"})),
            patch.object(map_tools._http, "request", mock_request),
        ):
            result = await map_tools.geocode_place.invoke({"query": "Jumbo Seafood, Singapore"})
        assert result["category"] == "Seafood restaurant"
        assert result["price_level"] == "$$$"
        assert result["open_now"] is False

    @pytest.mark.asyncio
    async def test_returns_null_category_price_and_open_now_when_absent(self):
        body = json.dumps(
            {"places": [{"formattedAddress": "somewhere", "location": {"latitude": 1.0, "longitude": 2.0}}]}
        ).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(map_tools.config, "get", side_effect=_config_get({"GOOGLE_MAPS_API_KEY": "test-key"})),
            patch.object(map_tools._http, "request", mock_request),
        ):
            result = await map_tools.geocode_place.invoke({"query": "somewhere"})
        assert result["category"] is None
        assert result["price_level"] is None
        assert result["open_now"] is None

    @pytest.mark.asyncio
    async def test_returns_null_rating_and_photo_when_place_has_neither(self):
        body = json.dumps(
            {
                "places": [
                    {
                        "formattedAddress": "somewhere, Thailand",
                        "location": {"latitude": 13.75, "longitude": 100.4913},
                    }
                ]
            }
        ).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(map_tools.config, "get", side_effect=_config_get({"GOOGLE_MAPS_API_KEY": "test-key"})),
            patch.object(map_tools._http, "request", mock_request),
        ):
            result = await map_tools.geocode_place.invoke({"query": "somewhere"})
        assert result["rating"] is None
        assert result["user_ratings_total"] is None
        assert result["image_url"] is None
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_sends_field_mask_and_text_query(self):
        body = json.dumps({"places": [{"location": {"latitude": 1.0, "longitude": 2.0}}]}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(map_tools.config, "get", side_effect=_config_get({"GOOGLE_MAPS_API_KEY": "test-key"})),
            patch.object(map_tools._http, "request", mock_request),
        ):
            await map_tools.geocode_place.invoke({"query": "Grand Palace, Bangkok"})
        _session, method, url = mock_request.await_args.args
        kwargs = mock_request.await_args.kwargs
        assert method == "POST"
        assert url == map_tools._PLACES_SEARCH_ENDPOINT
        assert kwargs["headers"]["X-Goog-Api-Key"] == "test-key"
        assert kwargs["headers"]["X-Goog-FieldMask"] == map_tools._PLACES_FIELD_MASK
        assert kwargs["json_body"] == {"textQuery": "Grand Palace, Bangkok"}

    @pytest.mark.asyncio
    async def test_returns_error_when_no_places_found(self):
        body = json.dumps({"places": []}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(map_tools.config, "get", side_effect=_config_get({"GOOGLE_MAPS_API_KEY": "test-key"})),
            patch.object(map_tools._http, "request", mock_request),
        ):
            result = await map_tools.geocode_place.invoke({"query": "somewhere that doesn't exist"})
        assert result["lat"] is None
        assert "error" in result

    @pytest.mark.asyncio
    async def test_http_error_returns_error_field(self):
        mock_request = AsyncMock(return_value=(403, {}, b"", "url", False))
        with (
            patch.object(map_tools.config, "get", side_effect=_config_get({"GOOGLE_MAPS_API_KEY": "test-key"})),
            patch.object(map_tools._http, "request", mock_request),
        ):
            result = await map_tools.geocode_place.invoke({"query": "Grand Palace, Bangkok"})
        assert result["lat"] is None
        assert "403" in result["error"]


class TestRenderMapEmbedHtml:
    def test_embeds_places_as_json(self):
        places = [
            map_tools.MapPlace(label="Grand Palace", lat=13.75, lng=100.4913),
            map_tools.MapPlace(label="Wat Arun", lat=13.7437, lng=100.4888),
        ]
        html = map_tools.render_map_embed_html(places, "test-key")
        assert '"label": "Grand Palace"' in html
        assert '"lat": 13.75' in html
        assert '"label": "Wat Arun"' in html

    def test_embeds_api_key_in_script_src(self):
        places = [map_tools.MapPlace(label="Grand Palace", lat=13.75, lng=100.4913)]
        html = map_tools.render_map_embed_html(places, "test-key")
        assert "key=test-key" in html

    def test_markers_render_as_custom_rating_pins_via_overlay_view(self):
        # No classic google.maps.Marker/`icon:` -- each place gets a custom
        # rating-pill OverlayView pin instead (see buildPin() in the embed).
        places = [map_tools.MapPlace(label="Grand Palace", lat=13.75, lng=100.4913)]
        html = map_tools.render_map_embed_html(places, "test-key")
        assert "icon:" not in html
        assert "new google.maps.Marker(" not in html
        assert "new google.maps.OverlayView()" in html
        assert "buildPin(" in html

    def test_uses_default_day_mode_map_style(self):
        # No custom `styles:` map option -- Google's own default day-mode
        # tiles, not the dark "Night mode" style this used to hardcode.
        places = [map_tools.MapPlace(label="Grand Palace", lat=13.75, lng=100.4913)]
        html = map_tools.render_map_embed_html(places, "test-key")
        assert "styles:" not in html

    def test_suppresses_cooperative_gesture_hint_but_keeps_cooperative_handling(self):
        places = [map_tools.MapPlace(label="Grand Palace", lat=13.75, lng=100.4913)]
        html = map_tools.render_map_embed_html(places, "test-key")
        assert 'gestureHandling: "cooperative"' in html
        # Two independent mechanisms: a CSS rule targeting the notice's
        # class name (fast path, but that name is an unofficial internal
        # detail confirmed on-device to no longer match), plus a polling
        # fallback that hides any element whose own text matches the
        # notice's stable, displayed wording -- confirmed more robust than
        # a MutationObserver, which missed cases where the wrapper and its
        # text are inserted as separate DOM mutations.
        assert ".gm-style-pbc" in html
        assert "function suppressGestureHint()" in html
        assert "setInterval(" in html
        assert "suppressGestureHint();" in html
        assert "two finger" in html

    def test_exposes_select_place_for_native_card_taps_to_call(self):
        # selectPlace(idx) is invoked two ways: tapping a pin directly here,
        # and via MapWebComponent.highlightPlace() -> webController's
        # runJavaScript() when the native place card below the map (see
        # genui.map_places_list()) is tapped -- both need this exact
        # function name/signature to keep working.
        places = [map_tools.MapPlace(label="Grand Palace", lat=13.75, lng=100.4913)]
        html = map_tools.render_map_embed_html(places, "test-key")
        assert "function selectPlace(idx)" in html
        assert "map.panTo(" in html
        assert "buildPin(place, idx)" in html

    def test_embeds_category_price_and_open_status_in_places_json(self):
        # The place cards below the map are now native components built
        # server-side (genui.map_places_list()) from the same payload, not
        # rendered inside this HTML -- so the embed only needs to carry the
        # raw data through, not reference these fields in its own JS.
        places = [
            map_tools.MapPlace(
                label="Jumbo Seafood",
                lat=13.75,
                lng=100.4913,
                category="Seafood restaurant",
                price_level="$$$",
                open_now=False,
            )
        ]
        html = map_tools.render_map_embed_html(places, "test-key")
        assert '"category": "Seafood restaurant"' in html
        assert '"price_level": "$$$"' in html
        assert '"open_now": false' in html

    def test_escapes_closing_script_tag_in_label(self):
        # A place label containing "</script>" must not be able to break out
        # of the page's own <script> block.
        places = [map_tools.MapPlace(label="</script><script>alert(1)</script>", lat=13.75, lng=100.4913)]
        html = map_tools.render_map_embed_html(places, "test-key")
        assert "</script><script>alert(1)</script>" not in html

    def test_uses_text_content_not_inner_html_for_label(self):
        # Marker click handler must build the info window content via
        # textContent, never innerHTML, so a place label can never be
        # interpreted as markup.
        places = [map_tools.MapPlace(label="Grand Palace", lat=13.75, lng=100.4913)]
        html = map_tools.render_map_embed_html(places, "test-key")
        assert "textContent" in html
        assert ".innerHTML" not in html

    def test_embeds_image_url_and_rating_when_present(self):
        places = [
            map_tools.MapPlace(
                label="Grand Palace",
                lat=13.75,
                lng=100.4913,
                image_url="https://places.googleapis.com/v1/places/abc/photos/xyz/media?key=test-key",
                rating=4.6,
                user_ratings_total=12345,
            )
        ]
        html = map_tools.render_map_embed_html(places, "test-key")
        assert '"image_url": "https://places.googleapis.com/v1/places/abc/photos/xyz/media?key=test-key"' in html
        assert '"rating": 4.6' in html
        assert '"user_ratings_total": 12345' in html

    def test_omits_image_url_and_rating_when_absent(self):
        places = [map_tools.MapPlace(label="Grand Palace", lat=13.75, lng=100.4913)]
        html = map_tools.render_map_embed_html(places, "test-key")
        assert '"image_url": null' in html
        assert '"rating": null' in html


class TestShowMap:
    @pytest.mark.asyncio
    async def test_returns_no_genui_when_api_key_not_configured(self):
        with patch.object(map_tools.config, "get", side_effect=_config_get({"PUBLIC_BASE_URL": "https://example.com"})):
            result = await map_tools.show_map.invoke(
                {"title": "Bangkok", "places": [{"label": "Grand Palace", "lat": 13.75, "lng": 100.4913}]}
            )
        assert result["genui"] == []

    @pytest.mark.asyncio
    async def test_returns_no_genui_when_public_base_url_not_configured(self):
        with patch.object(map_tools.config, "get", side_effect=_config_get({"GOOGLE_MAPS_API_KEY": "test-key"})):
            result = await map_tools.show_map.invoke(
                {"title": "Bangkok", "places": [{"label": "Grand Palace", "lat": 13.75, "lng": 100.4913}]}
            )
        assert result["genui"] == []

    @pytest.mark.asyncio
    async def test_returns_no_genui_for_empty_places(self):
        config_values = {"GOOGLE_MAPS_API_KEY": "test-key", "PUBLIC_BASE_URL": "https://example.com"}
        with patch.object(map_tools.config, "get", side_effect=_config_get(config_values)):
            result = await map_tools.show_map.invoke({"title": "Bangkok", "places": []})
        assert result["genui"] == []

    @pytest.mark.asyncio
    async def test_renders_map_card_with_embed_url(self):
        config_values = {"GOOGLE_MAPS_API_KEY": "test-key", "PUBLIC_BASE_URL": "https://example.com:8090"}
        with patch.object(map_tools.config, "get", side_effect=_config_get(config_values)):
            result = await map_tools.show_map.invoke(
                {
                    "title": "Top places in Bangkok",
                    "places": [
                        {"label": "Grand Palace", "lat": 13.75, "lng": 100.4913},
                        {"label": "Wat Arun", "lat": 13.7437, "lng": 100.4888},
                    ],
                }
            )
        assert "Grand Palace" in result["text"]
        assert "Wat Arun" in result["text"]
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert components["map"]["component"] == "MapWeb"
        assert components["map"]["url"].startswith(f"https://example.com:8090{map_tools.MAP_EMBED_ROUTE_PATH}?data=")

    @pytest.mark.asyncio
    async def test_passes_image_url_and_rating_through_to_embed_url_data(self):
        from urllib.parse import parse_qs, urlparse

        config_values = {"GOOGLE_MAPS_API_KEY": "test-key", "PUBLIC_BASE_URL": "https://example.com:8090"}
        with patch.object(map_tools.config, "get", side_effect=_config_get(config_values)):
            result = await map_tools.show_map.invoke(
                {
                    "title": "Bangkok",
                    "places": [
                        {
                            "label": "Grand Palace",
                            "lat": 13.75,
                            "lng": 100.4913,
                            "image_url": "https://places.googleapis.com/v1/places/abc/photos/xyz/media",
                            "rating": 4.6,
                            "user_ratings_total": 12345,
                        }
                    ],
                }
            )
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        embed_url = components["map"]["url"]
        data_json = parse_qs(urlparse(embed_url).query)["data"][0]
        places_data = json.loads(data_json)
        assert places_data == [
            {
                "label": "Grand Palace",
                "lat": 13.75,
                "lng": 100.4913,
                "image_url": "https://places.googleapis.com/v1/places/abc/photos/xyz/media",
                "rating": 4.6,
                "user_ratings_total": 12345,
                "category": None,
                "price_level": None,
                "open_now": None,
            }
        ]

    @pytest.mark.asyncio
    async def test_renders_native_place_cards_with_highlight_action(self):
        config_values = {"GOOGLE_MAPS_API_KEY": "test-key", "PUBLIC_BASE_URL": "https://example.com:8090"}
        with patch.object(map_tools.config, "get", side_effect=_config_get(config_values)):
            result = await map_tools.show_map.invoke(
                {
                    "title": "Bangkok",
                    "places": [
                        {"label": "Grand Palace", "lat": 13.75, "lng": 100.4913, "rating": 4.6},
                        {"label": "Wat Arun", "lat": 13.7437, "lng": 100.4888},
                    ],
                }
            )
        surface_id = result["genui"][0]["createSurface"]["surfaceId"]
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert components["placesList"]["component"] == "List"
        assert components["placesList"]["direction"] == "horizontal"
        assert components["placesList"]["children"] == ["place0Btn", "spacer1", "place1Btn"]
        assert components["place0Btn"]["action"] == {
            "functionCall": {"call": "highlightMapPlace", "args": {"surfaceId": surface_id, "index": 0}}
        }
        assert components["place0Name"]["text"] == "Grand Palace"
        assert "★ 4.6" in components["place0Meta"]["text"]
        assert components["place1Btn"]["action"]["functionCall"]["args"]["index"] == 1

    @pytest.mark.asyncio
    async def test_embed_url_strips_trailing_slash_on_base_url(self):
        config_values = {"GOOGLE_MAPS_API_KEY": "test-key", "PUBLIC_BASE_URL": "https://example.com:8090/"}
        with patch.object(map_tools.config, "get", side_effect=_config_get(config_values)):
            result = await map_tools.show_map.invoke(
                {"title": "Bangkok", "places": [{"label": "Grand Palace", "lat": 13.75, "lng": 100.4913}]}
            )
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert f"https://example.com:8090{map_tools.MAP_EMBED_ROUTE_PATH}?data=" in components["map"]["url"]
        assert "//map-embed" not in components["map"]["url"]
