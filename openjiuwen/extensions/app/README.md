# A2UI ReAct Agent Extension

A single [ReAct agent](../../../examples/react_agent) built with openJiuwen,
exposed over one WebSocket endpoint that streams A2UI (GenUI) JSON. It
speaks the envelope protocol implemented by the Flutter `a2ui_mobile_app`
client (`lib/services/ws_service.dart` + `lib/services/chat_bridge.dart`),
so that app can connect to this server with no client-side changes.

## How it fits together

```
Flutter app (WsService/ChatBridge)  <--WebSocket-->  openjiuwen/extensions/app/server.py
                                                        |
                                                        v
                                                 ReActAgent (agent.py)
                                                    - get_current_time
                                                    - show_card  -> genui.py builds
                                                                    A2UI JSON
```

1. Client sends `{"type": "chat.start", "conversationId": ..., "payload": {"text": "..."}}`.
2. Server runs the ReAct agent via `Runner.run_agent_streaming`.
3. `rails.A2uiToolEventRail` emits raw `tool_call`/`tool_result` chunks (kept
   un-stringified so `show_card`'s `genui` payload survives intact).
4. `ws_session._translate` turns those chunks into wire events: `chat.accepted`,
   `tool.started`, `tool.finished`, `tool.output`, one `genui` event per A2UI
   message, `chat.token` for the final text answer, then `chat.completed`.
5. The Flutter client's `ChatBridge` feeds `chat.token` into its
   `A2uiTransportAdapter` as streamed text and `genui` events into it as
   `A2uiMessage`s, which the `genui` package renders as UI surfaces.

## Run it

```bash
cp openjiuwen/extensions/app/.env.example openjiuwen/extensions/app/.env   # fill in API_KEY etc., or export env vars directly
uv run python -m openjiuwen.extensions.app.server
# or: uv run uvicorn openjiuwen.extensions.app.server:create_app --factory --host 0.0.0.0 --port 8090
```

Point the Flutter app at it:

```bash
# Android emulator -> host machine
flutter run --dart-define=A2UI_WS_URL=ws://10.0.2.2:8090/ws

# Physical device / desktop -> use your machine's LAN IP
flutter run --dart-define=A2UI_WS_URL=ws://192.168.1.23:8090/ws
```

Then send a message like "what's a good 3-step morning routine?" -- the
agent should reply with text and a rendered card.

## Files

- `config.py` -- env-driven config (model creds, host/port, catalog id).
- `models.py` -- the `Envelope` wire schema (`id`/`type`/`conversationId`/`timestamp`/`payload`).
- `genui.py` -- A2UI v0.9 message builders (`createSurface`, `updateComponents`, `summary_card`, ...).
- `tools/uiux_tools.py` -- the agent's general tools (`show_card`, `show_info_list`, `ask_preferences_form`, ...) and `ALL_TOOLS`, which assembles every tool (including from `tools/image_tools.py`/`tools/video_tools.py`/`tools/map_tools.py`/`tools/hotel_tools.py`) for `agent.py` to register.
- `tools/image_tools.py` -- `search_images` (SerpApi Google Images Light, keyword search) and `fetch_page_image` (og:image scrape of a known page), for getting a real image URL.
- `tools/video_tools.py` -- `search_youtube_videos`/`fetch_video_source`/`show_video_clips`, for finding and rendering playable video clips.
- `tools/map_tools.py` -- `geocode_place`/`show_map`, for resolving real places (Google Places API, incl. rating/photo when available) and rendering them as an interactive map (Google Maps JavaScript API, via `/map-embed`) with tappable pins whose info window shows the place's name, photo, and rating.
- `tools/hotel_tools.py` -- `search_hotels`/`show_hotel_results`, for finding real, bookable hotels (SerpApi Google Hotels engine) and rendering them as a gallery of cards, each handing off to the hotel's real page via a "View Hotel" button. Falls back to the general `free_search`/`browser_inspect_page` booking flow when unavailable or no results (see `agent.py`'s booking policy).
- `tools/browser_tools.py` -- `browser_inspect_page`, a read-only headless-browser fallback for JS-rendered pages.
- `rails.py` -- `A2uiToolEventRail`, captures raw tool results for the WS layer.
- `agent.py` -- builds and configures the `ReActAgent`.
- `ws_session.py` -- `ConnectionSession` + the OutputSchema-chunk-to-wire-event translator.
- `server.py` -- FastAPI app factory + `/ws` endpoint + entrypoint. Also serves two small HTML pages the client's custom WebView components load directly: `/youtube-embed` (wraps a YouTube URL in a real `<iframe>`) and `/map-embed` (an interactive Google Maps JavaScript API page with default markers, see `tools/map_tools.py`).

## Extending

- Add more tools in `tools/uiux_tools.py`; any tool that returns
  `{"text": ..., "genui": [...]}` will have its `genui` messages streamed to
  the client automatically -- no changes needed elsewhere.
- Swap `show_card`'s single title/body card for richer basic-catalog
  components (`Row`, list layouts, etc.) by building more helpers in
  `genui.py`.
- This extension has no REST layer or auth -- swap in real auth
  (`token` query param on `/ws`) before shipping.
