#!/usr/bin/env python3
# coding: utf-8

"""Import a portable SFT sample package into a target gateway."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any
import urllib.error
import urllib.request


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", help="JSON package produced by export_sft_samples.py.")
    parser.add_argument(
        "--gateway-url",
        default=os.getenv("RL_GATEWAY_URL") or os.getenv("TRAJECTORY_GATEWAY_URL") or "",
        help="Target gateway URL on the training machine.",
    )
    parser.add_argument("--gateway-api-key", default=os.getenv("TRAJECTORY_GATEWAY_API_KEY", ""))
    parser.add_argument(
        "--user-id",
        default="",
        help="Override imported sample user_id; defaults to the package/source user_id.",
    )
    parser.add_argument(
        "--trigger-training",
        action="store_true",
        help="Create a manual /v1/training/tasks task after import.",
    )
    parser.add_argument(
        "--task-endpoint",
        default="/v1/training/tasks",
        help="Manual training task endpoint path.",
    )
    return parser.parse_args()


def _load_package(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("SFT transfer package must be a JSON object")
    if payload.get("protocol_version") != "sft-transfer-v1":
        raise ValueError("unsupported package protocol_version; expected sft-transfer-v1")
    samples = payload.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("SFT transfer package must contain a non-empty samples list")
    if not all(isinstance(sample, dict) for sample in samples):
        raise ValueError("all imported samples must be JSON objects")
    return payload


def _headers(api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _request_json(
    *,
    gateway_url: str,
    path: str,
    payload: dict[str, Any],
    api_key: str,
    timeout: int = 60,
) -> dict[str, Any]:
    url = f"{gateway_url.rstrip('/')}/{path.lstrip('/')}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=_headers(api_key),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"request failed status={exc.code} url={url} body={body}") from exc
    return json.loads(body) if body else {}


def _sample_for_import(sample: dict[str, Any], *, user_id: str) -> dict[str, Any]:
    out = dict(sample)
    out.pop("_store_status", None)
    out["protocol_version"] = "sft-sample-v1"
    if user_id:
        out["user_id"] = user_id
    return out


def _resolve_user_id(args: argparse.Namespace, package: dict[str, Any]) -> str:
    if args.user_id:
        return args.user_id
    source = package.get("source") if isinstance(package.get("source"), dict) else {}
    return str(source.get("user_id") or "").strip()


def main() -> int:
    args = parse_args()
    if not args.gateway_url:
        raise ValueError("missing --gateway-url or RL_GATEWAY_URL")

    package_path = Path(args.package).expanduser().resolve()
    package = _load_package(package_path)
    samples = package["samples"]
    user_id = _resolve_user_id(args, package)

    accepted = 0
    rejected = 0
    for idx, sample in enumerate(samples):
        payload = _sample_for_import(sample, user_id=user_id)
        result = _request_json(
            gateway_url=args.gateway_url,
            path="/v1/gateway/upload/batch",
            payload=payload,
            api_key=args.gateway_api_key,
        )
        import_result = result.get("result") if isinstance(result.get("result"), dict) else result
        accepted += int(import_result.get("accepted") or 0)
        rejected += int(import_result.get("rejected") or 0)
        if int(import_result.get("rejected") or 0):
            raise RuntimeError(f"sample[{idx}] import rejected: {import_result}")

    print(f"imported={package_path}")
    print(f"accepted={accepted} rejected={rejected} user_id={user_id or '<sample-user>'}")

    if args.trigger_training:
        task = _request_json(
            gateway_url=args.gateway_url,
            path=args.task_endpoint,
            payload={
                "user_id": user_id,
                "sample_count": accepted,
                "metadata": {
                    "source": "sft-transfer-import",
                    "package": str(package_path),
                    "package_protocol_version": package.get("protocol_version"),
                    "imported_samples": accepted,
                },
            },
            api_key=args.gateway_api_key,
        )
        print("training_task=" + json.dumps(task, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
