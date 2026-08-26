# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""A2UI ReAct agent server: a single openJiuwen ReActAgent exposed over one
WebSocket endpoint, streaming A2UI (GenUI) JSON to any client that speaks
the envelope protocol -- in particular the Flutter ``a2ui_mobile_app``.

Run:
    python -m openjiuwen.extensions.app.server
    # or
    uvicorn openjiuwen.extensions.app.server:create_app --factory --host 0.0.0.0 --port 8090

Then point the Flutter app at it, e.g.:
    flutter run --dart-define=A2UI_WS_URL=wss://10.0.2.2:8090/ws

Serves over TLS (wss://) whenever certs/server.{crt,key} exist (see
certs/generate.sh) -- generate them once and the server picks them up
automatically. Falls back to plain ws:// with a warning if they're missing,
so a fresh checkout without generated certs still runs for local dev.
"""

import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from openjiuwen.core.runner import Runner
from openjiuwen.extensions.app import config as app_config
from openjiuwen.extensions.app.agent import build_agent
from openjiuwen.extensions.app.tools.map_tools import MAP_EMBED_ROUTE_PATH, MapPlace, render_map_embed_html
from openjiuwen.extensions.app.ws_session import ConnectionSession


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await Runner.start()
        app.state.agent = await build_agent()
        yield
        await Runner.stop()

    app = FastAPI(
        title="A2UI ReAct Agent",
        description=(
            "Minimal single-ReActAgent WebSocket server built on openJiuwen, speaking the A2UI/GenUI envelope protocol."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket, token: str = "") -> None:
        # Dev-mode: no real auth, matching the Flutter client which connects
        # with no token today. Swap in real auth before shipping this.
        user_id = token or "dev-user"
        session = ConnectionSession(websocket, app.state.agent, user_id)
        await session.run()

    @app.get("/youtube-embed")
    async def youtube_embed_page(url: str = "") -> HTMLResponse:
        # Wraps a YouTube embed URL in a real page with a real <iframe> --
        # see YouTubeWebComponent.ets's own comment for why: YouTube's player
        # rejects a WebView navigated straight to the embed URL (no parent
        # frame at all) as part of its embed validation.
        safe = url.replace('&', '&amp;').replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
        html = (
            '<!DOCTYPE html><html><head>'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<style>html,body{margin:0;padding:0;height:100%;overflow:hidden;background:#000}'
            'iframe{width:100%;height:100%;border:0;display:block}</style>'
            '</head><body>'
            '<iframe src="' + safe + '" allow="autoplay;encrypted-media;picture-in-picture" allowfullscreen></iframe>'
            '</body></html>'
        )
        return HTMLResponse(content=html)

    @app.get(MAP_EMBED_ROUTE_PATH)
    async def map_embed_page(data: str = "") -> HTMLResponse:
        # See map_tools.show_map: `data` is a URL-encoded JSON array of
        # {"label", "lat", "lng"} places, built server-side from real
        # `geocode_place` results (FastAPI already URL-decodes query params).
        try:
            places = [MapPlace(**p) for p in json.loads(data)]
        except Exception:  # noqa: BLE001 -- malformed/missing data, not a server error
            return HTMLResponse(content="Invalid map data.", status_code=400)
        if not places:
            return HTMLResponse(content="No places to show.", status_code=400)
        html = render_map_embed_html(places, app_config.get("GOOGLE_MAPS_API_KEY"))
        return HTMLResponse(content=html)

    return app


if __name__ == "__main__":
    import uvicorn

    certfile = app_config.get("SSL_CERTFILE")
    keyfile = app_config.get("SSL_KEYFILE")
    ssl_kwargs: dict[str, str] = {}
    if certfile and keyfile and os.path.exists(certfile) and os.path.exists(keyfile):
        ssl_kwargs = {"ssl_certfile": certfile, "ssl_keyfile": keyfile}
        logging.info("TLS enabled: serving wss:// with cert %s", certfile)
    else:
        logging.warning(
            "No TLS cert/key found at %s / %s -- serving plain ws:// "
            "(run certs/generate.sh to enable encryption)",
            certfile,
            keyfile,
        )

    uvicorn.run(
        create_app(),
        host=app_config.get("HOST"),
        port=app_config.get("PORT"),
        **ssl_kwargs,
    )
