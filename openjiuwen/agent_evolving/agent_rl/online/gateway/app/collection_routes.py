# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""HTTP adapter for gateway collection-session lifecycle."""

from __future__ import annotations

from typing import Any, Awaitable, Optional

from fastapi import APIRouter, Body, Header, HTTPException

from openjiuwen.agent_evolving.agent_rl.online.gateway.collector.types import (
    CollectionSessionError,
    CollectionSessionErrorCode,
    CollectionSessionManager,
    CollectionSessionRecord,
    CollectionSessionSpec,
    RewardMode,
)

from ..trajectory.task_reward import TaskReward
from .http_helpers import ensure_gateway_auth


def _require_collection_manager(manager: CollectionSessionManager | None) -> CollectionSessionManager:
    if manager is None:
        raise HTTPException(status_code=503, detail="gateway collection is disabled")
    return manager


async def _call_collection_transition(
    transition: Awaitable[CollectionSessionRecord],
) -> CollectionSessionRecord:
    try:
        return await transition
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def create_collection_router(
    *,
    config: Any,
    collection_manager: CollectionSessionManager | None,
) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/gateway/collection/sessions")
    async def create_collection_session(
        payload: dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        await ensure_gateway_auth(config.gateway_api_key, authorization)
        manager = _require_collection_manager(collection_manager)
        try:
            reward_mode = RewardMode(payload.get("reward_mode", RewardMode.DELAYED_FEEDBACK.value))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if payload.get("collection_mode", "gateway") != "gateway":
            raise HTTPException(
                status_code=400,
                detail="gateway collection sessions require collection_mode=gateway",
            )
        try:
            spec = CollectionSessionSpec(
                session_id=str(payload["session_id"]),
                model_id=str(payload.get("model_id") or config.model_id),
                tokenizer_revision=str(payload["tokenizer_revision"]),
                template_revision=str(payload["template_revision"]),
                reward_mode=reward_mode,
            )
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=f"missing field: {exc.args[0]}") from exc
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        record = await _call_collection_transition(manager.create_session(spec))
        return {"ok": True, "session": record.to_json()}

    @router.get("/v1/gateway/collection/sessions/{session_id}")
    async def get_collection_session(
        session_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        await ensure_gateway_auth(config.gateway_api_key, authorization)
        manager = _require_collection_manager(collection_manager)
        record = await manager.get_session(session_id)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown collection session")
        return {"ok": True, "session": record.to_json()}

    @router.post("/v1/gateway/collection/sessions/{session_id}/finalize")
    async def finalize_collection_session(
        session_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        await ensure_gateway_auth(config.gateway_api_key, authorization)
        manager = _require_collection_manager(collection_manager)
        record = await _call_collection_transition(manager.finalize_session(session_id))
        return {"ok": True, "session": record.to_json()}

    @router.post("/v1/gateway/collection/sessions/{session_id}/abort")
    async def abort_collection_session(
        session_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        await ensure_gateway_auth(config.gateway_api_key, authorization)
        manager = _require_collection_manager(collection_manager)
        record = await _call_collection_transition(manager.abort_session(session_id))
        return {"ok": True, "session": record.to_json()}

    @router.post("/v1/gateway/collection/sessions/{session_id}/task-reward")
    async def submit_collection_task_reward(
        session_id: str,
        payload: dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        await ensure_gateway_auth(config.gateway_api_key, authorization)
        manager = _require_collection_manager(collection_manager)
        try:
            reward = TaskReward.from_payload(payload)
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            projected_samples = await manager.submit_task_reward(session_id, reward)
        except CollectionSessionError as exc:
            status_code = 404 if exc.code is CollectionSessionErrorCode.UNKNOWN_SESSION else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True, "projected_samples": projected_samples}

    return router
