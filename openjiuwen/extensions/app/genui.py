# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""A2UI v0.9 message builders.

Matches the wire schema shipped with the Flutter ``genui`` package
(``genui-0.10.1/assets/schemas/server_to_client.json``): every message
requires ``"version": "v0.9"``; ``createSurface`` requires ``surfaceId`` and
``catalogId``; ``updateComponents`` requires one component with ``id: "root"``.
"""

import uuid
from typing import Any, Optional

from . import config

A2UI_VERSION = "v0.9"


def new_surface_id(prefix: str = "surface") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def create_surface(
    surface_id: str,
    catalog_id: Optional[str] = None,
    theme: Optional[dict[str, Any]] = None,
    send_data_model: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "surfaceId": surface_id,
        "catalogId": catalog_id or config.get("CATALOG_ID"),
    }
    if theme is not None:
        payload["theme"] = theme
    if send_data_model:
        payload["sendDataModel"] = True
    return {"version": A2UI_VERSION, "createSurface": payload}


def update_components(surface_id: str, components: list[dict[str, Any]]) -> dict[str, Any]:
    if not any(c.get("id") == "root" for c in components):
        raise ValueError("updateComponents requires one component with id 'root'")
    return {
        "version": A2UI_VERSION,
        "updateComponents": {"surfaceId": surface_id, "components": components},
    }


def delete_surface(surface_id: str) -> dict[str, Any]:
    return {"version": A2UI_VERSION, "deleteSurface": {"surfaceId": surface_id}}


def update_data_model(surface_id: str, path: str, value: Any) -> dict[str, Any]:
    return {
        "version": A2UI_VERSION,
        "updateDataModel": {"surfaceId": surface_id, "path": path, "value": value},
    }


# ---------------------------------------------------------------------------
# Minimal basic-catalog component helpers (Text / Column / Divider)
# ---------------------------------------------------------------------------


def text(
    comp_id: str,
    value: str,
    variant: str = "body",
    weight: Optional[int] = None,
    styles: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"id": comp_id, "component": "Text", "text": value, "variant": variant}
    if weight is not None:
        payload["weight"] = weight
    if styles is not None:
        payload["styles"] = styles
    return payload


def divider(comp_id: str) -> dict[str, Any]:
    return {"id": comp_id, "component": "Divider"}


def column(
    comp_id: str,
    children: list[str],
    justify: Optional[str] = None,
    align: Optional[str] = None,
    weight: Optional[int] = None,
    styles: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    # `weight` only matters when this Column is placed as a child of a Row
    # (or List laid out horizontally): Row only gives a child flexible sizing
    # -- so long text wraps instead of overflowing -- if the child component
    # carries an explicit "weight" property (Column/Text aren't
    # isImplicitlyFlexible like TextField/ChoicePicker are). See
    # genui-0.10.1/lib/src/catalog/basic_catalog_widgets/row.dart.
    payload: dict[str, Any] = {"id": comp_id, "component": "Column", "children": children}
    if justify is not None:
        payload["justify"] = justify
    if align is not None:
        payload["align"] = align
    if weight is not None:
        payload["weight"] = weight
    if styles is not None:
        payload["styles"] = styles
    return payload


# Card's own component spec only sets border-radius (16px); it inherits
# transparent background / 0px border from the universal style baseline.
# Against this app's plain white page that leaves cards with no visible
# boundary at all, so every card() call gets this light, bordered surface
# by default instead of relying on the (invisible) built-in look.
_DEFAULT_CARD_STYLES: dict[str, Any] = {
    "background-color": "#FFFFFF",
    "border-width": "1px",
    "border-color": "#E1E4E9",
    "filter": "drop-shadow(0px 1px 4px rgba(0, 0, 0, 0.08))",
    "padding": "16px",
}


def card(comp_id: str, child_id: str, styles: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """A Material card surface (elevation/shadow) wrapping a single child."""
    payload: dict[str, Any] = {"id": comp_id, "component": "Card", "child": child_id}
    payload["styles"] = {**_DEFAULT_CARD_STYLES, **(styles or {})}
    return payload


def row(
    comp_id: str,
    children: list[str],
    justify: Optional[str] = None,
    align: Optional[str] = None,
    styles: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"id": comp_id, "component": "Row", "children": children}
    if justify is not None:
        payload["justify"] = justify
    if align is not None:
        payload["align"] = align
    if styles is not None:
        payload["styles"] = styles
    return payload


_MATERIAL_TO_LUCIDE_ICON_NAMES: dict[str, str] = {
    "accountCircle": "circle-user-round",
    "add": "plus",
    "arrowBack": "arrow-left",
    "arrowForward": "arrow-right",
    "attachFile": "paperclip",
    "calendarToday": "calendar-days",
    "call": "phone",
    "camera": "camera",
    "check": "check",
    "close": "x",
    "delete": "trash-2",
    "download": "download",
    "edit": "pencil",
    "error": "circle-alert",
    "event": "calendar",
    "favorite": "heart",
    "favoriteOff": "heart-off",
    "folder": "folder",
    "help": "circle-help",
    "home": "house",
    "info": "info",
    "locationOn": "map-pin",
    "lock": "lock",
    "lockOpen": "lock-open",
    "mail": "mail",
    "menu": "menu",
    "moreHoriz": "ellipsis",
    "moreVert": "ellipsis-vertical",
    "notifications": "bell",
    "notificationsOff": "bell-off",
    "payment": "credit-card",
    "person": "user",
    "phone": "phone",
    "photo": "image",
    "print": "printer",
    "refresh": "refresh-cw",
    "search": "search",
    "send": "send",
    "settings": "settings",
    "share": "share-2",
    "shoppingCart": "shopping-cart",
    "star": "star",
    "starHalf": "star-half",
    "starOff": "star-off",
    "upload": "upload",
    "visibility": "eye",
    "visibilityOff": "eye-off",
    "warning": "triangle-alert",
}


def icon(comp_id: str, name: str) -> dict[str, Any]:
    """Render a supported backend icon through Harmony AGenUI's Lucide mapper."""
    return {
        "id": comp_id,
        "component": "Icon",
        "name": _MATERIAL_TO_LUCIDE_ICON_NAMES.get(name, name),
    }


def image(
    comp_id: str,
    url: str,
    variant: str = "mediumFeature",
    fit: Optional[str] = None,
    styles: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """An image loaded directly by the client via ``Image.network(url)``.

    ``url`` must be a real, publicly reachable http(s) URL -- the client
    fetches it directly, nothing is downloaded/served by this backend. A
    broken/unreachable URL degrades gracefully to a broken-image icon
    client-side, it doesn't fail the surface.

    ``variant``: one of icon/avatar/smallFeature/mediumFeature/largeFeature/header.
    """
    payload: dict[str, Any] = {"id": comp_id, "component": "Image", "url": url, "variant": variant}
    if fit is not None:
        payload["fit"] = fit
    if styles is not None:
        payload["styles"] = styles
    return payload


def chart(
    comp_id: str,
    chart_type: str,
    series: list[dict[str, Any]],
    x_axis: Optional[list[str]] = None,
    y_axis: Optional[list[str]] = None,
    styles: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """A native interactive chart, rendered directly by AGenUI's built-in
    ``Chart`` catalog component. Unlike ``map_web()``/``youtube_embed()``,
    this needs no WebView or client-side custom-component registration --
    it's part of the standard catalog every AGenUI client already renders,
    the same way ``image()``/``text()`` are.

    ``chart_type``: one of "line"/"donut"/"bar".
    ``series``: list of ``{"name": <str, optional>, "data": [{"value": <number>,
    "label": <str, optional>}, ...]}`` -- donut charts use a single series
    with a label per slice; line/bar charts use one series per line/bar set.
    ``x_axis``/``y_axis``: optional string axis labels, for line/bar charts.
    Colors default to the catalog's own palette; override with
    ``styles={"chartConfig": {"colors": [...]}}``.
    """
    data: dict[str, Any] = {"series": series}
    if x_axis is not None:
        data["xAxis"] = x_axis
    if y_axis is not None:
        data["yAxis"] = y_axis
    payload: dict[str, Any] = {"id": comp_id, "component": "Chart", "chartType": chart_type, "data": data}
    if styles is not None:
        payload["styles"] = styles
    return payload


def carousel(
    comp_id: str,
    content: list[str],
    autoplay: Optional[bool] = None,
    draggable: Optional[bool] = None,
    styles: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """A swipeable image carousel (native catalog component, built-in page
    indicator) -- every URL in ``content`` starts loading immediately once
    this renders (the client builds one Image node per URL upfront, not
    lazily as the user swipes), so keep ``content`` short; this app caps it
    at a few images per gallery item (see ``hotel_tools.MAX_HOTEL_IMAGES``).
    """
    payload: dict[str, Any] = {"id": comp_id, "component": "Carousel", "content": content}
    if autoplay is not None:
        payload["autoplay"] = autoplay
    if draggable is not None:
        payload["draggable"] = draggable
    if styles is not None:
        payload["styles"] = styles
    return payload


def video(comp_id: str, url: str, styles: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """A native video player (client-side AVPlayer or equivalent) loaded directly
    from ``url``.

    ``url`` must be a real, direct, playable video file or stream (e.g. an
    .mp4/.webm/.m3u8 URL) -- NOT a YouTube/Vimeo watch page, which the native
    player cannot open (it has no HTML/JS engine, just a media decoder). For
    YouTube/Vimeo content use ``web()`` instead, pointed at that site's own
    embed URL.
    """
    payload: dict[str, Any] = {"id": comp_id, "component": "Video", "url": url}
    if styles is not None:
        payload["styles"] = styles
    return payload


def web(comp_id: str, url: str, styles: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """An embedded WebView loading ``url`` (adaptive height, JavaScript enabled).

    For general third-party pages. For YouTube embeds specifically, use
    ``youtube_embed()`` instead -- YouTube's player rejects a WebView that
    navigates straight to its embed URL as a top-level page (no parent
    frame), so that needs the client's dedicated YouTube component, which
    wraps the URL in a real ``<iframe>`` first.
    """
    payload: dict[str, Any] = {"id": comp_id, "component": "Web", "url": url}
    if styles is not None:
        payload["styles"] = styles
    return payload


def youtube_embed(comp_id: str, url: str, styles: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """A YouTube video embed, rendered via the client's ``YouTubeWeb`` custom
    component (registered through AGenUI's custom-component API).

    ``url`` should be a ``youtube.com/embed/<id>`` (or ``youtube-nocookie.com``)
    URL. Unlike the generic ``web()``/``Web`` component, this wraps the URL in
    a real ``<iframe>`` client-side before loading it -- YouTube's embedded
    player checks whether it's genuinely inside an iframe as part of its
    validation, and a WebView navigated directly to the embed URL (no parent
    frame at all) fails that check for most real videos ("Error 153: Video
    player configuration error"), even though the URL itself is valid.
    """
    payload: dict[str, Any] = {"id": comp_id, "component": "YouTubeWeb", "url": url}
    if styles is not None:
        payload["styles"] = styles
    return payload


def map_web(comp_id: str, url: str, styles: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """An interactive map embed, rendered via the client's ``MapWeb`` custom
    component (registered through AGenUI's custom-component API, mirroring
    ``YouTubeWeb``/``youtube_embed()`` above).

    ``url`` must be this app's own ``/map-embed`` route (see
    ``map_tools.show_map``) -- a self-contained page embedding the real
    Google Maps JavaScript API with every place's marker already placed.
    A generic ``web()``/``Web`` load would fail here: that route is
    served over this backend's own self-signed cert, which the client's
    custom ``MapWeb`` component explicitly trusts (like ``YouTubeWeb`` does
    for ``/youtube-embed``) but the generic ``Web`` component's WebView does
    not, and would silently reject.
    """
    payload: dict[str, Any] = {"id": comp_id, "component": "MapWeb", "url": url}
    if styles is not None:
        payload["styles"] = styles
    return payload


def video_gallery_card(
    surface_id: str,
    title: str,
    items: list[tuple[str, str, str]],
) -> list[dict[str, Any]]:
    """A titled vertical list of playable video clips, each in its own Card.

    ``items`` is a list of (kind, url, caption):
      - kind="youtube": ``url`` is a ``youtube.com/embed/<id>`` URL, rendered
        via ``youtube_embed()`` -- playback goes through YouTube's own
        embedded player.
      - kind="direct": ``url`` is a real, direct video file/stream, rendered
        via ``video()`` -- the client's native player downloads/decodes it
        itself.
    """
    card_ids = [f"clip{i}Card" for i in range(len(items))]
    item_components: list[dict[str, Any]] = []
    for i, (kind, url, caption) in enumerate(items):
        card_id = card_ids[i]
        outer_id = f"clip{i}"
        media_id = f"{outer_id}Media"
        caption_id = f"{outer_id}Caption"
        # padding=0 on the card (overriding the default 16px), same reasoning
        # as info_list_card's photo items: the video should sit flush against
        # the card edges, with the caption text getting its own inset padding
        # below it rather than the whole card being inset.
        item_components.append(card(card_id, outer_id, styles={"padding": "0px"}))
        # align="stretch": Web/Video have no `variant` like Image does to pick
        # a full-width preset, so without an explicit stretch the column only
        # gives them wrap-content width -- they loaded but had ~0px to paint
        # into. width:100% on the media component itself backs that up.
        item_components.append(column(outer_id, [media_id, caption_id], align="stretch"))
        # aspect-ratio: "height is computed from the width" per the A2UI
        # style schema -- yoga can size this synchronously during the list's
        # very first layout pass, before the WebView/player has loaded
        # anything. Without it, height only exists once the embedded content
        # asynchronously reports its own size back (YouTubeWebComponent's
        # reportContentHeight / the native Video player's metadata callback),
        # which arrives well after the list has already laid out every card
        # at its collapsed zero-height -- and nothing in the client's custom-
        # component update path was forcing AGenUIContainer to re-measure
        # once that late size showed up, so every card stayed collapsed and
        # visually stacked on top of its neighbors.
        media_styles: dict[str, Any] = {"width": "100%", "aspect-ratio": "16/9"}
        if kind == "youtube":
            item_components.append(youtube_embed(media_id, url, styles=media_styles))
        else:
            item_components.append(video(media_id, url, styles=media_styles))
        item_components.append(
            text(caption_id, caption, variant="body", styles={"padding": "12px 16px", "line-clamp": 0})
        )

    components = [
        column("root", ["title", "list"], styles={"gap": "12px"}),
        text("title", title, variant="h3"),
        list_view("list", card_ids, styles={"gap": "12px"}),
        *item_components,
    ]
    return [
        create_surface(surface_id),
        update_components(surface_id, components),
    ]


_MAP_PLACE_STATUS_COLORS: dict[bool, str] = {True: "#16A34A", False: "#DC2626"}


def map_places_list(surface_id: str, places: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """A horizontally-scrollable strip of place cards (native ``List``
    component, ``direction="horizontal"`` -- unlike ``Carousel``, which this
    catalog only supports as a plain image gallery, ``List`` takes arbitrary
    child components, so each card can carry a photo, rating, category,
    price, and open/closed status). Tapping a card calls the client's
    ``highlightMapPlace`` function (registered in ``Index.ets`` /
    ``HighlightMapPlaceFunction.ets``) with this map's own ``surface_id`` and
    the place's index, which pans/highlights that place's pin on the map --
    see ``buildPin()``/``selectPlace()`` in ``map_tools._MAP_EMBED_TEMPLATE``
    for the client-side half of this.

    Each dict in ``places`` is one ``_place_payload()`` result: ``label``
    (required), ``image_url``, ``rating``, ``category``, ``price_level``,
    ``open_now`` all optional -- only what's present is rendered.

    Returns ``(list_component_id, components)`` -- the caller embeds the
    returned id as a child alongside the map, and appends ``components`` to
    its own component list (this doesn't create its own surface).
    """
    card_ids = [f"place{i}Card" for i in range(len(places))]
    item_components: list[dict[str, Any]] = []
    for i, place in enumerate(places):
        outer = f"place{i}"
        button_id = f"{outer}Btn"
        card_id = card_ids[i]
        content_id = f"{outer}Content"
        name_id = f"{outer}Name"
        meta_id = f"{outer}Meta"
        status_id = f"{outer}Status"
        image_id = f"{outer}Image"

        meta_parts: list[str] = []
        if place.get("rating") is not None:
            meta_parts.append(f"★ {place['rating']:.1f}")
        if place.get("category"):
            meta_parts.append(place["category"])
        status_parts: list[str] = []
        if place.get("price_level"):
            status_parts.append(place["price_level"])
        if place.get("open_now") is not None:
            status_parts.append("Open" if place["open_now"] else "Closed")

        # Every card gets the same four rows (image, name, meta, status),
        # each present even when that place has no data for it -- a card
        # that skips a row entirely ends up shorter than its siblings, and
        # since the horizontal List stretches every card to the row's
        # height, the shorter one's content visually centers instead of
        # lining up at the top with the rest. A blank placeholder in that
        # row's spot keeps every card's content the same height instead.
        content_children = [image_id, name_id, meta_id, status_id]

        item_components.append(
            {
                "id": button_id,
                "component": "Button",
                "child": card_id,
                "variant": "borderless",
                # Unlike button()/open_url_button(), no _DEFAULT_BUTTON_STYLES
                # merge here -- this wrapper exists only to make the whole
                # card tappable; its own chrome (the CTA blue pill styling
                # those helpers apply) would show through behind the card.
                "styles": {"padding": "0px", "background-color": "transparent", "border-radius": "0px"},
                # Client-local functionCall (like open_url_button), not a
                # server-round-trip action.event -- see
                # HighlightMapPlaceFunction.ets / MapWebComponent.highlightPlace().
                "action": {
                    "functionCall": {"call": "highlightMapPlace", "args": {"surfaceId": surface_id, "index": i}}
                },
            }
        )
        item_components.append(
            card(card_id, content_id, styles={"width": "220px", "padding": "0px", "overflow": "hidden"})
        )
        item_components.append(
            # Card doesn't stretch its child to the card's own width by
            # itself -- without "width": "100%" here, this column (and the
            # image inside it) only take their own intrinsic width (as wide
            # as the longest text row), leaving the image short of the
            # card's actual edges despite its own width:100% below (100% of
            # an already-too-narrow parent is still too narrow). No
            # align="stretch" here though -- that would *also* stretch the
            # text rows to the card's full width instead of their own
            # natural size; only the image (and its no-photo placeholder)
            # ask for width:100% on themselves below, so only they stretch.
            column(content_id, content_children, styles={"gap": "2px", "width": "100%"})
        )
        if place.get("image_url"):
            item_components.append(
                image(
                    image_id,
                    place["image_url"],
                    variant="header",
                    fit="cover",
                    # Explicit width -- without it the image only takes its
                    # own intrinsic/variant-driven width instead of the
                    # card's full 220px, leaving a gap (the card's `overflow:
                    # hidden` cropped it from the *right*, which is what
                    # showed as "the left side doesn't fit").
                    # "header" variant bakes in a default aspect-ratio:"16/9" --
                    # override it to "auto" or Yoga derives width from
                    # height*ratio instead of respecting width:100% (this is
                    # what caused images to render narrower than their card,
                    # leaving a blank gap before the next card).
                    styles={"width": "100%", "height": "96px", "aspect-ratio": "auto"},
                )
            )
        else:
            item_components.append(
                column(image_id, [], styles={"width": "100%", "height": "96px", "background-color": "#E1E4E9"})
            )
        item_components.append(
            text(name_id, place["label"], variant="body", weight=1, styles={"padding": "0px 12px", "line-clamp": 1})
        )
        # " " (not "") -- an empty string can collapse to zero height
        # in some Text renderers, which would reintroduce the same
        # misalignment this whole placeholder scheme exists to avoid.
        item_components.append(
            text(
                meta_id,
                "  •  ".join(meta_parts) if meta_parts else " ",
                variant="caption",
                styles={"padding": "0px 12px"},
            )
        )
        status_color = _MAP_PLACE_STATUS_COLORS.get(place.get("open_now"), "#6B7280") if status_parts else "transparent"
        item_components.append(
            text(
                status_id,
                "  •  ".join(status_parts) if status_parts else " ",
                variant="caption",
                weight=1,
                styles={"padding": "0px 12px 12px 12px", "color": status_color},
            )
        )

    # Neither "gap" on the List nor per-item "margin" has any visible effect
    # in this client -- confirmed two different ways: (1) "gap" from 0px to
    # 60px all rendered identically, and (2) a rebuilt debug client with
    # logging showed the engine computing the *correct* margin-adjusted
    # position for each card, yet the on-screen spacing between cards still
    # didn't change. A real spacer *component* between cards works instead,
    # since a component's own width is the one thing consistently confirmed
    # (by the same logging) to render exactly as requested.
    list_children: list[str] = []
    for i in range(len(places)):
        if i > 0:
            list_children.append(f"spacer{i}")
            item_components.append(column(f"spacer{i}", [], styles={"width": "6px"}))
        list_children.append(f"place{i}Btn")

    list_id = "placesList"
    components = [
        list_view(
            list_id,
            list_children,
            direction="horizontal",
            # Top padding separates this row from the map above without
            # adding unwanted space anywhere else in map_card()'s
            # title/divider/map/placesList stack (that Column has no "gap"
            # style, so title-divider-map stay flush, which already looks
            # right).
            styles={"padding": "12px 0px 0px 0px"},
        ),
        *item_components,
    ]
    return list_id, components


def map_card(
    surface_id: str,
    title: str,
    map_embed_url: str,
    places: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """A titled card showing an interactive map (see ``map_web()`` -- real,
    tappable markers via a live Google Maps JavaScript API page, not a
    static image) -- ``map_embed_url`` must be this app's own ``/map-embed``
    route, see ``map_tools.show_map``. When ``places`` is given, a
    horizontally-scrollable card strip (see ``map_places_list()``) renders
    below the map, one card per place, synced to the map's pins by tap.
    """
    places_components: list[dict[str, Any]] = []
    places_list_id: Optional[str] = None
    if places:
        places_list_id, places_components = map_places_list(surface_id, places)

    inner_children = ["title", "divider", "map"] + ([places_list_id] if places_list_id else [])
    components = [
        card("root", "content", styles={"padding": "0px"}),
        column("content", inner_children),
        text("title", title, variant="h3", styles={"padding": "16px 16px 12px 16px"}),
        divider("divider"),
        # Must match MapWebComponent.ets's own .aspectRatio() value -- this
        # server-declared hint and the client's internal ArkUI sizing are
        # two independent sources of truth for the same box; a mismatch
        # between them leaves blank space around the map (the layout engine
        # reserves a slot sized off this value, but the WebView itself
        # renders at whatever ratio the client component actually uses).
        map_web("map", map_embed_url, styles={"width": "100%", "aspect-ratio": "4/3"}),
        *places_components,
    ]
    return [
        create_surface(surface_id),
        update_components(surface_id, components),
    ]


# Every Material-style icon name accepted by the tool API. ``icon()`` above
# translates them to AGenUI/Harmony's Lucide names before sending the surface.
ICON_NAMES = [
    "accountCircle",
    "add",
    "arrowBack",
    "arrowForward",
    "attachFile",
    "calendarToday",
    "call",
    "camera",
    "check",
    "close",
    "delete",
    "download",
    "edit",
    "error",
    "event",
    "favorite",
    "favoriteOff",
    "folder",
    "help",
    "home",
    "info",
    "locationOn",
    "lock",
    "lockOpen",
    "mail",
    "menu",
    "moreHoriz",
    "moreVert",
    "notifications",
    "notificationsOff",
    "payment",
    "person",
    "phone",
    "photo",
    "print",
    "refresh",
    "search",
    "send",
    "settings",
    "share",
    "shoppingCart",
    "star",
    "starHalf",
    "starOff",
    "upload",
    "visibility",
    "visibilityOff",
    "warning",
]


def list_view(
    comp_id: str,
    children: list[str],
    direction: Optional[str] = None,
    align: Optional[str] = None,
    styles: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"id": comp_id, "component": "List", "children": children}
    if direction is not None:
        payload["direction"] = direction
    if align is not None:
        payload["align"] = align
    if styles is not None:
        payload["styles"] = styles
    return payload


def tabs(comp_id: str, tab_specs: list[tuple[str, str]], active_tab: int = 0) -> dict[str, Any]:
    """``tab_specs`` is a list of (label, content_component_id) pairs."""
    return {
        "id": comp_id,
        "component": "Tabs",
        "tabs": [{"label": label, "content": content_id} for label, content_id in tab_specs],
        "activeTab": active_tab,
    }


def summary_card(
    surface_id: str,
    title: str,
    body: str,
    icon_name: Optional[str] = None,
    image_url: Optional[str] = None,
    link_url: Optional[str] = None,
    link_label: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Build a create+update pair rendering a title/body card in a Material Card surface.

    ``icon_name`` (one of ``ICON_NAMES``) puts a leading icon next to the title.
    ``image_url`` puts a full-width header image above the title.
    ``link_url`` adds a button below the body that opens that URL externally
    (via ``open_url_button``) -- e.g. handing off to a real website for the
    user to finish something (like a booking) there themselves.
    """
    header_children = ["headerIcon", "title"] if icon_name else ["title"]
    inner_children = (
        (["image"] if image_url else [])
        + (["header"] if icon_name else ["title"])
        + ["divider", "body"]
        + (["linkButton"] if link_url else [])
    )
    components = [
        card("root", "content"),
        column("content", inner_children),
        *([image("image", image_url, variant="header")] if image_url else []),
        *([row("header", header_children, align="center"), icon("headerIcon", icon_name)] if icon_name else []),
        text("title", title, variant="h3", weight=1 if icon_name else None),
        divider("divider"),
        text("body", body, variant="body", styles={"line-clamp": 0}),
        *(
            [
                open_url_button("linkButton", "linkText", link_url, styles={"width": "100%"}),
                text(
                    "linkText",
                    link_label or "Continue on site",
                    styles={"color": "#FFFFFF", "width": "100%", "text-align": "center"},
                ),
            ]
            if link_url
            else []
        ),
    ]
    return [
        create_surface(surface_id),
        update_components(surface_id, components),
    ]


def info_list_card(
    surface_id: str,
    title: str,
    items: list[tuple[Optional[str], str, Optional[str], Optional[str]]],
    icon_name: Optional[str] = None,
) -> list[dict[str, Any]]:
    """A titled heading followed by a vertical list of individually-carded items.

    ``items`` is a list of (item_icon_name_or_None, item_title,
    item_subtitle_or_None, item_image_url_or_None). If both an icon and an
    image are given for an item, the image wins (it's shown, not the icon).
    Each item renders in its own Material Card (its own elevated surface,
    not one shared list inside a single outer card) -- good for itineraries,
    step-by-step guides, medication schedules, feature lists -- anything
    that's naturally several discrete entries rather than one paragraph.
    """
    card_ids = [f"item{i}Card" for i in range(len(items))]
    item_components: list[dict[str, Any]] = []
    for i, (item_icon, item_title, item_subtitle, item_image_url) in enumerate(items):
        card_id = card_ids[i]
        outer_id = f"item{i}"
        text_col_id = f"{outer_id}TextCol"
        text_id = f"{outer_id}Text"
        subtitle_id = f"{outer_id}Subtitle"
        media_id = f"{outer_id}Media"
        text_col_children = [text_id] + ([subtitle_id] if item_subtitle else [])
        # padding=0 on the card itself (overriding the default 16px): a photo
        # is only a full-bleed "on top of the card" look if nothing insets
        # it. The text block below gets its own padding instead, applied to
        # textColId, so it's still comfortably inset from the card edges.
        item_components.append(card(card_id, outer_id, styles={"padding": "0px"}))
        if item_image_url:
            item_components.append(column(outer_id, [media_id, text_col_id]))
            item_components.append(image(media_id, item_image_url, variant="header", fit="cover"))
            item_components.append(column(text_col_id, text_col_children, styles={"padding": "16px", "gap": "8px"}))
        else:
            # No photo for this item: a leading icon next to the title
            # instead of an empty full-width photo block.
            header_row_id = f"{outer_id}Header"
            item_components.append(
                column(
                    outer_id,
                    [header_row_id, *([subtitle_id] if item_subtitle else [])],
                    styles={"padding": "16px", "gap": "8px"},
                )
            )
            item_components.append(row(header_row_id, [media_id, text_id], align="center", styles={"gap": "12px"}))
            # tools.py's _item_icon() always supplies a fallback icon when
            # there's no image.
            item_components.append(icon(media_id, item_icon or "check"))
        item_components.append(text(text_id, item_title, variant="body"))
        if item_subtitle:
            # line-clamp: the Text component's shared style baseline defaults
            # to a single visible line with a trailing ellipsis; these
            # subtitles are 1-2 sentence descriptions, so that was cutting
            # nearly all of them off after a few words. 0 = unlimited, so the
            # full description always shows regardless of length.
            item_components.append(text(subtitle_id, item_subtitle, variant="caption", styles={"line-clamp": 0}))

    header_children = ["headerIcon", "title"] if icon_name else ["title"]
    root_children = (["header"] if icon_name else ["title"]) + ["list"]
    components = [
        column("root", root_children, styles={"gap": "12px"}),
        *([row("header", header_children, align="center"), icon("headerIcon", icon_name)] if icon_name else []),
        text("title", title, variant="h3", weight=1 if icon_name else None),
        list_view("list", card_ids, styles={"gap": "10px"}),
        *item_components,
    ]
    return [
        create_surface(surface_id),
        update_components(surface_id, components),
    ]


# ---------------------------------------------------------------------------
# Form input components (ChoicePicker / Slider / TextField / CheckBox / Button)
#
# Each input auto-binds to a data-model path of "<comp_id>.value" unless an
# explicit {"path": ...} is given for its value -- see genui's TextField /
# ChoicePicker / Slider / CheckBox widget builders. Button.action's "context"
# map is resolved against those same paths when pressed, so a submit button
# collects whatever the user has entered into its sibling fields.
# ---------------------------------------------------------------------------


def choice_picker(
    comp_id: str,
    options: list[tuple[str, str]],
    label: Optional[str] = None,
    value: Optional[list[str]] = None,
    variant: str = "mutuallyExclusive",
    styles: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": comp_id,
        "component": "ChoicePicker",
        "options": [{"label": opt_label, "value": opt_value} for opt_label, opt_value in options],
        "value": value or [],
        "variant": variant,
    }
    if label is not None:
        payload["label"] = label
    if styles is not None:
        payload["styles"] = styles
    return payload


def slider(
    comp_id: str,
    value: float,
    min_value: float = 0,
    max_value: float = 1,
    label: Optional[str] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": comp_id,
        "component": "Slider",
        "value": value,
        "min": min_value,
        "max": max_value,
    }
    if label is not None:
        payload["label"] = label
    return payload


def text_field(
    comp_id: str,
    label: Optional[str] = None,
    value: str = "",
    variant: Optional[str] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"id": comp_id, "component": "TextField", "value": value}
    if label is not None:
        payload["label"] = label
    if variant is not None:
        payload["variant"] = variant
    return payload


def check_box(comp_id: str, label: str, value: bool = False) -> dict[str, Any]:
    return {"id": comp_id, "component": "CheckBox", "label": label, "value": value}


def date_input(
    comp_id: str,
    value: str,
    label: Optional[str] = None,
    min_date: Optional[str] = None,
    max_date: Optional[str] = None,
) -> dict[str, Any]:
    """A date-only native calendar picker using YYYY-MM-DD values."""
    payload: dict[str, Any] = {
        "id": comp_id,
        "component": "DateTimeInput",
        "value": value,
        "enableDate": True,
        "enableTime": False,
    }
    if label is not None:
        payload["label"] = label
    if min_date is not None:
        payload["min"] = min_date
    if max_date is not None:
        payload["max"] = max_date
    return payload


# The catalog's Button spec only recognizes "default"/"borderless" variants
# ("primary" is not a real enum value there) and its "default" look is a
# near-white background (Color_BG_L5) with a 6%-black hairline border --
# invisible against this app's plain white page. Give every button an
# explicit, unmistakably-visible brand-blue fill instead of relying on that.
_DEFAULT_BUTTON_STYLES: dict[str, Any] = {
    "background-color": "#2273F7",
    "border-width": "0px",
    # Button has no built-in padding at all (its spec never sets one), so
    # the label text sat flush against the button's edges. A generous
    # padding plus a large, fully-rounded radius gives the pill shape a
    # real button needs instead of a bare colored rectangle.
    "padding": "16px 32px",
    "border-radius": "32px",
}


def button(
    comp_id: str,
    child_id: str,
    action_name: str,
    context_paths: Optional[dict[str, str]] = None,
    variant: str = "default",
    styles: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {"name": action_name}
    if context_paths:
        event["context"] = {key: {"path": path} for key, path in context_paths.items()}
    return {
        "id": comp_id,
        "component": "Button",
        "child": child_id,
        "variant": variant,
        "styles": {**_DEFAULT_BUTTON_STYLES, **(styles or {})},
        "action": {"event": event},
    }


def open_url_button(
    comp_id: str,
    child_id: str,
    url: str,
    variant: str = "default",
    styles: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """A button that opens ``url`` externally via the client's built-in ``openUrl``
    function, instead of ``button()``'s in-app ``UserActionEvent``.

    Use this to hand off to a real website (e.g. so the user can finish a
    booking/reservation there themselves) -- pressing it never sends anything
    back to this agent, it only opens the URL in the user's browser/handler.
    """
    return {
        "id": comp_id,
        "component": "Button",
        "child": child_id,
        "variant": variant,
        "styles": {**_DEFAULT_BUTTON_STYLES, **(styles or {})},
        "action": {"functionCall": {"call": "openUrl", "args": {"url": url}}},
    }


def form(
    surface_id: str,
    title: str,
    fields: list[dict[str, Any]],
    submit_label: str,
    action_name: str,
    field_paths: dict[str, str],
    field_defaults: Optional[dict[str, Any]] = None,
    field_groups: Optional[list[tuple[str, list[dict[str, Any]]]]] = None,
    extra_components: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Build a create+update(+updateDataModel) sequence for a titled form.

    ``field_paths`` maps context keys to "<field_id>.value" data-model paths;
    the submit button reads these off the data model when pressed, so its
    resulting UserActionEvent's ``context`` carries whatever the user entered.

    A field's ``value`` in its component JSON only sets its *visual* initial
    state -- it is not written into the data model until the user interacts
    with the widget (see e.g. ChoicePicker/Slider/TextField/CheckBox widget
    builders: unset paths fall back to that literal for display only). A
    field left untouched at its pre-selected default would therefore submit
    as empty. ``field_defaults`` (field id -> default value) seeds the data
    model directly via ``updateDataModel`` so defaults are real, submittable
    values even if the user never touches that field.

    Each ``group_fields`` entry's own ``id`` ends up as a direct child of
    that group's Column -- so to nest a field two levels deep (e.g. inside
    a ``row()`` for a compact side-by-side layout), put only the *wrapping*
    Row/Column in ``group_fields`` and pass the actual field widgets (and
    anything else the wrapper's own ``children`` references) via
    ``extra_components`` instead, so they still land in the tree without
    also being listed as direct children of the group's own Column.
    """
    groups = field_groups or [("Preferences", fields)]
    group_card_ids = [f"group{index}Card" for index in range(len(groups))]
    group_components: list[dict[str, Any]] = []
    for index, (category, group_fields) in enumerate(groups):
        content_id = f"group{index}Content"
        category_id = f"group{index}Title"
        content_children = [field["id"] for field in group_fields]
        # An empty category skips the heading entirely -- e.g. a compact
        # row of fields (see the `extra_components` doc above) that already
        # each show their own caption inline, where a card-level "h4" above
        # them would be a redundant, unwanted second label.
        heading = []
        if category:
            content_children.insert(0, category_id)
            heading = [text(category_id, category, variant="h4")]
        group_components.extend(
            [
                card(group_card_ids[index], content_id),
                column(
                    content_id,
                    content_children,
                    align="stretch",
                    styles={"gap": "10px"},
                ),
                *heading,
                *group_fields,
            ]
        )
    components = [
        # The submit button lives in its own trailing Card, as a sibling of
        # the fields card rather than nested inside the same Column/Row
        # subtree as every field. Splitting the button out this way keeps
        # each card's own reconciliation small, which has proven far more
        # reliable than one deeply-nested tree for large forms -- big single
        # trees have intermittently dropped the button component entirely
        # during the client's batch-flush pass.
        column("root", ["title", *group_card_ids, "submitCard"], styles={"gap": "12px"}),
        text("title", title, variant="h3"),
        *group_components,
        # The Card wrapper keeps the submit component reliable in the client.
        # Give it the same blue surface as its full-width Button, so the whole
        # visible card is a single obvious submit target rather than a white
        # card containing a smaller button.
        card(
            "submitCard",
            "submit",
            styles={
                "background-color": "#2273F7",
                "border-width": "0px",
                "border-radius": "32px",
                "padding": "0px",
            },
        ),
        button(
            "submit",
            "submitText",
            action_name=action_name,
            context_paths=field_paths,
            styles={"width": "100%", "border-radius": "32px"},
        ),
        text(
            "submitText",
            submit_label,
            styles={"color": "#FFFFFF", "width": "100%", "text-align": "center"},
        ),
        *(extra_components or []),
    ]
    # updateDataModel must land BEFORE updateComponents. The submit button's
    # action.context binds one data-model path per field; the client
    # evaluates every bound component's data-binding status at the next
    # flush, and if the button's very first flush sees any of those paths
    # still unresolved, it's classified "partially ready", rejected, and
    # orphaned -- permanently, since orphans in this state are never
    # re-attached later (confirmed against the client's virtual_dom source).
    # So every field path must already have a value by the time the button
    # component is first flushed, not after.
    messages = [create_surface(surface_id, send_data_model=True)]
    for field_id, default_value in (field_defaults or {}).items():
        if default_value is None:
            continue
        messages.append(update_data_model(surface_id, field_paths.get(field_id, f"/{field_id}/value"), default_value))
    messages.append(update_components(surface_id, components))
    return messages


def hotel_gallery_card(
    surface_id: str,
    title: str,
    hotels: list[dict[str, Any]],
    more_count: int = 0,
) -> list[dict[str, Any]]:
    """A titled vertical list of hotel results, each in its own Card with a
    photo, name, price/rating/class subtitle, and a "View Hotel" button that
    opens the hotel's real page externally (via ``open_url_button``) -- the
    user always finishes the actual booking there themselves, same handoff
    pattern as ``summary_card``'s ``link_url``.

    Each dict in ``hotels`` may have: ``name`` (required), ``image_urls``
    (a list -- rendered as a swipeable ``carousel()`` when it has more than
    one URL, a plain ``image()`` when it has exactly one), ``price_per_night``,
    ``rating``, ``reviews``, ``hotel_class``, ``description``, ``link``. Any
    field besides ``name`` is optional -- only what's actually present is
    rendered.

    ``more_count`` > 0 adds a "Show more..." link (bold, underlined text,
    no button chrome) below the list -- still a real in-app ``button()``
    under the hood (a "borderless" variant), not ``open_url_button``, so
    tapping it sends a ``show_more_hotels`` UI action back to the agent
    (see ``hotel_tools.show_hotel_results`` and the booking policy in
    ``agent.py``) -- lets the caller render only the first batch of results
    up front instead of every image in a long list all at once.
    """
    card_ids = [f"hotel{i}Card" for i in range(len(hotels))]
    item_components: list[dict[str, Any]] = []
    for i, hotel in enumerate(hotels):
        card_id = card_ids[i]
        outer_id = f"hotel{i}"
        text_col_id = f"{outer_id}TextCol"
        name_id = f"{outer_id}Name"
        subtitle_id = f"{outer_id}Subtitle"
        desc_id = f"{outer_id}Desc"
        media_id = f"{outer_id}Media"
        button_id = f"{outer_id}Button"
        button_text_id = f"{outer_id}ButtonText"

        subtitle_parts: list[str] = []
        if hotel.get("price_per_night"):
            subtitle_parts.append(f"{hotel['price_per_night']}/night")
        if hotel.get("rating"):
            rating_text = f"★ {hotel['rating']}"
            if hotel.get("reviews"):
                rating_text += f" ({hotel['reviews']:,} reviews)"
            subtitle_parts.append(rating_text)
        if hotel.get("hotel_class"):
            subtitle_parts.append(hotel["hotel_class"])
        subtitle_text = "  •  ".join(subtitle_parts)

        text_col_children = [name_id]
        if subtitle_text:
            text_col_children.append(subtitle_id)
        if hotel.get("description"):
            text_col_children.append(desc_id)
        if hotel.get("link"):
            text_col_children.append(button_id)

        # padding=0 on the card itself, same reasoning as info_list_card's
        # photo items: the image sits flush against the card edges, with the
        # text block below getting its own inset padding instead.
        item_components.append(card(card_id, outer_id, styles={"padding": "0px"}))
        image_urls = hotel.get("image_urls") or []
        if len(image_urls) > 1:
            item_components.append(column(outer_id, [media_id, text_col_id]))
            item_components.append(carousel(media_id, image_urls, styles={"width": "100%", "aspect-ratio": "16/9"}))
        elif image_urls:
            item_components.append(column(outer_id, [media_id, text_col_id]))
            item_components.append(image(media_id, image_urls[0], variant="header", fit="cover"))
        else:
            item_components.append(column(outer_id, [text_col_id]))
        item_components.append(column(text_col_id, text_col_children, styles={"padding": "16px", "gap": "8px"}))
        item_components.append(text(name_id, hotel["name"], variant="h3"))
        if subtitle_text:
            item_components.append(text(subtitle_id, subtitle_text, variant="body"))
        if hotel.get("description"):
            item_components.append(text(desc_id, hotel["description"], variant="caption", styles={"line-clamp": 0}))
        if hotel.get("link"):
            item_components.append(open_url_button(button_id, button_text_id, hotel["link"], styles={"width": "100%"}))
            item_components.append(
                text(
                    button_text_id,
                    "View Hotel",
                    styles={"color": "#FFFFFF", "width": "100%", "text-align": "center"},
                )
            )

    root_children = ["title", "list"]
    more_components: list[dict[str, Any]] = []
    if more_count > 0:
        root_children.append("moreButton")
        # A link, not a filled button -- "borderless" strips the pill
        # chrome _DEFAULT_BUTTON_STYLES normally adds, and the background/
        # padding/border-radius overrides below clear what's left of it, so
        # only the bold, underlined text itself reads as tappable.
        more_components.append(
            button(
                "moreButton",
                "moreButtonText",
                "show_more_hotels",
                variant="borderless",
                styles={
                    "width": "100%",
                    "background-color": "transparent",
                    "padding": "8px 0px",
                    "border-radius": "0px",
                },
            )
        )
        more_components.append(
            text(
                "moreButtonText",
                "Show more...",
                styles={
                    "color": "#2273F7",
                    "font-weight": "bold",
                    "text-decoration": "underline",
                    "width": "100%",
                    "text-align": "center",
                },
            )
        )

    components = [
        column("root", root_children, styles={"gap": "12px"}),
        text("title", title, variant="h3"),
        list_view("list", card_ids, styles={"gap": "12px"}),
        *more_components,
        *item_components,
    ]
    return [
        create_surface(surface_id),
        update_components(surface_id, components),
    ]


_FINANCE_MOVEMENT_COLORS: dict[str, str] = {"Up": "#16A34A", "Down": "#DC2626"}
_FINANCE_MOVEMENT_ARROWS: dict[str, str] = {"Up": "▲ ", "Down": "▼ "}


def finance_gallery_card(
    surface_id: str,
    title: str,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """A titled vertical list of finance results, each in its own Card with
    the security's name/ticker, current price, a colored change line (green
    for "Up", red for "Down"), a real interactive price chart (native
    ``chart()``, colored to match the change line), and a "View on Google
    Finance" button that opens the real quote page externally (via
    ``open_url_button``).

    Each dict in ``items`` may have: ``title`` (required), ``stock``,
    ``exchange``, ``price``, ``change_text``, ``movement`` ('Up'/'Down'/
    'Flat' -- colors ``change_text`` and the chart line), ``as_of``,
    ``window``, ``description``, ``chart_x_axis``/``chart_values`` (paired
    lists -- both required together to render a chart), ``link``. Any field
    besides ``title`` is optional -- only what's actually present is rendered.
    """
    card_ids = [f"finance{i}Card" for i in range(len(items))]
    item_components: list[dict[str, Any]] = []
    for i, item in enumerate(items):
        card_id = card_ids[i]
        outer_id = f"finance{i}"
        name_id = f"{outer_id}Name"
        price_id = f"{outer_id}Price"
        change_id = f"{outer_id}Change"
        chart_id = f"{outer_id}Chart"
        desc_id = f"{outer_id}Desc"
        meta_id = f"{outer_id}Meta"
        button_id = f"{outer_id}Button"
        button_text_id = f"{outer_id}ButtonText"

        name_text = f"{item['title']} ({item['stock']})" if item.get("stock") else item["title"]

        meta_parts: list[str] = []
        if item.get("exchange"):
            meta_parts.append(item["exchange"])
        if item.get("as_of"):
            meta_parts.append(item["as_of"])
        if item.get("window"):
            meta_parts.append(item["window"])
        meta_text = "  •  ".join(meta_parts)

        outer_children = [name_id]
        if item.get("price"):
            outer_children.append(price_id)
        if item.get("change_text"):
            outer_children.append(change_id)
        has_chart = item.get("chart_x_axis") and item.get("chart_values")
        if has_chart:
            outer_children.append(chart_id)
        if meta_text:
            outer_children.append(meta_id)
        if item.get("description"):
            outer_children.append(desc_id)
        if item.get("link"):
            outer_children.append(button_id)

        item_components.append(card(card_id, outer_id))
        item_components.append(column(outer_id, outer_children, styles={"gap": "8px"}))
        item_components.append(text(name_id, name_text, variant="h3"))
        if item.get("price"):
            item_components.append(text(price_id, item["price"], variant="h2"))
        if item.get("change_text"):
            movement = item.get("movement")
            arrow = _FINANCE_MOVEMENT_ARROWS.get(movement, "")
            color = _FINANCE_MOVEMENT_COLORS.get(movement, "#6B7280")
            item_components.append(
                text(change_id, f"{arrow}{item['change_text']}", variant="body", styles={"color": color})
            )
        if has_chart:
            color = _FINANCE_MOVEMENT_COLORS.get(item.get("movement"), "#6B7280")
            item_components.append(
                chart(
                    chart_id,
                    "line",
                    series=[{"name": item["title"], "data": [{"value": v} for v in item["chart_values"]]}],
                    x_axis=item["chart_x_axis"],
                    styles={"width": "100%", "aspect-ratio": "2/1", "chartConfig": {"colors": [color]}},
                )
            )
        if meta_text:
            item_components.append(text(meta_id, meta_text, variant="caption"))
        if item.get("description"):
            item_components.append(text(desc_id, item["description"], variant="caption", styles={"line-clamp": 0}))
        if item.get("link"):
            item_components.append(open_url_button(button_id, button_text_id, item["link"], styles={"width": "100%"}))
            item_components.append(
                text(
                    button_text_id,
                    "View on Google Finance",
                    styles={"color": "#FFFFFF", "width": "100%", "text-align": "center"},
                )
            )

    components = [
        column("root", ["title", "list"], styles={"gap": "12px"}),
        text("title", title, variant="h3"),
        list_view("list", card_ids, styles={"gap": "12px"}),
        *item_components,
    ]
    return [
        create_surface(surface_id),
        update_components(surface_id, components),
    ]


def flight_gallery_card(
    surface_id: str,
    title: str,
    flights: list[dict[str, Any]],
    more_count: int = 0,
) -> list[dict[str, Any]]:
    """A titled vertical list of flight results, each in its own Card with a
    leading airline logo, airline name, price/stops/duration/class subtitle,
    a departure/arrival route+time line, and a "View Flights" button that
    opens Google Flights externally (via ``open_url_button``) for that route
    -- the user picks a fare and finishes booking there themselves, same
    handoff pattern as ``hotel_gallery_card``'s "View Hotel" button.

    Each dict in ``flights`` may have: ``airline`` (required), ``airline_logo``,
    ``price``, ``stops_label``, ``duration``, ``travel_class``,
    ``departure_airport``, ``departure_time``, ``arrival_airport``,
    ``arrival_time``, ``link``. Any field besides ``airline`` is optional --
    only what's actually present is rendered.

    ``more_count`` > 0 adds a "Show more..." link (bold, underlined text,
    no button chrome) below the list -- pressing it sends a
    ``show_more_flights`` UI action back to the agent, see
    ``flight_tools.show_flight_results`` and the booking policy in
    ``agent.py`` -- same pagination pattern as ``hotel_gallery_card``.
    """
    card_ids = [f"flight{i}Card" for i in range(len(flights))]
    item_components: list[dict[str, Any]] = []
    for i, flight in enumerate(flights):
        card_id = card_ids[i]
        outer_id = f"flight{i}"
        header_id = f"{outer_id}Header"
        logo_id = f"{outer_id}Logo"
        name_id = f"{outer_id}Name"
        subtitle_id = f"{outer_id}Subtitle"
        route_id = f"{outer_id}Route"
        button_id = f"{outer_id}Button"
        button_text_id = f"{outer_id}ButtonText"

        subtitle_parts: list[str] = []
        if flight.get("price"):
            subtitle_parts.append(flight["price"])
        if flight.get("stops_label"):
            subtitle_parts.append(flight["stops_label"])
        if flight.get("duration"):
            subtitle_parts.append(flight["duration"])
        if flight.get("travel_class"):
            subtitle_parts.append(flight["travel_class"])
        subtitle_text = "  •  ".join(subtitle_parts)

        route_parts: list[str] = []
        if flight.get("departure_airport"):
            departure = flight["departure_airport"]
            if flight.get("departure_time"):
                departure += f" {flight['departure_time']}"
            route_parts.append(departure)
        if flight.get("arrival_airport"):
            arrival = flight["arrival_airport"]
            if flight.get("arrival_time"):
                arrival += f" {flight['arrival_time']}"
            route_parts.append(arrival)
        route_text = "  →  ".join(route_parts)

        has_logo = bool(flight.get("airline_logo"))
        header_children = [logo_id, name_id] if has_logo else [name_id]
        outer_children = [header_id] if has_logo else [name_id]
        if subtitle_text:
            outer_children.append(subtitle_id)
        if route_text:
            outer_children.append(route_id)
        if flight.get("link"):
            outer_children.append(button_id)

        item_components.append(card(card_id, outer_id))
        item_components.append(column(outer_id, outer_children, styles={"gap": "8px"}))
        if has_logo:
            item_components.append(row(header_id, header_children, align="center", styles={"gap": "12px"}))
            item_components.append(
                image(
                    logo_id,
                    flight["airline_logo"],
                    variant="avatar",
                    fit="contain",
                    styles={"width": "32px", "height": "32px"},
                )
            )
        item_components.append(text(name_id, flight["airline"], variant="h3"))
        if subtitle_text:
            item_components.append(text(subtitle_id, subtitle_text, variant="body"))
        if route_text:
            item_components.append(text(route_id, route_text, variant="caption", styles={"line-clamp": 0}))
        if flight.get("link"):
            item_components.append(open_url_button(button_id, button_text_id, flight["link"], styles={"width": "100%"}))
            item_components.append(
                text(
                    button_text_id,
                    "View Flights",
                    styles={"color": "#FFFFFF", "width": "100%", "text-align": "center"},
                )
            )

    root_children = ["title", "list"]
    more_components: list[dict[str, Any]] = []
    if more_count > 0:
        root_children.append("moreButton")
        # A link, not a filled button -- see hotel_gallery_card's identical
        # "Show more" block above for why.
        more_components.append(
            button(
                "moreButton",
                "moreButtonText",
                "show_more_flights",
                variant="borderless",
                styles={
                    "width": "100%",
                    "background-color": "transparent",
                    "padding": "8px 0px",
                    "border-radius": "0px",
                },
            )
        )
        more_components.append(
            text(
                "moreButtonText",
                "Show more...",
                styles={
                    "color": "#2273F7",
                    "font-weight": "bold",
                    "text-decoration": "underline",
                    "width": "100%",
                    "text-align": "center",
                },
            )
        )

    components = [
        column("root", root_children, styles={"gap": "12px"}),
        text("title", title, variant="h3"),
        list_view("list", card_ids, styles={"gap": "12px"}),
        *more_components,
        *item_components,
    ]
    return [
        create_surface(surface_id),
        update_components(surface_id, components),
    ]
