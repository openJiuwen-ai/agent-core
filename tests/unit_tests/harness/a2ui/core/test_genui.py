# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for openjiuwen.harness.a2ui.core.genui."""

import pytest

from openjiuwen.harness.a2ui.core import genui


class TestSurfaceMessages:
    def test_create_surface_uses_configured_catalog_by_default(self):
        message = genui.create_surface("surface-1")
        assert message["version"] == genui.A2UI_VERSION
        assert message["createSurface"]["surfaceId"] == "surface-1"
        assert message["createSurface"]["catalogId"]

    def test_create_surface_accepts_explicit_catalog_and_theme(self):
        message = genui.create_surface(
            "surface-1", catalog_id="custom-catalog", theme={"dark": True}, send_data_model=True
        )
        payload = message["createSurface"]
        assert payload["catalogId"] == "custom-catalog"
        assert payload["theme"] == {"dark": True}
        assert payload["sendDataModel"] is True

    def test_update_components_requires_root_component(self):
        with pytest.raises(ValueError, match="root"):
            genui.update_components("surface-1", [{"id": "not-root", "component": "Text"}])

    def test_update_components_accepts_root_component(self):
        components = [{"id": "root", "component": "Column", "children": []}]
        message = genui.update_components("surface-1", components)
        assert message["updateComponents"]["surfaceId"] == "surface-1"
        assert message["updateComponents"]["components"] == components

    def test_delete_surface(self):
        message = genui.delete_surface("surface-1")
        assert message == {"version": genui.A2UI_VERSION, "deleteSurface": {"surfaceId": "surface-1"}}


class TestBasicCatalogHelpers:
    def test_text_default_variant(self):
        assert genui.text("t1", "hello") == {
            "id": "t1",
            "component": "Text",
            "text": "hello",
            "variant": "body",
        }

    def test_column_omits_unset_optional_fields(self):
        component = genui.column("root", ["a", "b"])
        assert "justify" not in component
        assert "align" not in component
        assert component["children"] == ["a", "b"]

    def test_chart_builds_native_component_with_series_and_axes(self):
        component = genui.chart(
            "c1",
            "line",
            series=[{"name": "AAPL", "data": [{"value": 150.1}, {"value": 151.2}]}],
            x_axis=["09:30 AM", "10:00 AM"],
        )
        assert component == {
            "id": "c1",
            "component": "Chart",
            "chartType": "line",
            "data": {
                "series": [{"name": "AAPL", "data": [{"value": 150.1}, {"value": 151.2}]}],
                "xAxis": ["09:30 AM", "10:00 AM"],
            },
        }

    def test_chart_omits_unset_axes_and_styles(self):
        component = genui.chart("c1", "donut", series=[{"data": [{"value": 1, "label": "A"}]}])
        assert "xAxis" not in component["data"]
        assert "yAxis" not in component["data"]
        assert "styles" not in component

    def test_carousel_builds_component_payload(self):
        component = genui.carousel("media", ["https://example.com/a.jpg", "https://example.com/b.jpg"])
        assert component == {
            "id": "media",
            "component": "Carousel",
            "content": ["https://example.com/a.jpg", "https://example.com/b.jpg"],
        }

    def test_carousel_omits_unset_optional_fields(self):
        component = genui.carousel("media", ["https://example.com/a.jpg"])
        assert "autoplay" not in component
        assert "draggable" not in component
        assert "styles" not in component

    def test_summary_card_builds_create_and_update_pair(self):
        messages = genui.summary_card("surface-1", title="Title", body="Body")
        assert len(messages) == 2
        assert "createSurface" in messages[0]
        update = messages[1]["updateComponents"]
        ids = {c["id"] for c in update["components"]}
        assert ids == {"root", "content", "title", "divider", "body"}
        assert "linkButton" not in ids

    def test_summary_card_with_link_url_adds_open_url_button(self):
        messages = genui.summary_card(
            "surface-1", title="Title", body="Body", link_url="https://example.com/book", link_label="Continue"
        )
        components = {c["id"]: c for c in messages[1]["updateComponents"]["components"]}
        assert "linkButton" in components
        assert components["linkButton"]["action"]["functionCall"] == {
            "call": "openUrl",
            "args": {"url": "https://example.com/book"},
        }
        assert components["linkText"]["text"] == "Continue"

    def test_map_card_builds_create_and_update_pair(self):
        messages = genui.map_card("surface-1", "Bangkok", "https://example.com/map-embed?data=...")
        assert len(messages) == 2
        assert "createSurface" in messages[0]
        components = {c["id"]: c for c in messages[1]["updateComponents"]["components"]}
        assert components["map"]["component"] == "MapWeb"
        assert components["map"]["url"] == "https://example.com/map-embed?data=..."
        assert "caption" not in components

    def test_map_card_with_places_adds_horizontal_places_list(self):
        messages = genui.map_card(
            "surface-1",
            "Bangkok",
            "https://example.com/map-embed?data=...",
            places=[{"label": "Grand Palace", "rating": 4.6}],
        )
        components = {c["id"]: c for c in messages[1]["updateComponents"]["components"]}
        assert components["placesList"]["component"] == "List"
        assert components["placesList"]["direction"] == "horizontal"
        assert components["place0Name"]["text"] == "Grand Palace"
        root_children = next(c for c in messages[1]["updateComponents"]["components"] if c["id"] == "content")[
            "children"
        ]
        assert "placesList" in root_children

    def test_map_places_list_wires_tap_to_highlight_map_place_function(self):
        list_id, components = genui.map_places_list("surface-1", [{"label": "Grand Palace", "rating": 4.6}])
        assert list_id == "placesList"
        by_id = {c["id"]: c for c in components}
        assert by_id["place0Btn"]["action"] == {
            "functionCall": {"call": "highlightMapPlace", "args": {"surfaceId": "surface-1", "index": 0}}
        }
        assert by_id["place0Btn"]["child"] == "place0Card"
        assert by_id["place0Meta"]["text"] == "★ 4.6"

    def test_map_places_list_spaces_cards_via_spacer_component_not_gap_or_margin(self):
        # Regression test: neither "gap" on the List nor per-item "margin"
        # has any visible effect in this client -- confirmed on-device two
        # separate ways (gap: 0px-60px all rendered identically; margin: a
        # rebuilt debug client logged the *correct* margin-adjusted position
        # per card, yet the on-screen spacing didn't change). A real spacer
        # component between cards is the one thing consistently confirmed to
        # render at its requested width.
        _list_id, components = genui.map_places_list(
            "surface-1", [{"label": "Grand Palace"}, {"label": "Wat Arun"}, {"label": "Erawan Shrine"}]
        )
        by_id = {c["id"]: c for c in components}
        assert "gap" not in by_id["placesList"]["styles"]
        assert "margin" not in by_id["place0Btn"]["styles"]
        assert by_id["placesList"]["children"] == ["place0Btn", "spacer1", "place1Btn", "spacer2", "place2Btn"]
        assert by_id["spacer1"] == {"id": "spacer1", "component": "Column", "children": [], "styles": {"width": "6px"}}

    def test_map_places_list_keeps_every_card_row_present_when_data_missing(self):
        # Regression test: a card that skips a row entirely (e.g. no rating)
        # ends up shorter than its siblings, and the horizontal List's
        # stretch-to-equal-height then visually centers its content instead
        # of aligning it to the top like the rest of the row -- every card
        # must keep the same rows, blank where there's no data for them.
        _list_id, components = genui.map_places_list("surface-1", [{"label": "Wat Arun"}])
        by_id = {c["id"]: c for c in components}
        assert by_id["place0Meta"]["text"] == " "
        assert by_id["place0Status"]["text"] == " "
        assert by_id["place0Image"]["component"] == "Column"
        assert by_id["place0Image"]["children"] == []

    def test_map_places_list_image_fills_full_card_width(self):
        # Regression test: without an explicit width, the image only took
        # its own intrinsic/variant-driven width instead of the card's full
        # width, leaving a gap on one side instead of filling the card.
        _list_id, components = genui.map_places_list(
            "surface-1", [{"label": "Grand Palace", "image_url": "https://example.com/a.jpg"}]
        )
        by_id = {c["id"]: c for c in components}
        assert by_id["place0Image"]["component"] == "Image"
        assert by_id["place0Image"]["styles"]["width"] == "100%"
        assert by_id["place0Image"]["fit"] == "cover"
        # Card doesn't stretch its child to the card's own width by itself,
        # so the image's own width:100% is 100% of whatever this wrapping
        # column resolves to -- it must itself be pinned to the card's full
        # width, or the image ends up narrower than the card regardless.
        assert by_id["place0Content"]["styles"]["width"] == "100%"
        # No align="stretch" on this column -- that would also stretch the
        # text rows to the card's full width instead of their own natural
        # size; only elements that explicitly opt in (the image, above)
        # should stretch.
        assert "align" not in by_id["place0Content"]

    def test_map_web_builds_component_payload(self):
        component = genui.map_web("map", "https://example.com/map-embed?data=...")
        assert component == {"id": "map", "component": "MapWeb", "url": "https://example.com/map-embed?data=..."}

    def test_hotel_gallery_card_builds_create_and_update_pair(self):
        messages = genui.hotel_gallery_card(
            "surface-1",
            "Bali hotels",
            [
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
        )
        assert len(messages) == 2
        assert "createSurface" in messages[0]
        components = {c["id"]: c for c in messages[1]["updateComponents"]["components"]}
        assert components["hotel0Name"]["text"] == "The Ritz-Carlton, Bali"
        assert "$548/night" in components["hotel0Subtitle"]["text"]
        assert "★ 4.6" in components["hotel0Subtitle"]["text"]
        assert "5-star hotel" in components["hotel0Subtitle"]["text"]
        assert components["hotel0Media"]["component"] == "Carousel"
        assert components["hotel0Media"]["content"] == ["https://example.com/a.jpg", "https://example.com/b.jpg"]
        assert components["hotel0Desc"]["text"] == "Upscale property with a spa."
        assert components["hotel0Button"]["action"]["functionCall"] == {
            "call": "openUrl",
            "args": {"url": "https://example.com/ritz"},
        }
        assert components["hotel0ButtonText"]["text"] == "View Hotel"

    def test_hotel_gallery_card_renders_plain_image_for_a_single_photo(self):
        messages = genui.hotel_gallery_card(
            "surface-1", "Bali hotels", [{"name": "Solo Photo Hotel", "image_urls": ["https://example.com/only.jpg"]}]
        )
        components = {c["id"]: c for c in messages[1]["updateComponents"]["components"]}
        assert components["hotel0Media"]["component"] == "Image"
        assert components["hotel0Media"]["url"] == "https://example.com/only.jpg"

    def test_hotel_gallery_card_omits_optional_fields_when_absent(self):
        messages = genui.hotel_gallery_card("surface-1", "Bali hotels", [{"name": "Mystery Hotel"}])
        components = {c["id"]: c for c in messages[1]["updateComponents"]["components"]}
        assert components["hotel0Name"]["text"] == "Mystery Hotel"
        assert "hotel0Subtitle" not in components
        assert "hotel0Media" not in components
        assert "hotel0Desc" not in components
        assert "hotel0Button" not in components

    def test_hotel_gallery_card_adds_show_more_button_when_more_count_positive(self):
        messages = genui.hotel_gallery_card("surface-1", "Bali hotels", [{"name": "Hotel A"}], more_count=5)
        components = {c["id"]: c for c in messages[1]["updateComponents"]["components"]}
        assert components["moreButtonText"]["text"] == "Show more..."
        assert components["moreButton"]["action"]["event"]["name"] == "show_more_hotels"
        assert "moreButton" in messages[1]["updateComponents"]["components"][0]["children"]
        # A link, not a filled button -- "borderless" variant, transparent
        # background, and the label styled bold + underlined instead of the
        # white-on-brand-blue pill every other button in this app uses.
        assert components["moreButton"]["variant"] == "borderless"
        assert components["moreButton"]["styles"]["background-color"] == "transparent"
        assert components["moreButtonText"]["styles"]["font-weight"] == "bold"
        assert components["moreButtonText"]["styles"]["text-decoration"] == "underline"

    def test_hotel_gallery_card_omits_show_more_button_when_more_count_zero(self):
        messages = genui.hotel_gallery_card("surface-1", "Bali hotels", [{"name": "Hotel A"}])
        components = {c["id"]: c for c in messages[1]["updateComponents"]["components"]}
        assert "moreButton" not in components
        assert "moreButtonText" not in components

    def test_flight_gallery_card_builds_create_and_update_pair(self):
        messages = genui.flight_gallery_card(
            "surface-1",
            "Tokyo flights",
            [
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
        )
        assert len(messages) == 2
        assert "createSurface" in messages[0]
        components = {c["id"]: c for c in messages[1]["updateComponents"]["components"]}
        assert components["flight0Name"]["text"] == "Singapore Airlines"
        assert "$412" in components["flight0Subtitle"]["text"]
        assert "Nonstop" in components["flight0Subtitle"]["text"]
        assert "7h 30m" in components["flight0Subtitle"]["text"]
        assert components["flight0Logo"]["component"] == "Image"
        assert components["flight0Logo"]["url"] == "https://example.com/sq-logo.png"
        assert "SIN" in components["flight0Route"]["text"]
        assert "NRT" in components["flight0Route"]["text"]
        assert components["flight0Button"]["action"]["functionCall"] == {
            "call": "openUrl",
            "args": {"url": "https://www.google.com/travel/flights?q=test"},
        }
        assert components["flight0ButtonText"]["text"] == "View Flights"

    def test_flight_gallery_card_omits_logo_when_absent(self):
        messages = genui.flight_gallery_card("surface-1", "Tokyo flights", [{"airline": "Mystery Air"}])
        components = {c["id"]: c for c in messages[1]["updateComponents"]["components"]}
        assert "flight0Logo" not in components
        assert components["flight0Name"]["text"] == "Mystery Air"

    def test_flight_gallery_card_omits_optional_fields_when_absent(self):
        messages = genui.flight_gallery_card("surface-1", "Tokyo flights", [{"airline": "Mystery Air"}])
        components = {c["id"]: c for c in messages[1]["updateComponents"]["components"]}
        assert components["flight0Name"]["text"] == "Mystery Air"
        assert "flight0Subtitle" not in components
        assert "flight0Route" not in components
        assert "flight0Button" not in components

    def test_flight_gallery_card_adds_show_more_button_when_more_count_positive(self):
        messages = genui.flight_gallery_card("surface-1", "Tokyo flights", [{"airline": "Airline A"}], more_count=5)
        components = {c["id"]: c for c in messages[1]["updateComponents"]["components"]}
        assert components["moreButtonText"]["text"] == "Show more..."
        assert components["moreButton"]["action"]["event"]["name"] == "show_more_flights"
        assert "moreButton" in messages[1]["updateComponents"]["components"][0]["children"]
        assert components["moreButton"]["variant"] == "borderless"
        assert components["moreButtonText"]["styles"]["font-weight"] == "bold"
        assert components["moreButtonText"]["styles"]["text-decoration"] == "underline"

    def test_flight_gallery_card_omits_show_more_button_when_more_count_zero(self):
        messages = genui.flight_gallery_card("surface-1", "Tokyo flights", [{"airline": "Airline A"}])
        components = {c["id"]: c for c in messages[1]["updateComponents"]["components"]}
        assert "moreButton" not in components
        assert "moreButtonText" not in components

    def test_finance_gallery_card_builds_create_and_update_pair(self):
        messages = genui.finance_gallery_card(
            "surface-1",
            "Markets",
            [
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
                    "chart_x_axis": ["09:30 AM", "10:00 AM", "10:30 AM"],
                    "chart_values": [150.1, 151.2, 150.23],
                    "link": "https://www.google.com/finance/quote/AAPL:NASDAQ",
                }
            ],
        )
        assert len(messages) == 2
        assert "createSurface" in messages[0]
        components = {c["id"]: c for c in messages[1]["updateComponents"]["components"]}
        assert components["finance0Name"]["text"] == "Apple Inc (AAPL)"
        assert components["finance0Price"]["text"] == "$150.23"
        assert components["finance0Price"]["variant"] == "h2"
        assert components["finance0Change"]["text"] == "▲ +2.34 (+1.58%) today"
        assert components["finance0Change"]["styles"]["color"] == "#16A34A"
        assert components["finance0Chart"]["component"] == "Chart"
        assert components["finance0Chart"]["chartType"] == "line"
        assert components["finance0Chart"]["data"]["xAxis"] == ["09:30 AM", "10:00 AM", "10:30 AM"]
        assert components["finance0Chart"]["data"]["series"] == [
            {"name": "Apple Inc", "data": [{"value": 150.1}, {"value": 151.2}, {"value": 150.23}]}
        ]
        assert components["finance0Chart"]["styles"]["chartConfig"]["colors"] == ["#16A34A"]
        assert "NASDAQ" in components["finance0Meta"]["text"]
        assert components["finance0Desc"]["text"] == "Apple Inc. designs, manufactures, and markets smartphones."
        assert components["finance0Button"]["action"]["functionCall"] == {
            "call": "openUrl",
            "args": {"url": "https://www.google.com/finance/quote/AAPL:NASDAQ"},
        }
        assert components["finance0ButtonText"]["text"] == "View on Google Finance"

    def test_finance_gallery_card_colors_change_text_red_for_down_movement(self):
        messages = genui.finance_gallery_card(
            "surface-1", "Markets", [{"title": "Widget Co", "change_text": "-1.00 (-2%)", "movement": "Down"}]
        )
        components = {c["id"]: c for c in messages[1]["updateComponents"]["components"]}
        assert components["finance0Change"]["text"] == "▼ -1.00 (-2%)"
        assert components["finance0Change"]["styles"]["color"] == "#DC2626"

    def test_finance_gallery_card_omits_optional_fields_when_absent(self):
        messages = genui.finance_gallery_card("surface-1", "Markets", [{"title": "Mystery Corp"}])
        components = {c["id"]: c for c in messages[1]["updateComponents"]["components"]}
        assert components["finance0Name"]["text"] == "Mystery Corp"
        assert "finance0Price" not in components
        assert "finance0Change" not in components
        assert "finance0Chart" not in components
        assert "finance0Meta" not in components
        assert "finance0Desc" not in components
        assert "finance0Button" not in components

    def test_finance_gallery_card_name_omits_ticker_when_no_stock(self):
        messages = genui.finance_gallery_card("surface-1", "Markets", [{"title": "Mystery Corp"}])
        components = {c["id"]: c for c in messages[1]["updateComponents"]["components"]}
        assert components["finance0Name"]["text"] == "Mystery Corp"

    def test_shopping_gallery_card_builds_create_and_update_pair(self):
        messages = genui.shopping_gallery_card(
            "surface-1",
            "Headphones",
            [
                {
                    "title": "Sony WH-1000XM5",
                    "price": "$328.00",
                    "rating": 4.7,
                    "reviews": 12345,
                    "image_url": "https://example.com/headphones.jpg",
                    "link": "https://example.com/dp/B09XS7JWHH",
                    "is_prime": True,
                }
            ],
        )
        assert len(messages) == 2
        assert "createSurface" in messages[0]
        components = {c["id"]: c for c in messages[1]["updateComponents"]["components"]}
        assert components["product0Name"]["text"] == "Sony WH-1000XM5"
        assert "$328.00" in components["product0Subtitle"]["text"]
        assert "★ 4.7 (12,345 reviews)" in components["product0Subtitle"]["text"]
        assert "Prime" in components["product0Subtitle"]["text"]
        assert components["product0Media"]["component"] == "Image"
        assert components["product0Media"]["url"] == "https://example.com/headphones.jpg"
        assert components["product0Media"]["fit"] == "contain"
        assert components["product0Button"]["action"]["functionCall"] == {
            "call": "openUrl",
            "args": {"url": "https://example.com/dp/B09XS7JWHH"},
        }
        assert components["product0ButtonText"]["text"] == "Buy Now"

    def test_shopping_gallery_card_omits_optional_fields_when_absent(self):
        messages = genui.shopping_gallery_card("surface-1", "Headphones", [{"title": "Mystery Product"}])
        components = {c["id"]: c for c in messages[1]["updateComponents"]["components"]}
        assert components["product0Name"]["text"] == "Mystery Product"
        assert "product0Subtitle" not in components
        assert "product0Media" not in components
        assert "product0Button" not in components

    def test_shopping_gallery_card_adds_show_more_button_when_more_count_positive(self):
        messages = genui.shopping_gallery_card("surface-1", "Headphones", [{"title": "Product A"}], more_count=5)
        components = {c["id"]: c for c in messages[1]["updateComponents"]["components"]}
        assert components["moreButtonText"]["text"] == "Show more..."
        assert components["moreButton"]["action"]["event"]["name"] == "show_more_products"
        assert components["moreButton"]["variant"] == "borderless"

    def test_shopping_gallery_card_omits_show_more_button_when_more_count_zero(self):
        messages = genui.shopping_gallery_card("surface-1", "Headphones", [{"title": "Product A"}])
        components = {c["id"]: c for c in messages[1]["updateComponents"]["components"]}
        assert "moreButton" not in components
        assert "moreButtonText" not in components


class TestFormHelpers:
    def test_choice_picker_defaults(self):
        picker = genui.choice_picker("level", [("Beginner", "Beginner")])
        assert picker["options"] == [{"label": "Beginner", "value": "Beginner"}]
        assert picker["variant"] == "mutuallyExclusive"

    def test_button_builds_context_paths(self):
        btn = genui.button("submit", "label", "submit_action", context_paths={"level": "level.value"})
        assert btn["action"]["event"]["name"] == "submit_action"
        assert btn["action"]["event"]["context"] == {"level": {"path": "level.value"}}

    def test_open_url_button_uses_function_call_not_event(self):
        btn = genui.open_url_button("openBtn", "openText", "https://example.com")
        assert btn["action"] == {"functionCall": {"call": "openUrl", "args": {"url": "https://example.com"}}}
        assert "event" not in btn["action"]

    def test_form_builds_create_and_update_with_submit_button(self):
        fields = [genui.text_field("brand", label="Brand")]
        messages = genui.form(
            "surface-1",
            title="Preferences",
            fields=fields,
            submit_label="Submit",
            action_name="submit_prefs",
            field_paths={"brand": "brand.value"},
        )
        assert len(messages) == 2
        assert messages[0]["createSurface"]["sendDataModel"] is True
        component_ids = [c["id"] for c in messages[1]["updateComponents"]["components"]]
        # Single unnamed group ("Preferences") still wraps fields in a group card.
        assert component_ids == [
            "root",
            "title",
            "group0Card",
            "group0Content",
            "group0Title",
            "brand",
            "submitCard",
            "submit",
            "submitText",
        ]

    def test_form_groups_fields_by_category(self):
        fields = [
            genui.text_field("brand", label="Brand"),
            genui.text_field("budget", label="Budget"),
        ]
        messages = genui.form(
            "surface-1",
            title="Preferences",
            fields=fields,
            field_groups=[("Details", [fields[0]]), ("Budget", [fields[1]])],
            submit_label="Submit",
            action_name="submit_prefs",
            field_paths={"brand": "/brand/value", "budget": "/budget/value"},
        )
        component_ids = [c["id"] for c in messages[1]["updateComponents"]["components"]]
        assert "group0Card" in component_ids
        assert "group1Card" in component_ids
        titles = [c for c in messages[1]["updateComponents"]["components"] if c["id"] in ("group0Title", "group1Title")]
        assert {c["text"] for c in titles} == {"Details", "Budget"}

    def test_form_seeds_data_model_with_field_defaults(self):
        fields = [genui.text_field("brand", label="Brand")]
        messages = genui.form(
            "surface-1",
            title="Preferences",
            fields=fields,
            submit_label="Submit",
            action_name="submit_prefs",
            field_paths={"brand": "/brand/value"},
            field_defaults={"brand": "Yonex"},
        )
        # updateDataModel messages land between createSurface and updateComponents.
        assert messages[0]["createSurface"]
        assert messages[1]["updateDataModel"] == {"surfaceId": "surface-1", "path": "/brand/value", "value": "Yonex"}
        assert "updateComponents" in messages[-1]
