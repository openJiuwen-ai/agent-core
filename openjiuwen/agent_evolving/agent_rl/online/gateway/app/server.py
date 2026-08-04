# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""FastAPI route binding for the online-RL gateway."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from fastapi import Body, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .http_helpers import build_upstream_headers, ensure_gateway_auth, stream_chat_response
from .request_context import require_messages, require_user_id, resolve_trace_id
from ...lora_runtime import build_lora_info, lora_id as build_lora_id
from ..trajectory import GatewayTrajectoryRuntime
from ..upstream import Forwarder, UpstreamGatewayClient
from ....storage.lora_repo import LoRAPublishRequest

logger = logging.getLogger("online_rl.gateway")


def _inject_latest_lora(
    *,
    body: dict[str, Any],
    user_id: str,
    lora_repo: Any = None,
    lora_default_policy: str = "disabled",
) -> dict[str, Any] | None:
    if lora_repo is None or lora_default_policy != "latest_by_user":
        return None
    latest_lora = lora_repo.get_latest(user_id)
    if latest_lora:
        body["model"] = user_id
        extra_body = body.get("extra_body")
        if isinstance(extra_body, dict):
            extra_body.pop("lora_name", None)
            if not extra_body:
                body.pop("extra_body", None)
        return build_lora_info(user_id, latest_lora, default_policy=lora_default_policy)
    return None


async def _forward_chat_completions(
    *,
    request: Request,
    body: dict[str, Any],
    config: Any,
    forwarder: Forwarder,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    trace_id = resolve_trace_id(request)
    messages = require_messages(body)
    require_user_id(request, config)
    upstream_headers = build_upstream_headers(request, llm_api_key=config.llm_api_key)

    logger.debug(
        "[Gateway %s] proxy_only messages=%d stream=%s",
        trace_id,
        len(messages),
        bool(body.get("stream", False)),
    )

    response_json = await forwarder.forward(body=body, headers=upstream_headers)
    logger.debug(
        "[Gateway] chat_completions cost_ms=%.1f",
        (time.perf_counter() - t0) * 1000,
    )
    return response_json


async def _snapshot_stats(*, trajectory_runtime: GatewayTrajectoryRuntime, total_requests: int) -> dict[str, Any]:
    trajectory_stats = await trajectory_runtime.snapshot_stats()
    return {
        "total_requests": total_requests,
        "total_samples": trajectory_stats["total_samples"],
        "trajectory_store_total": trajectory_stats["trajectory_store_total"],
        "trajectory_store_pending": trajectory_stats["trajectory_store_pending"],
    }


def _lora_id(user_id: str, version: str) -> str:
    return build_lora_id(user_id, version)


def _split_lora_id(adapter_id: str) -> tuple[str, str]:
    if ":" not in adapter_id:
        raise HTTPException(status_code=400, detail="lora_id must use '<model_id>:<version>'")
    user_id, version = adapter_id.rsplit(":", 1)
    if not user_id or not version:
        raise HTTPException(status_code=400, detail="invalid lora_id")
    return user_id, version


@dataclass(frozen=True)
class TrajectoryListFilters:
    model_id: str | None = None
    status: str | None = None
    user_id: str | None = None
    session_id: str | None = None
    task_id: str | None = None
    source: str | None = None
    policy_version: str | None = None
    limit: int = 100


def _optional_query(request: Request, name: str) -> str | None:
    value = request.query_params.get(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _int_query(request: Request, name: str, default: int) -> int:
    value = request.query_params.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{name} must be an integer") from exc


def _trajectory_list_filters(request: Request) -> TrajectoryListFilters:
    return TrajectoryListFilters(
        model_id=_optional_query(request, "model_id"),
        status=_optional_query(request, "status"),
        user_id=_optional_query(request, "user_id"),
        session_id=_optional_query(request, "session_id"),
        task_id=_optional_query(request, "task_id"),
        source=_optional_query(request, "source"),
        policy_version=_optional_query(request, "policy_version"),
        limit=_int_query(request, "limit", 100),
    )


def _lora_to_response(
    version: Any,
    *,
    latest_version: str | None = None,
    load_status: str = "not_loaded",
) -> dict[str, Any]:
    is_latest = latest_version == version.version
    path = Path(version.path)
    size_bytes = 0
    if path.exists():
        size_bytes = sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    return {
        "lora_id": _lora_id(version.user_id, version.version),
        "model_id": version.user_id,
        "base_model": version.base_model,
        "parent_lora_id": getattr(version, "parent_lora_id", ""),
        "parent_lora_version": getattr(version, "parent_lora_version", ""),
        "parent_lora_path": getattr(version, "parent_lora_path", ""),
        "version": version.version,
        "status": "latest" if is_latest else "versioned",
        "load_status": load_status,
        "availability": {
            "status": getattr(version, "availability_status", "pending"),
            "reason": getattr(version, "availability_reason", ""),
            "checked_at": (
                getattr(version, "availability_checked_at", None).isoformat()
                if getattr(version, "availability_checked_at", None)
                else None
            ),
        },
        "training_source": getattr(version, "training_source", "base_model"),
        "path": version.path,
        "size_bytes": size_bytes,
        "download_url": f"/v1/rl/lora/{_lora_id(version.user_id, version.version)}:download",
        "source": {
            # Reserved for future richer TrainJob linkage.
            "type": "unknown",
            "job_id": None,
        },
        "metrics": {
            "reward_mean": version.reward_avg,
        },
        "trajectory_count": version.trajectory_count,
        "created_at": version.created_at.isoformat(),
        "latest_at": version.created_at.isoformat() if is_latest else None,
        "metadata": {},
    }


def _require_lora_repo(lora_repo: Any) -> Any:
    if lora_repo is None:
        raise HTTPException(status_code=503, detail="LoRA repository is not configured")
    return lora_repo


def build_gateway_app(
    *,
    config: Any,
    forwarder: Forwarder,
    upstream_client: UpstreamGatewayClient,
    trajectory_runtime: GatewayTrajectoryRuntime,
    close_resources: Callable[[], Awaitable[None]],
    lora_repo: Any = None,
) -> FastAPI:
    """Assemble FastAPI app and bind gateway public/internal routes."""
    metrics_lock = asyncio.Lock()
    total_requests = 0

    async def _inc_request_counter() -> None:
        nonlocal total_requests
        async with metrics_lock:
            total_requests += 1

    async def _loaded_lora_roots() -> dict[str, str]:
        try:
            response = await upstream_client.request(
                method="GET",
                url=f"{config.llm_url.rstrip('/')}/v1/models",
                params={},
                headers={},
                content=b"",
            )
            response.raise_for_status()
            models = response.json().get("data", [])
        except Exception as exc:
            logger.debug("failed to query upstream LoRA models: %s", exc)
            return {}
        return {
            str(item.get("id")): str(item.get("root") or "")
            for item in models
            if isinstance(item, dict) and item.get("id") and item.get("root")
        }

    async def _lora_load_status(version: Any) -> str:
        loaded_roots = await _loaded_lora_roots()
        loaded_path = loaded_roots.get(version.user_id)
        if loaded_path and Path(loaded_path).resolve() == Path(version.path).resolve():
            return "loaded"
        return "not_loaded"

    async def _ensure_lora_loaded(version: Any) -> tuple[bool, str, str]:
        lora_path = str(getattr(version, "path", "") or "").strip()
        if not lora_path:
            return False, "not_loaded", "lora_path is empty"

        loaded_roots = await _loaded_lora_roots()
        loaded_path = loaded_roots.get(version.user_id)
        if loaded_path and Path(loaded_path).resolve() == Path(lora_path).resolve():
            return True, "loaded", ""

        try:
            response = await upstream_client.request(
                method="POST",
                url=f"{config.llm_url.rstrip('/')}/v1/load_lora_adapter",
                params={},
                headers={"content-type": "application/json"},
                content=json.dumps({
                    "lora_name": version.user_id,
                    "lora_path": lora_path,
                    "load_inplace": True,
                }).encode("utf-8"),
            )
        except Exception as exc:
            logger.warning(
                "failed to hot-load LoRA user=%s path=%s: %r",
                version.user_id,
                lora_path,
                exc,
            )
            return False, "load_failed", repr(exc)

        if response.status_code >= 400:
            return False, "load_failed", response.text[:400]
        return True, "loaded", ""

    async def _effective_lora_response(
        *,
        model_id: str,
        ensure_loaded: bool,
    ) -> dict[str, Any]:
        policy = getattr(config, "lora_default_policy", "disabled")
        if policy != "latest_by_user":
            return {"enabled": False, "reason": "policy_disabled", "default_policy": policy}

        repo = _require_lora_repo(lora_repo)
        version = repo.get_latest(model_id)
        if version is None:
            return {"enabled": False, "reason": "latest_lora_not_found", "default_policy": policy}

        lora_info = build_lora_info(model_id, version, default_policy=policy)
        load_status = await _lora_load_status(version)
        if ensure_loaded and load_status != "loaded":
            ok, load_status, reason = await _ensure_lora_loaded(version)
            if not ok:
                return {
                    **lora_info,
                    "enabled": False,
                    "load_status": load_status,
                    "reason": reason,
                }

        return {
            **lora_info,
            "enabled": True,
            "load_status": load_status,
            "reason": "",
        }

    async def _lora_response(version: Any, *, latest_version: str | None = None) -> dict[str, Any]:
        return _lora_to_response(
            version,
            latest_version=latest_version,
            load_status=await _lora_load_status(version),
        )

    @asynccontextmanager
    async def _lifespan(_: FastAPI):
        try:
            yield
        finally:
            await close_resources()

    app = FastAPI(title="Online-RL Gateway", lifespan=_lifespan)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok"}

    @app.get("/v1/gateway/stats")
    async def gateway_stats(authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
        await ensure_gateway_auth(config.gateway_api_key, authorization)
        async with metrics_lock:
            request_count = total_requests
        return await _snapshot_stats(
            trajectory_runtime=trajectory_runtime,
            total_requests=request_count,
        )

    @app.post("/v1/gateway/upload/batch")
    async def create_upload_batch(
        payload: dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        await ensure_gateway_auth(config.gateway_api_key, authorization)
        logger.debug(
            "[GatewayAPI] rail_upload session=%s trajectory=%s samples=%s",
            payload.get("session_id"),
            payload.get("trajectory_id"),
            len(payload.get("samples") or []) if isinstance(payload.get("samples"), list) else None,
        )
        try:
            result = await trajectory_runtime.rail_ingestor.ingest_rail_batch(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "result": result}

    @app.get("/v1/rl/health")
    async def rl_health(authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
        await ensure_gateway_auth(config.gateway_api_key, authorization)
        stats = await trajectory_runtime.snapshot_stats()
        return {
            "status": "ready",
            "gateway": {"status": "ready", "version": "0.1.0"},
            "active_rl_model": config.model_id,
            "services": {
                "trajectory_manager": stats.get("trajectory_store_backend"),
                "rl_scheduler": "external",
                "upstream_infer": "configured" if config.llm_url else "unknown",
            },
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    @app.post("/v1/rl/trajectories:batchCreate")
    async def create_trajectories(
        payload: dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        await ensure_gateway_auth(config.gateway_api_key, authorization)
        try:
            return await trajectory_runtime.batch_create_trajectories(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/v1/rl/trajectories/stats")
    async def trajectory_stats(
        model_id: Optional[str] = Query(default=None),
        user_id: Optional[str] = Query(default=None),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        await ensure_gateway_auth(config.gateway_api_key, authorization)
        stats = await trajectory_runtime.trajectory_management_stats(model_id=model_id, user_id=user_id)
        if model_id:
            stats["model_id"] = model_id
        if user_id:
            stats["user_id"] = user_id
        return stats

    @app.get("/v1/rl/trajectories")
    async def list_trajectories(
        request: Request,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        await ensure_gateway_auth(config.gateway_api_key, authorization)
        filters = _trajectory_list_filters(request)
        return await trajectory_runtime.list_trajectories(
            model_id=filters.model_id,
            status=filters.status,
            user_id=filters.user_id,
            session_id=filters.session_id,
            task_id=filters.task_id,
            source=filters.source,
            policy_version=filters.policy_version,
            limit=filters.limit,
        )

    @app.get("/v1/rl/trajectories/{trajectory_id}")
    async def get_trajectory(
        trajectory_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        await ensure_gateway_auth(config.gateway_api_key, authorization)
        trajectory = await trajectory_runtime.get_trajectory(trajectory_id)
        if trajectory is None:
            raise HTTPException(status_code=404, detail="trajectory not found")
        return trajectory

    @app.patch("/v1/rl/trajectories/{trajectory_id}")
    async def patch_trajectory(
        trajectory_id: str,
        updates: dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        await ensure_gateway_auth(config.gateway_api_key, authorization)
        trajectory = await trajectory_runtime.patch_trajectory(trajectory_id, updates)
        if trajectory is None:
            raise HTTPException(status_code=404, detail="trajectory not found")
        return trajectory

    @app.delete("/v1/rl/trajectories/{trajectory_id}")
    async def delete_trajectory(
        trajectory_id: str,
        force: bool = Query(default=False),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        await ensure_gateway_auth(config.gateway_api_key, authorization)
        try:
            deleted = await trajectory_runtime.delete_trajectory(trajectory_id, force=force)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="trajectory not found")
        return {"trajectory_id": trajectory_id, "deleted": True}

    @app.get("/v1/rl/lora")
    async def list_lora(
        model_id: Optional[str] = Query(default=None),
        limit: int = Query(default=20),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        await ensure_gateway_auth(config.gateway_api_key, authorization)
        repo = _require_lora_repo(lora_repo)
        users = [model_id] if model_id else repo.list_users()
        items: list[dict[str, Any]] = []
        for user in users:
            latest = repo.get_latest(user)
            latest_version = latest.version if latest else None
            for version in repo.list_versions(user):
                items.append(await _lora_response(version, latest_version=latest_version))
                if len(items) >= max(1, int(limit)):
                    return {"items": items, "next_cursor": None}
        return {"items": items, "next_cursor": None}

    @app.get("/v1/rl/lora/latest")
    async def get_latest_lora(
        model_id: str = Query(...),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        await ensure_gateway_auth(config.gateway_api_key, authorization)
        repo = _require_lora_repo(lora_repo)
        latest = repo.get_latest(model_id)
        if latest is None:
            raise HTTPException(status_code=404, detail="latest LoRA not found")
        return await _lora_response(latest, latest_version=latest.version)

    @app.post("/v1/rl/lora/effective")
    async def get_effective_lora(
        payload: dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        await ensure_gateway_auth(config.gateway_api_key, authorization)
        model_id = str(payload.get("model_id") or "").strip()
        if not model_id:
            raise HTTPException(status_code=400, detail="model_id is required")
        return await _effective_lora_response(
            model_id=model_id,
            ensure_loaded=bool(payload.get("ensure_loaded", True)),
        )

    @app.post("/v1/rl/lora")
    async def register_lora(
        payload: dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        await ensure_gateway_auth(config.gateway_api_key, authorization)
        repo = _require_lora_repo(lora_repo)
        model_id = str(payload.get("model_id") or "").strip()
        lora_path = str(payload.get("lora_path") or payload.get("path") or "").strip()
        if not model_id:
            raise HTTPException(status_code=400, detail="model_id is required")
        if not lora_path or not os.path.exists(lora_path):
            raise HTTPException(status_code=400, detail="lora_path is required and must exist")
        metadata = dict(payload.get("metadata") or {})
        if isinstance(payload.get("metrics"), dict):
            metadata.update(payload["metrics"])
        if isinstance(payload.get("source"), dict):
            metadata["source"] = payload["source"]
        version = repo.publish(
            LoRAPublishRequest(
                user_id=model_id,
                lora_path=lora_path,
                metadata=metadata,
                base_model=str(payload.get("base_model") or ""),
            )
        )
        if payload.get("set_latest") is False:
            # Current repository publish updates latest atomically. This field is
            # kept for API compatibility and future non-latest import support.
            logger.info("set_latest=false requested, but current LoRARepository publish always updates latest")
        return await _lora_response(version, latest_version=repo.get_latest(model_id).version)

    @app.get("/v1/rl/lora/{lora_id}:download")
    async def download_lora(
        lora_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        await ensure_gateway_auth(config.gateway_api_key, authorization)
        repo = _require_lora_repo(lora_repo)
        user_id, version = _split_lora_id(lora_id)
        item = repo.get_version(user_id, version)
        if item is None:
            raise HTTPException(status_code=404, detail="LoRA not found")
        # The first implementation returns local path metadata. File streaming or
        # signed URLs are reserved for object-store based repos.
        return await _lora_response(item, latest_version=(repo.get_latest(user_id) or item).version)

    @app.get("/v1/rl/lora/{lora_id}")
    async def get_lora(
        lora_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        await ensure_gateway_auth(config.gateway_api_key, authorization)
        repo = _require_lora_repo(lora_repo)
        user_id, version = _split_lora_id(lora_id)
        item = repo.get_version(user_id, version)
        if item is None:
            raise HTTPException(status_code=404, detail="LoRA not found")
        latest = repo.get_latest(user_id)
        return await _lora_response(item, latest_version=latest.version if latest else None)

    @app.post("/v1/rl/lora/{lora_id}:setLatest")
    async def set_latest_lora(
        lora_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        await ensure_gateway_auth(config.gateway_api_key, authorization)
        repo = _require_lora_repo(lora_repo)
        user_id, version = _split_lora_id(lora_id)
        try:
            item = repo.set_latest(user_id, version)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return await _lora_response(item, latest_version=item.version)

    @app.post("/v1/rl/lora/{lora_id}:setAvailability")
    async def set_lora_availability(
        lora_id: str,
        payload: Optional[dict[str, Any]] = Body(default=None),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        await ensure_gateway_auth(config.gateway_api_key, authorization)
        repo = _require_lora_repo(lora_repo)
        user_id, version = _split_lora_id(lora_id)
        payload = payload or {}
        available = bool(payload.get("available", True))
        reason = str(payload.get("reason") or "")
        try:
            item = repo.set_availability(user_id, version, available=available, reason=reason)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return await _lora_response(item, latest_version=(repo.get_latest(user_id) or item).version)

    @app.delete("/v1/rl/lora/{lora_id}")
    async def delete_lora(
        lora_id: str,
        force: bool = Query(default=False),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        await ensure_gateway_auth(config.gateway_api_key, authorization)
        repo = _require_lora_repo(lora_repo)
        user_id, version = _split_lora_id(lora_id)
        try:
            repo.delete_version(user_id, version, force=force)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"lora_id": lora_id, "deleted": True}

    @app.post("/v1/chat/completions")
    async def chat_completions(
        request: Request,
        authorization: Optional[str] = Header(default=None),
    ):
        await ensure_gateway_auth(config.gateway_api_key, authorization)
        await _inc_request_counter()
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"invalid json: {exc}") from exc

        user_id = require_user_id(request, config)
        lora_info = _inject_latest_lora(
            body=body,
            user_id=user_id,
            lora_repo=lora_repo,
            lora_default_policy=getattr(config, "lora_default_policy", "disabled"),
        )
        if lora_info:
            logger.info(
                "[Gateway] applied LoRA adapter user=%s version=%s path=%s via model field",
                user_id,
                lora_info.get("version"),
                lora_info.get("path"),
            )

        client_wants_stream = bool(body.pop("stream", False))

        response_json = await _forward_chat_completions(
            request=request,
            body=body,
            config=config,
            forwarder=forwarder,
        )
        if lora_info:
            response_json["rl_lora"] = lora_info

        if client_wants_stream:
            return StreamingResponse(
                stream_chat_response(response_json, model_id=config.model_id),
                media_type="text/event-stream",
            )
        return JSONResponse(content=response_json)

    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def proxy_other(
        path: str,
        request: Request,
        authorization: Optional[str] = Header(default=None),
    ):
        await ensure_gateway_auth(config.gateway_api_key, authorization)
        await _inc_request_counter()
        target_url = f"{config.llm_url.rstrip('/')}/{path}"
        upstream_headers = build_upstream_headers(request, llm_api_key=config.llm_api_key)
        body_bytes = await request.body()
        try:
            resp = await upstream_client.request(
                method=request.method,
                url=target_url,
                params=dict(request.query_params),
                headers=upstream_headers,
                content=body_bytes,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"proxy failed: {exc}") from exc

        response_headers = {
            key: value
            for key, value in resp.headers.items()
            if key.lower() not in {"content-length", "transfer-encoding", "connection", "content-encoding"}
        }
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=response_headers,
            media_type=resp.headers.get("content-type"),
        )

    return app
