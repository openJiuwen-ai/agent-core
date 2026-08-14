#!/usr/bin/env python
"""Launch vLLM with a local Prometheus routing compatibility patch."""

from __future__ import annotations

import runpy
import sys

from starlette.routing import Match, Mount

import prometheus_fastapi_instrumentator.routing as prom_routing


def _safe_get_route_name(scope, routes, route_name=None):
    for route in routes:
        match, child_scope = route.matches(scope)
        route_path = getattr(route, "path", None)
        if route_path is None:
            continue
        if match == Match.FULL:
            route_name = route_path
            child_scope = {**scope, **child_scope}
            if isinstance(route, Mount) and route.routes:
                child_route_name = _safe_get_route_name(child_scope, route.routes, route_name)
                route_name = None if child_route_name is None else route_name + child_route_name
            return route_name
        if match == Match.PARTIAL and route_name is None:
            route_name = route_path
    return None


def main() -> None:
    prom_routing._get_route_name = _safe_get_route_name
    sys.argv[0] = "vllm.entrypoints.openai.api_server"
    runpy.run_module("vllm.entrypoints.openai.api_server", run_name="__main__")


if __name__ == "__main__":
    main()
