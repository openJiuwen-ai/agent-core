# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for openjiuwen.harness.a2ui.tools.uiux_tools.

Image/video-specific tools have their own test modules (test_image_tools.py,
test_video_tools.py) alongside their own source modules (image_tools.py,
video_tools.py) -- this file covers only the tools that stay directly in
uiux_tools.py.
"""

import pytest

from openjiuwen.harness.a2ui.tools import uiux_tools as tools


class TestGetCurrentTime:
    @pytest.mark.asyncio
    async def test_returns_utc_formatted_string(self):
        result = await tools.get_current_time.invoke({})
        assert result.endswith("UTC")
        assert len(result) == len("2025-01-01 00:00 UTC")


class TestShowCard:
    @pytest.mark.asyncio
    async def test_returns_text_and_genui_messages(self):
        result = await tools.show_card.invoke({"title": "Title", "body": "Body"})
        assert result["text"] == "Body"
        assert len(result["genui"]) == 2
        assert "createSurface" in result["genui"][0]
        assert "updateComponents" in result["genui"][1]

    @pytest.mark.asyncio
    async def test_link_url_adds_open_url_button(self):
        result = await tools.show_card.invoke(
            {"title": "Title", "body": "Body", "link_url": "https://example.com/book", "link_label": "Continue"}
        )
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert components["linkButton"]["action"]["functionCall"]["args"]["url"] == "https://example.com/book"


class TestShowInfoList:
    @pytest.mark.asyncio
    async def test_returns_text_and_genui_messages(self):
        result = await tools.show_info_list.invoke(
            {"title": "Steps", "items": [{"title": "First"}, {"title": "Second", "subtitle": "details"}]}
        )
        assert "First" in result["text"]
        assert "Second" in result["text"]
        assert len(result["genui"]) == 2


class TestAskPreferencesForm:
    @pytest.mark.asyncio
    async def test_returns_form_with_requested_fields(self):
        result = await tools.ask_preferences_form.invoke(
            {
                "title": "Racket preferences",
                "fields": [
                    {"id": "level", "type": "choice", "label": "Level", "options": [{"label": "Pro", "value": "pro"}]},
                    {
                        "id": "budget",
                        "type": "choice",
                        "label": "Budget",
                        "options": [{"label": "$30-$100", "value": "30-100"}],
                    },
                ],
            }
        )
        assert "genui" in result
        component_ids = {c["id"] for c in result["genui"][-1]["updateComponents"]["components"]}
        assert {"level", "budget"} <= component_ids

    @pytest.mark.asyncio
    async def test_hotel_title_auto_inserts_stay_date_fields(self):
        result = await tools.ask_preferences_form.invoke(
            {
                "title": "Hotel booking preferences",
                "fields": [
                    {
                        "id": "budget",
                        "type": "choice",
                        "label": "Budget",
                        "options": [{"label": "$0-$1000", "value": "0-1000"}],
                    }
                ],
            }
        )
        component_ids = {c["id"] for c in result["genui"][-1]["updateComponents"]["components"]}
        assert {"check_in", "check_out"} <= component_ids

    @pytest.mark.asyncio
    async def test_flight_title_auto_inserts_travel_date_fields(self):
        result = await tools.ask_preferences_form.invoke(
            {
                "title": "Flight booking preferences",
                "fields": [
                    {"id": "adults", "type": "choice", "label": "Adults", "options": [{"label": "1", "value": "1"}]}
                ],
            }
        )
        component_ids = {c["id"] for c in result["genui"][-1]["updateComponents"]["components"]}
        assert {"outbound_date", "return_date"} <= component_ids
        assert "check_in" not in component_ids

    @pytest.mark.asyncio
    async def test_flight_title_does_not_also_insert_hotel_stay_date_fields(self):
        result = await tools.ask_preferences_form.invoke({"title": "Flight booking preferences", "fields": []})
        component_ids = {c["id"] for c in result["genui"][-1]["updateComponents"]["components"]}
        assert {"check_in", "check_out"}.isdisjoint(component_ids)

    @pytest.mark.asyncio
    async def test_hotel_stay_dates_render_side_by_side_in_correct_order(self):
        result = await tools.ask_preferences_form.invoke({"title": "Hotel booking preferences", "fields": []})
        components = result["genui"][-1]["updateComponents"]["components"]
        component_ids = [c["id"] for c in components]
        # Regression test: the auto-insert loop used to insert(0, ...) each
        # date individually, which reverses their order (the second insert
        # pushes the first one down a slot) -- check-in must render before
        # check-out, not after.
        assert component_ids.index("check_in") < component_ids.index("check_out")
        # A compact two-column row (one shared card), not two separate
        # full-width cards -- each date's own caption (not a card-level h4
        # heading) is what's visible now.
        by_id = {c["id"]: c for c in components}
        assert by_id["datesRow"]["component"] == "Row"
        assert by_id["datesRow"]["children"] == ["check_inCol", "check_outCol"]
        caption_texts = [c["text"] for c in components if c.get("variant") == "caption"]
        assert "Check-in date" in caption_texts
        assert "Check-out date" in caption_texts

    @pytest.mark.asyncio
    async def test_flight_travel_dates_render_side_by_side_in_correct_order(self):
        result = await tools.ask_preferences_form.invoke({"title": "Flight booking preferences", "fields": []})
        components = result["genui"][-1]["updateComponents"]["components"]
        component_ids = [c["id"] for c in components]
        assert component_ids.index("outbound_date") < component_ids.index("return_date")
        # Compact two-column row (same treatment as the hotel form), not two
        # separate full-width cards -- each date's own caption (not a
        # card-level h4 heading) is what's visible now.
        by_id = {c["id"]: c for c in components}
        assert by_id["datesRow"]["component"] == "Row"
        assert by_id["datesRow"]["children"] == ["outbound_dateCol", "return_dateCol"]
        caption_texts = [c["text"] for c in components if c.get("variant") == "caption"]
        assert "Departure date" in caption_texts
        assert "Return date" in caption_texts

    @pytest.mark.asyncio
    async def test_flight_guest_count_fields_render_side_by_side_as_number_inputs(self):
        result = await tools.ask_preferences_form.invoke({"title": "Flight booking preferences", "fields": []})
        components = result["genui"][-1]["updateComponents"]["components"]
        by_id = {c["id"]: c for c in components}
        assert by_id["guestsRow"]["component"] == "Row"
        assert by_id["guestsRow"]["children"] == ["adultsCol", "childrenCol"]
        assert by_id["adults"]["component"] == "TextField"
        assert by_id["adults"]["variant"] == "number"
        assert by_id["adults"]["value"] == "1"
        assert by_id["children"]["value"] == "0"
        assert "roomsCol" not in by_id

    @pytest.mark.asyncio
    async def test_purely_chinese_hotel_title_still_auto_inserts_stay_date_fields(self):
        # Regression test: a title generated entirely in Chinese, with no
        # English word at all (e.g. the model titling a form "预订酒店" for a
        # "我想订酒店" request), must still trigger the same auto-insert as an
        # English "hotel" title -- the keyword list isn't English-only.
        result = await tools.ask_preferences_form.invoke({"title": "预订酒店", "fields": []})
        component_ids = {c["id"] for c in result["genui"][-1]["updateComponents"]["components"]}
        assert {"check_in", "check_out"} <= component_ids

    @pytest.mark.asyncio
    async def test_purely_chinese_flight_title_still_auto_inserts_travel_date_fields(self):
        result = await tools.ask_preferences_form.invoke({"title": "预订机票", "fields": []})
        component_ids = {c["id"] for c in result["genui"][-1]["updateComponents"]["components"]}
        assert {"outbound_date", "return_date"} <= component_ids
        assert "check_in" not in component_ids

    @pytest.mark.asyncio
    async def test_bus_title_auto_inserts_departure_return_fields(self):
        result = await tools.ask_preferences_form.invoke({"title": "Bus ticket booking", "fields": []})
        component_ids = {c["id"] for c in result["genui"][-1]["updateComponents"]["components"]}
        assert {"departure_date", "return_date"} <= component_ids
        assert {"check_in", "check_out"}.isdisjoint(component_ids)

    @pytest.mark.asyncio
    async def test_purely_chinese_bus_title_auto_inserts_departure_return_fields(self):
        # Regression test for the reported bug: a round-trip bus ticket
        # request ("我想买来回的巴士票") titled entirely in Chinese as
        # "预订巴士票" used to match the hotel branch on the generic "预订"
        # (book/reserve) substring, bolting check_in/check_out onto the
        # form's own correctly-labeled departure_date/return_date fields --
        # four date fields shown to the user instead of two.
        result = await tools.ask_preferences_form.invoke({"title": "预订巴士票", "fields": []})
        component_ids = {c["id"] for c in result["genui"][-1]["updateComponents"]["components"]}
        assert {"departure_date", "return_date"} <= component_ids
        assert {"check_in", "check_out"}.isdisjoint(component_ids)

    @pytest.mark.asyncio
    async def test_generic_booking_word_alone_no_longer_triggers_hotel_fields(self):
        # "booking"/"预订"/"预定" alone are shared across every domain and
        # must not be sufficient on their own to identify a hotel form.
        for title in ("Booking preferences", "预订", "预定详情"):
            result = await tools.ask_preferences_form.invoke({"title": title, "fields": []})
            component_ids = {c["id"] for c in result["genui"][-1]["updateComponents"]["components"]}
            assert {"check_in", "check_out"}.isdisjoint(component_ids), title

    @pytest.mark.asyncio
    async def test_bus_travel_dates_get_separate_categories_in_correct_order(self):
        result = await tools.ask_preferences_form.invoke({"title": "Bus ticket booking", "fields": []})
        components = result["genui"][-1]["updateComponents"]["components"]
        component_ids = [c["id"] for c in components]
        assert component_ids.index("departure_date") < component_ids.index("return_date")
        category_titles = [c["text"] for c in components if c.get("variant") == "h4"]
        assert "Departure date" in category_titles
        assert "Return date" in category_titles

    @pytest.mark.asyncio
    async def test_chinese_bus_title_gets_chinese_date_labels(self):
        # Regression test: a form titled entirely in Chinese must not end up
        # with English "Departure date"/"Return date" labels while every
        # other field on the same form (which the model wrote itself) is in
        # Chinese.
        result = await tools.ask_preferences_form.invoke({"title": "往返巴士票预订", "fields": []})
        components = result["genui"][-1]["updateComponents"]["components"]
        category_titles = [c["text"] for c in components if c.get("variant") == "h4"]
        assert "出发日期" in category_titles
        assert "返程日期" in category_titles
        assert "Departure date" not in category_titles
        assert "Return date" not in category_titles

    @pytest.mark.asyncio
    async def test_chinese_flight_title_gets_chinese_date_labels(self):
        result = await tools.ask_preferences_form.invoke({"title": "预订机票", "fields": []})
        components = result["genui"][-1]["updateComponents"]["components"]
        caption_texts = [c["text"] for c in components if c.get("variant") == "caption"]
        assert "出发日期" in caption_texts
        assert "返程日期" in caption_texts
        assert "Departure date" not in caption_texts

    @pytest.mark.asyncio
    async def test_chinese_hotel_title_gets_chinese_date_labels(self):
        result = await tools.ask_preferences_form.invoke({"title": "预订酒店", "fields": []})
        components = result["genui"][-1]["updateComponents"]["components"]
        caption_texts = [c["text"] for c in components if c.get("variant") == "caption"]
        assert "入住日期" in caption_texts
        assert "退房日期" in caption_texts
        assert "Check-in date" not in caption_texts

    @pytest.mark.asyncio
    async def test_english_titles_keep_english_date_labels(self):
        for title, expected in (("Bus ticket booking", ("Departure date", "Return date")),):
            result = await tools.ask_preferences_form.invoke({"title": title, "fields": []})
            components = result["genui"][-1]["updateComponents"]["components"]
            category_titles = [c["text"] for c in components if c.get("variant") == "h4"]
            for label in expected:
                assert label in category_titles, (title, label)
        # Hotel and flight dates render as compact-row captions, not h4
        # category headings -- see
        # test_hotel_stay_dates_render_side_by_side_in_correct_order and
        # test_flight_travel_dates_render_side_by_side_in_correct_order.
        for title, expected in (
            ("Hotel booking preferences", ("Check-in date", "Check-out date")),
            ("Flight booking preferences", ("Departure date", "Return date")),
        ):
            result = await tools.ask_preferences_form.invoke({"title": title, "fields": []})
            components = result["genui"][-1]["updateComponents"]["components"]
            caption_texts = [c["text"] for c in components if c.get("variant") == "caption"]
            for label in expected:
                assert label in caption_texts, (title, label)

    @pytest.mark.asyncio
    async def test_hotel_title_auto_inserts_localized_guest_count_fields(self):
        # Regression test for the reported bug: a Chinese hotel request
        # ("我想订酒店") must get "成人"/"儿童" captions, not English
        # "Adults"/"Children" -- these are now code-inserted the same
        # deterministic way as check_in/check_out, not left to the model.
        result = await tools.ask_preferences_form.invoke({"title": "预订酒店", "fields": []})
        components = result["genui"][-1]["updateComponents"]["components"]
        component_ids = {c["id"] for c in components}
        assert {"adults", "children", "rooms"} <= component_ids
        caption_texts = [c["text"] for c in components if c.get("variant") == "caption"]
        assert "成人" in caption_texts
        assert "儿童" in caption_texts
        assert "房间数" in caption_texts
        assert "Adults" not in caption_texts
        assert "Children" not in caption_texts

    @pytest.mark.asyncio
    async def test_english_hotel_title_gets_english_guest_count_labels(self):
        result = await tools.ask_preferences_form.invoke({"title": "Hotel booking preferences", "fields": []})
        components = result["genui"][-1]["updateComponents"]["components"]
        caption_texts = [c["text"] for c in components if c.get("variant") == "caption"]
        assert "Adults" in caption_texts
        assert "Children" in caption_texts
        assert "Rooms" in caption_texts

    @pytest.mark.asyncio
    async def test_hotel_guest_count_fields_render_side_by_side_as_number_inputs(self):
        result = await tools.ask_preferences_form.invoke({"title": "Hotel booking preferences", "fields": []})
        components = result["genui"][-1]["updateComponents"]["components"]
        by_id = {c["id"]: c for c in components}
        assert by_id["guestsRow"]["component"] == "Row"
        assert by_id["guestsRow"]["children"] == ["adultsCol", "childrenCol", "roomsCol"]
        assert by_id["adults"]["component"] == "TextField"
        assert by_id["adults"]["variant"] == "number"
        assert by_id["adults"]["value"] == "2"
        assert by_id["children"]["value"] == "0"
        assert by_id["rooms"]["value"] == "1"

    @pytest.mark.asyncio
    async def test_flight_title_auto_inserts_localized_guest_count_fields(self):
        result = await tools.ask_preferences_form.invoke({"title": "预订机票", "fields": []})
        components = result["genui"][-1]["updateComponents"]["components"]
        component_ids = {c["id"] for c in components}
        assert {"adults", "children"} <= component_ids
        assert "rooms" not in component_ids
        caption_texts = [c["text"] for c in components if c.get("variant") == "caption"]
        assert "成人" in caption_texts
        assert "儿童" in caption_texts
        assert "Adults" not in caption_texts

    @pytest.mark.asyncio
    async def test_transport_form_does_not_insert_guest_count_fields(self):
        # search_hotels/search_flights need structured adults/children ints;
        # there's no dedicated transport search tool, so the general flow
        # handles passenger count itself -- no auto-insert for these here.
        result = await tools.ask_preferences_form.invoke({"title": "预订巴士票", "fields": []})
        component_ids = {c["id"] for c in result["genui"][-1]["updateComponents"]["components"]}
        assert {"adults", "children"}.isdisjoint(component_ids)

    @pytest.mark.asyncio
    async def test_model_authored_hotel_fields_are_discarded_and_rebuilt(self):
        # Regression test for the reported bug's second half: when the model
        # pre-populates check_in/adults itself (instead of leaving it to the
        # auto-insert) with a bilingual/English label -- observed live as
        # "Check-in date 入住日期" -- that must never reach the screen. The
        # compact-row builder always discards any model-supplied
        # check_in/check_out/adults/children/rooms field and rebuilds all
        # five from scratch, rather than trying to merge/override whatever
        # the model wrote.
        result = await tools.ask_preferences_form.invoke(
            {
                "title": "预订酒店",
                "fields": [
                    {
                        "id": "check_in",
                        "type": "date",
                        "label": "Check-in date 入住日期",
                        "category": "Check-in date 入住日期",
                    },
                    {
                        "id": "adults",
                        "type": "choice",
                        "label": "Adults 成人",
                        "category": "Adults 成人",
                        "options": [{"label": "2", "value": "2"}],
                    },
                ],
            }
        )
        components = result["genui"][-1]["updateComponents"]["components"]
        by_id = {c["id"]: c for c in components}
        assert by_id["check_in"]["component"] == "DateTimeInput"
        assert by_id["adults"]["component"] == "TextField"
        caption_texts = [c["text"] for c in components if c.get("variant") == "caption"]
        assert "入住日期" in caption_texts
        assert "成人" in caption_texts
        assert not any("Check-in" in t or "Adults" in t for t in caption_texts)


class TestAllTools:
    def test_all_tools_exposes_expected_names(self):
        names = {t.card.name for t in tools.ALL_TOOLS}
        assert names == {
            "get_current_time",
            "show_card",
            "show_info_list",
            "ask_preferences_form",
            "fetch_page_image",
            "search_images",
            "search_youtube_videos",
            "fetch_video_source",
            "show_video_clips",
            "geocode_place",
            "show_map",
            "search_hotels",
            "show_hotel_results",
            "search_flights",
            "show_flight_results",
            "search_finance",
            "show_finance_results",
            "search_products",
            "show_shopping_results",
            "search_weather_forecast",
            "show_weather_forecast",
            "search_weather_history",
            "show_weather_history",
        }
