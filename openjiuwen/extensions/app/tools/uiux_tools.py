# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tools available to the ReAct agent.

``show_card``'s return value (``{"text": ..., "genui": [...]}``) is how A2UI
messages reach the WebSocket layer: ``rails.A2uiToolEventRail`` captures the
raw dict from ``AFTER_TOOL_CALL`` and ``ws_session._translate`` turns the
``genui`` list into one WebSocket ``genui`` event per message.

Image, video, map, hotel, flight, and finance tools live in their own
modules (``image_tools.py``, ``video_tools.py``, ``map_tools.py``,
``hotel_tools.py``, ``flight_tools.py``, ``finance_tools.py``) -- imported
back in here just to keep ``ALL_TOOLS`` as the single place that assembles
everything the agent gets.
"""

import re
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from openjiuwen.core.foundation.tool import tool

from .. import genui
from .finance_tools import search_finance, show_finance_results
from .flight_tools import search_flights, show_flight_results
from .hotel_tools import search_hotels, show_hotel_results
from .image_tools import fetch_page_image, search_images
from .map_tools import geocode_place, show_map
from .video_tools import fetch_video_source, search_youtube_videos, show_video_clips

_CJK_RE = re.compile(r"[一-鿿]")


def _is_chinese_title(title: str) -> bool:
    """True if `title` contains any Chinese (CJK) characters.

    Used to pick Chinese vs. English labels for the date/guest-count fields
    `ask_preferences_form` auto-inserts below, so a form titled entirely in
    Chinese (e.g. "预订巴士票", with no English gloss) doesn't end up with
    English "Departure date"/"Return date"/"Adults"/"Children" labels while
    every other field on the same form -- the ones the model wrote itself --
    is in Chinese.
    """
    return bool(_CJK_RE.search(title))


def _compact_dates_and_guests_rows(
    title: str,
    parsed_fields: list["FormField"],
    date_fields: tuple[tuple[str, str, str], tuple[str, str, str]],
    guest_fields: list[tuple[str, str, str, str]],
) -> tuple[list[tuple[str, list[dict[str, Any]]]], list[dict[str, Any]], dict[str, str], dict[str, Any]]:
    """Build a compact two-field dates row and an N-field guest-count row
    (matching a typical hotel/flight search widget) instead of each field
    getting its own full-width card the way every other auto-inserted field
    in this file does. Shared by the hotel and flight branches below --
    they differ only in field ids/labels/defaults, not in layout.

    `date_fields` is ``((id, english_label, chinese_label), (id, ..., ...))``
    for the two date fields (in display order); `guest_fields` is a list of
    ``(id, english_label, chinese_label, default_value)`` for the guest-count
    row (2 fields for a flight, 3 -- including rooms -- for a hotel).

    Strips any FormField the model already added for these ids from
    `parsed_fields` -- they always render this compact way; the generic
    per-category loop below has no way to lay fields out side by side, so
    they never go through it.

    Returns (field_groups, extra_components, field_paths, field_defaults)
    for the caller to merge into `genui.form()`'s own arguments -- see
    `genui.form`'s docstring for why the row's own children need to travel
    separately as `extra_components` rather than as ordinary group fields.
    """
    auto_ids = {date_fields[0][0], date_fields[1][0]} | {f[0] for f in guest_fields}
    parsed_fields[:] = [f for f in parsed_fields if f.id not in auto_ids]

    chinese = _is_chinese_title(title)
    today = datetime.now(timezone.utc).date()
    default_date1 = today.isoformat()
    default_date2 = (today + timedelta(days=1)).isoformat()

    extra_components: list[dict[str, Any]] = []
    field_paths: dict[str, str] = {}
    field_defaults: dict[str, Any] = {}

    def _mini_field(field_id: str, caption: str, widget: dict[str, Any]) -> str:
        caption_id = f"{field_id}Caption"
        col_id = f"{field_id}Col"
        col = genui.column(col_id, [caption_id, field_id], styles={"gap": "4px"})
        col["weight"] = 1  # share the row's width equally with its siblings
        extra_components.append(col)
        extra_components.append(genui.text(caption_id, caption, variant="caption"))
        extra_components.append(widget)
        field_paths[field_id] = f"/{field_id}/value"
        return col_id

    (date1_id, date1_en, date1_zh), (date2_id, date2_en, date2_zh) = date_fields
    date1_col = _mini_field(
        date1_id,
        date1_zh if chinese else date1_en,
        genui.date_input(date1_id, value=default_date1, min_date=default_date1),
    )
    date2_col = _mini_field(
        date2_id,
        date2_zh if chinese else date2_en,
        genui.date_input(date2_id, value=default_date2, min_date=default_date1),
    )
    field_defaults[date1_id] = default_date1
    field_defaults[date2_id] = default_date2
    dates_row = genui.row("datesRow", [date1_col, date2_col], styles={"gap": "12px"})

    guest_cols = []
    for field_id, en_label, zh_label, default_value in guest_fields:
        guest_cols.append(
            _mini_field(
                field_id,
                zh_label if chinese else en_label,
                genui.text_field(field_id, value=default_value, variant="number"),
            )
        )
        field_defaults[field_id] = default_value
    guests_row = genui.row("guestsRow", guest_cols, styles={"gap": "12px"})

    field_groups = [("", [dates_row]), ("", [guests_row])]
    return field_groups, extra_components, field_paths, field_defaults


def _hotel_compact_search_rows(
    title: str, parsed_fields: list["FormField"]
) -> tuple[list[tuple[str, list[dict[str, Any]]]], list[dict[str, Any]], dict[str, str], dict[str, Any]]:
    return _compact_dates_and_guests_rows(
        title,
        parsed_fields,
        date_fields=(("check_in", "Check-in date", "入住日期"), ("check_out", "Check-out date", "退房日期")),
        guest_fields=[
            ("adults", "Adults", "成人", "2"),
            ("children", "Children", "儿童", "0"),
            ("rooms", "Rooms", "房间数", "1"),
        ],
    )


def _flight_compact_search_rows(
    title: str, parsed_fields: list["FormField"]
) -> tuple[list[tuple[str, list[dict[str, Any]]]], list[dict[str, Any]], dict[str, str], dict[str, Any]]:
    return _compact_dates_and_guests_rows(
        title,
        parsed_fields,
        date_fields=(("outbound_date", "Departure date", "出发日期"), ("return_date", "Return date", "返程日期")),
        guest_fields=[
            ("adults", "Adults", "成人", "1"),
            ("children", "Children", "儿童", "0"),
        ],
    )


@tool(
    description=(
        "Get the current UTC date and time. Call this first if the user's request "
        "depends on knowing what time or day it is."
    )
)
def get_current_time() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _safe_icon(name: Optional[str]) -> Optional[str]:
    """Drop an icon name the LLM hallucinated outside ``genui.ICON_NAMES``.

    The client validates every component against its catalog schema and
    silently drops ones that fail (logs a warning, no error surfaced) --
    see genui's SurfaceController._handleCoreMessage. An invalid icon name
    would otherwise take the whole card/list down with it; dropping just
    the icon here degrades gracefully instead.
    """
    if name in genui.ICON_NAMES:
        return name
    return None


_ICON_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("hotel", "room", "accommodation", "stay", "guesthouse", "resort"), "home"),
    (("restaurant", "food", "dish", "meal", "cuisine", "cafe", "hawker"), "favorite"),
    (("location", "place", "address", "area", "near", "distance", "map"), "locationOn"),
    (("date", "day", "time", "schedule", "check-in", "check-out", "booking"), "calendarToday"),
    (("price", "budget", "cost", "payment", "fee", "rm"), "payment"),
    (("warning", "caution", "risk", "alert"), "warning"),
    (("phone", "call", "contact"), "phone"),
    (("photo", "image", "picture"), "photo"),
    (("step", "tip", "recommend", "highlight", "must-see"), "star"),
)


def _suggest_icon(*texts: Optional[str]) -> Optional[str]:
    """Choose a supported visual cue from the content when the model omits one."""
    searchable = " ".join(text for text in texts if text).lower()
    for keywords, icon_name in _ICON_HINTS:
        if any(keyword in searchable for keyword in keywords):
            return icon_name
    return None


@tool(
    description=(
        "Render a small titled card of information as an A2UI surface on the "
        "user's screen. Call this once you have something worth showing — a "
        "summary, an answer, a short paragraph of prose. For several discrete "
        "items (a list, steps, an itinerary) use `show_info_list` instead -- it "
        "reads far better than one long paragraph. The card is shown alongside "
        "your text reply, it does not replace it. Do not call this more than "
        "once per user request. `icon` is optional -- one of: " + ", ".join(genui.ICON_NAMES) + ". "
        "`image_url` is optional -- a real, publicly reachable http(s) image URL "
        "to show as a header image above the title. Get one from `search_images` "
        "(preferred), `fetch_page_image`, or `browser_inspect_page`; do not type one "
        "from memory, it will almost always be wrong. "
        "`link_url`/`link_label` are optional -- add these to hand the user off to a real "
        "website (opens externally, in their own browser) to finish something there "
        "themselves, e.g. completing a booking on the real site you inspected with "
        "`browser_inspect_page`. Never fabricate a `link_url`; only use a URL you actually "
        "navigated to."
    )
)
def show_card(
    title: str,
    body: str,
    icon: Optional[str] = None,
    image_url: Optional[str] = None,
    link_url: Optional[str] = None,
    link_label: Optional[str] = None,
) -> dict[str, Any]:
    surface_id = genui.new_surface_id("card")
    return {
        "text": body,
        "genui": genui.summary_card(
            surface_id,
            title=title,
            body=body,
            icon_name=_safe_icon(icon) or _suggest_icon(title, body),
            image_url=image_url,
            link_url=link_url,
            link_label=link_label,
        ),
    }


class InfoListItem(BaseModel):
    title: str = Field(description="The item's main text.")
    subtitle: Optional[str] = Field(default=None, description="Optional smaller secondary text below the title.")
    icon: Optional[str] = Field(
        default=None, description=f"Optional leading icon, one of: {', '.join(genui.ICON_NAMES)}"
    )
    image_url: Optional[str] = Field(
        default=None,
        description=(
            "Optional real, publicly reachable http(s) image URL shown instead of "
            "the icon for this item. Get one from `search_images` (preferred) or "
            "`fetch_page_image`; do not type one from memory, it will almost always "
            "be wrong."
        ),
    )


_DEFAULT_ITEM_ICON = "check"


def _item_icon(item: InfoListItem) -> Optional[str]:
    if item.image_url:
        return None  # image takes precedence -- info_list_card ignores icon when set
    # Every item needs *some* leading element, or its Row has one fewer
    # child than its siblings and its text starts flush against the card
    # edge instead of aligned with the icon column -- a hallucinated or
    # omitted icon must still fall back to something, not disappear.
    return _safe_icon(item.icon) or _suggest_icon(item.title, item.subtitle) or _DEFAULT_ITEM_ICON


@tool(
    description=(
        "Render a titled card containing a clean vertical list of items (each with "
        "an optional icon or image, a title, and an optional subtitle) as an A2UI "
        "surface. Use this instead of `show_card` whenever the content is naturally "
        "several discrete entries -- an itinerary, a step-by-step guide, a "
        "medication schedule, a feature/amenity list -- rather than one paragraph "
        "of prose. Do not call this more than once per user request."
    )
)
def show_info_list(title: str, items: list[InfoListItem], icon: Optional[str] = None) -> dict[str, Any]:
    surface_id = genui.new_surface_id("list")
    parsed_items = [i if isinstance(i, InfoListItem) else InfoListItem(**i) for i in items]
    summary = "\n".join(f"- {i.title}" + (f" — {i.subtitle}" if i.subtitle else "") for i in parsed_items)
    genui_messages = genui.info_list_card(
        surface_id,
        title=title,
        items=[(_item_icon(i), i.title, i.subtitle, i.image_url) for i in parsed_items],
        icon_name=_safe_icon(icon) or _suggest_icon(title),
    )
    return {"text": f"{title}\n{summary}", "genui": genui_messages}


class FormFieldType(str, Enum):
    choice = "choice"
    multi_choice = "multi_choice"
    slider = "slider"
    text = "text"
    checkbox = "checkbox"
    date = "date"


class FormFieldOption(BaseModel):
    label: str = Field(description="Text shown to the user for this option.")
    value: str = Field(description="Stable value submitted for this option.")


class FormField(BaseModel):
    id: str = Field(description="Unique field id (letters/digits/underscore only), e.g. 'budget'.")
    category: Optional[str] = Field(
        default=None,
        description=(
            "Short section heading for this field -- this heading is the field's "
            "real, reliably-visible label (a field's own `label` value is not "
            "shown by this client's input widgets), so give every field its own "
            "specific category matching what it actually is, e.g. category='Adults' "
            "on the adults field, category='Check-in date' on the check-in field. "
            "Never give two different fields the same shared/umbrella category "
            "(like 'Guests' for both an adults field and a children field, or "
            "'Stay dates' for both a check-in and a check-out field) -- fields in "
            "the same category are visually merged under one heading in one card, "
            "so sharing one hides which value is which just as much as combining "
            "them into a single field would. One field, one category, always."
        ),
    )
    type: FormFieldType = Field(
        description=(
            "choice = single-select radio buttons (pick exactly one); "
            "multi_choice = checkboxes (pick any number, including zero) -- use this "
            "whenever the user could reasonably want more than one option, e.g. "
            "'which cuisines do you like', 'which amenities matter to you'; "
            "slider = a numeric range where the exact value matters less than the "
            "position within a wide range, e.g. a price/budget range -- do NOT use "
            "it for a small discrete count like a number of guests/adults/"
            "children/passengers, since a slider knob doesn't let the user see or "
            "set the exact number precisely; use `choice` with explicit numbered "
            "options instead (e.g. '1', '2', '3', '4+') for those; "
            "text = free text; checkbox = a single yes/no toggle; "
            "date = date-only calendar picker (use for check-in, check-out, booking, or travel dates)."
        )
    )
    label: str = Field(
        description=(
            "Label shown above/next to the field. For type=date, this must specifically "
            "name which date it is -- 'Check-in date', 'Check-out date', 'Departure date', "
            "'Return date', 'Appointment date', etc. -- never a bare 'Date' the user has to "
            "guess the meaning of."
        )
    )
    help_text: Optional[str] = Field(
        default=None,
        description=(
            "Short explanation shown below the field. Required for sliders: say "
            "what the setting controls and what low versus high values mean."
        ),
    )
    options: Optional[list[FormFieldOption]] = Field(
        default=None, description="Required for type=choice/multi_choice: the selectable options."
    )
    default_option_value: Optional[str] = Field(
        default=None, description="For type=choice: the value of the option pre-selected."
    )
    default_option_values: Optional[list[str]] = Field(
        default=None, description="For type=multi_choice: the values pre-selected."
    )
    min_value: Optional[float] = Field(default=None, description="For type=slider: minimum value.")
    max_value: Optional[float] = Field(default=None, description="For type=slider: maximum value.")
    default_number: Optional[float] = Field(default=None, description="For type=slider: starting value.")
    default_text: str = Field(default="", description="For type=text: starting value.")
    default_checked: bool = Field(default=False, description="For type=checkbox: starting checked state.")
    default_date: Optional[str] = Field(default=None, description="For type=date: starting date in YYYY-MM-DD format.")
    min_date: Optional[str] = Field(
        default=None, description="For type=date: earliest selectable date in YYYY-MM-DD format."
    )
    max_date: Optional[str] = Field(
        default=None, description="For type=date: latest selectable date in YYYY-MM-DD format."
    )


@tool(
    description=(
        "Show an interactive preferences form on the user's screen, instead of asking "
        "several questions one at a time in plain text. Not specific to any topic -- use "
        "it any time you need a handful of structured inputs before you can give a good "
        "answer (choosing/buying something, planning something, configuring something, "
        "etc.). You decide the title and the fields yourself, based on what information "
        "you actually need for the request at hand. Every distinct piece of information "
        "is its own field, in its own category -- never merge two different preferences "
        "into one field or combine their options together, and never give two different "
        "fields the same shared category (see FormField.category). For example, "
        "'adults' and 'children' are two separate counts: use two separate `choice` "
        "fields with explicit numbered options (e.g. '1'/'2'/'3'/'4+'), each in its own "
        "category ('Adults', 'Children') -- never one field whose options are combined "
        "pairs like '(1 adult, 2 children)' / '(2 adults, 0 children)', and never a "
        "`slider` for a count like this (see FormField.type). Likewise, every `date` "
        "field needs its own specific label naming which date it is and its own category "
        "(see FormField.label/category) -- e.g. a 'Check-in date' field and a "
        "'Check-out date' field are two separate fields in two separate categories, "
        "never a bare 'Date' field, and never two date fields sharing one category. The "
        "user's answers come back later as a new message describing a submitted UI form "
        "-- respond to that by calling `show_card` with an answer that references their "
        "actual submitted values."
    )
)
def ask_preferences_form(title: str, fields: list[FormField], submit_label: str = "Submit") -> dict[str, Any]:
    surface_id = genui.new_surface_id("form")
    # Nested list items arrive as raw dicts, not coerced FormField instances
    # (LocalFunction's schema formatting doesn't recurse into list[BaseModel]).
    parsed_fields = [f if isinstance(f, FormField) else FormField(**f) for f in fields]
    # Title keywords aren't guaranteed to be English -- the model may title a
    # form entirely in the user's own language (e.g. a Chinese request for
    # "我想订酒店" can come back titled "预订酒店" with no English gloss at
    # all), so each list also carries the common-language equivalents most
    # likely to appear rather than relying on English substrings alone.
    is_flight_form = any(
        term in title.lower()
        for term in (
            "flight",
            "flights",
            "fly",
            "airfare",
            "plane",
            "机票",
            "航班",
            "飞机",  # Chinese: air ticket / flight / airplane
        )
    )
    # "booking"/"预订"/"预定" alone used to be in this list, but they're
    # generic booking-verb words that appear in titles for *any* domain (a
    # Chinese bus-ticket form titled "预订巴士票" matched here and got
    # hotel check_in/check_out fields bolted onto its own correctly-labeled
    # departure/return fields -- four date fields instead of two). Every
    # term below is specific to actually staying somewhere overnight.
    is_hotel_form = not is_flight_form and any(
        term in title.lower()
        for term in (
            "hotel",
            "accommodation",
            "stay",
            "酒店",
            "住宿",
            "订房",  # Chinese: hotel / lodging / book a room
        )
    )
    # Round-trip transport (bus, coach, train, ferry) -- checked after
    # flight/hotel since a title could otherwise ambiguously match more than
    # one (e.g. a title mentioning both a city and "巴士"). Without this
    # branch these forms fell through to the model inventing its own
    # departure/return fields from scratch, which is what "General flow"
    # asks it to do for stay dates too -- so a bus form ended up with both
    # its own correct fields AND the unrelated hotel ones.
    is_transport_form = (
        not is_flight_form
        and not is_hotel_form
        and any(
            term in title.lower()
            for term in (
                "bus",
                "coach",
                "train",
                "railway",
                "ferry",
                "巴士",
                "大巴",
                "客车",
                "火车",
                "高铁",
                "动车",
                "渡轮",
            )
        )
    )
    flight_field_groups: list[tuple[str, list[dict[str, Any]]]] = []
    flight_extra_components: list[dict[str, Any]] = []
    flight_field_paths: dict[str, str] = {}
    flight_field_defaults: dict[str, Any] = {}
    hotel_field_groups: list[tuple[str, list[dict[str, Any]]]] = []
    hotel_extra_components: list[dict[str, Any]] = []
    hotel_field_paths: dict[str, str] = {}
    hotel_field_defaults: dict[str, Any] = {}
    if is_flight_form:
        # Two compact side-by-side cards (dates, then adults/children) --
        # same treatment as the hotel form below, just without a "Rooms"
        # equivalent. See _hotel_compact_search_rows's docstring (shared by
        # _flight_compact_search_rows via _compact_dates_and_guests_rows)
        # for why this needs its own field_groups/extra_components rather
        # than going through the generic per-category loop below.
        flight_field_groups, flight_extra_components, flight_field_paths, flight_field_defaults = (
            _flight_compact_search_rows(title, parsed_fields)
        )
    elif is_hotel_form:
        # Two compact side-by-side cards (dates, then adults/children/rooms)
        # instead of a full-width card per field -- matches a typical hotel
        # search widget's layout. See _hotel_compact_search_rows's own
        # docstring for why this needs its own field_groups/extra_components
        # rather than going through the generic per-category loop below.
        hotel_field_groups, hotel_extra_components, hotel_field_paths, hotel_field_defaults = (
            _hotel_compact_search_rows(title, parsed_fields)
        )
    elif is_transport_form:
        today = datetime.now(timezone.utc).date()
        default_departure = today.isoformat()
        default_return = (today + timedelta(days=1)).isoformat()
        field_by_id = {field.id: field for field in parsed_fields}
        departure_label, return_label = (
            ("出发日期", "返程日期") if _is_chinese_title(title) else ("Departure date", "Return date")
        )
        new_fields = []
        for field_id, label, default_date in (
            ("departure_date", departure_label, default_departure),
            ("return_date", return_label, default_return),
        ):
            existing = field_by_id.get(field_id)
            if existing is None:
                new_fields.append(
                    FormField(
                        id=field_id,
                        type=FormFieldType.date,
                        label=label,
                        # Its own category, not a shared "Travel dates" --
                        # the category heading is the field's real visible
                        # label (see FormField.category), so departure and
                        # return each get their own card.
                        category=label,
                        default_date=default_date,
                        min_date=default_departure,
                    )
                )
            else:
                existing.type = FormFieldType.date
                existing.category = label
                existing.label = label
                existing.default_date = existing.default_date or default_date
                existing.min_date = existing.min_date or default_departure
        parsed_fields[0:0] = new_fields
    built_groups: dict[str, list[dict[str, Any]]] = {}
    field_paths: dict[str, str] = {**hotel_field_paths, **flight_field_paths}
    field_defaults: dict[str, Any] = {**hotel_field_defaults, **flight_field_defaults}
    for f in parsed_fields:
        category = f.category.strip() if f.category and f.category.strip() else "Preferences"
        built_fields = built_groups.setdefault(category, [])
        if f.type == FormFieldType.choice:
            options = [(opt.label, opt.value) for opt in (f.options or [])]
            default_value = [f.default_option_value] if f.default_option_value else []
            built_fields.append(
                genui.choice_picker(
                    f.id,
                    options,
                    label=f.label,
                    value=default_value,
                    variant="mutuallyExclusive",
                )
            )
            field_defaults[f.id] = default_value
            field_paths[f.id] = f"/{f.id}/value"
        elif f.type == FormFieldType.multi_choice:
            options = [(opt.label, opt.value) for opt in (f.options or [])]
            default_values = f.default_option_values if f.default_option_values else []
            built_fields.append(
                genui.choice_picker(
                    f.id,
                    options,
                    label=f.label,
                    value=default_values,
                    variant="multipleSelection",
                )
            )
            field_defaults[f.id] = default_values
            field_paths[f.id] = f"/{f.id}/value"
        elif f.type == FormFieldType.slider:
            default_value = f.default_number if f.default_number is not None else (f.min_value or 0)
            min_value = f.min_value or 0
            max_value = f.max_value if f.max_value is not None else 100
            built_fields.append(
                genui.slider(
                    f.id,
                    value=default_value,
                    min_value=min_value,
                    max_value=max_value,
                    label=f.label,
                )
            )
            slider_help = f.help_text or (
                f"Choose a value from {min_value:g} to {max_value:g}. Current value: {default_value:g}."
            )
            built_fields.append(
                genui.text(
                    f"{f.id}_help",
                    slider_help,
                    variant="caption",
                    styles={"line-clamp": 0},
                )
            )
            field_defaults[f.id] = default_value
            field_paths[f.id] = f"/{f.id}/value"
        elif f.type == FormFieldType.text:
            built_fields.append(genui.text_field(f.id, label=f.label, value=f.default_text))
            field_defaults[f.id] = f.default_text
            field_paths[f.id] = f"/{f.id}/value"
        elif f.type == FormFieldType.checkbox:
            built_fields.append(genui.check_box(f.id, f.label, value=f.default_checked))
            field_defaults[f.id] = f.default_checked
            field_paths[f.id] = f"/{f.id}/value"
        elif f.type == FormFieldType.date:
            default_value = f.default_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
            built_fields.append(
                genui.date_input(
                    f.id,
                    value=default_value,
                    label=f.label,
                    min_date=f.min_date,
                    max_date=f.max_date,
                )
            )
            field_defaults[f.id] = default_value
            field_paths[f.id] = f"/{f.id}/value"

    messages = genui.form(
        surface_id,
        title=title,
        fields=[field for group in built_groups.values() for field in group],
        # Model-supplied fields (e.g. destination/route) first, then the
        # compact rows -- matches a typical search widget's order:
        # destination, then dates, then guests.
        field_groups=list(built_groups.items()) + hotel_field_groups + flight_field_groups,
        submit_label=submit_label,
        action_name="submit_preferences_form",
        field_paths=field_paths,
        field_defaults=field_defaults,
        extra_components=hotel_extra_components + flight_extra_components,
    )
    return {
        "text": f'I\'ve put a "{title}" form on your screen — fill it in and submit when ready.',
        "genui": messages,
    }


ALL_TOOLS = (
    get_current_time,
    show_card,
    show_info_list,
    ask_preferences_form,
    search_images,
    fetch_page_image,
    search_youtube_videos,
    fetch_video_source,
    show_video_clips,
    geocode_place,
    show_map,
    search_hotels,
    show_hotel_results,
    search_flights,
    show_flight_results,
    search_finance,
    show_finance_results,
)
