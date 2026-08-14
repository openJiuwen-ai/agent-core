#!/usr/bin/env python3
# coding: utf-8

"""Trigger one manual SFT training task on the target gateway."""

from __future__ import annotations

import argparse
import json
import os
from typing import Any
import urllib.error
import urllib.request


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gateway-url",
        default=os.getenv("RL_GATEWAY_URL") or os.getenv("TRAJECTORY_GATEWAY_URL") or "",
        help="Target gateway URL on the training machine.",
    )
    parser.add_argument("--gateway-api-key", default=os.getenv("TRAJECTORY_GATEWAY_API_KEY", ""))
    parser.add_argument("--user-id", default=os.getenv("RL_ONLINE_TENANT_ID") or os.getenv("WEB_USER_ID") or "local-web-user")
    parser.add_argument("--sample-count", type=int, default=int(os.getenv("SFT_IMPORT_SAMPLE_COUNT", "0")))
    parser.add_argument("--task-endpoint", default="/v1/training/tasks")
    parser.add_argument(
        "--metadata",
        default="{}",
        help="JSON object attached to the training task metadata field.",
    )
    return parser.parse_args()


def _headers(api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _request_json(*, gateway_url: str, path: str, payload: dict[str, Any], api_key: str) -> dict[str, Any]:
    url = f"{gateway_url.rstrip('/')}/{path.lstrip('/')}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=_headers(api_key),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"request failed status={exc.code} url={url} body={body}") from exc
    return json.loads(body) if body else {}


def main() -> int:
    args = parse_args()
    if not args.gateway_url:
        raise ValueError("missing --gateway-url or RL_GATEWAY_URL")

    try:
        metadata = json.loads(args.metadata)
    except json.JSONDecodeError as exc:
        raise ValueError("--metadata must be valid JSON") from exc
    if not isinstance(metadata, dict):
        raise ValueError("--metadata must be a JSON object")

    task = _request_json(
        gateway_url=args.gateway_url,
        path=args.task_endpoint,
        payload={
            "user_id": args.user_id,
            "sample_count": int(args.sample_count),
            "metadata": metadata,
        },
        api_key=args.gateway_api_key,
    )
    print(json.dumps(task, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
