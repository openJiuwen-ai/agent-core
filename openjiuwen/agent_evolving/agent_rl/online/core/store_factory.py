# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Factories for online RL storage backends."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...storage.local_store import LocalPendingJudgeStore, LocalSFTStore, LocalTrainingTaskStore, LocalTrajectoryStore
from ...storage.training_task_store import TrainingTaskStore
from ..backends.rl.redis_store import RedisTrajectoryStore
from ..backends.sft.redis_store import RedisSFTStore


@dataclass(frozen=True)
class OnlineStoreBundle:
    """Stores shared by gateway and scheduler."""

    backend: str
    trajectory_store: Any
    training_task_store: Any
    sft_store: Any | None = None
    pending_judge_store: Any | None = None
    redis_client: Any | None = None
    owns_redis_client: bool = False


def normalize_store_backend(backend: str | None, *, redis_url: str | None = None) -> str:
    """Resolve backend name, keeping Redis as the default when configured."""
    normalized = str(backend or "").strip().lower()
    if normalized in {"", "auto"}:
        return "redis" if str(redis_url or "").strip() else "local"
    if normalized not in {"redis", "local"}:
        raise ValueError("trajectory store backend must be one of: auto, redis, local")
    return normalized


def resolve_local_store_dir(local_store_dir: str | None, *, record_dir: str = "records") -> str:
    """Return the directory used by the file-backed local store."""
    configured = str(local_store_dir or "").strip()
    if configured:
        return configured
    return str(Path(record_dir or "records") / "local_store")


def build_gateway_store_bundle(
    *,
    backend: str | None,
    redis_url: str | None,
    local_store_dir: str | None,
    record_dir: str,
    redis_client: Any = None,
) -> OnlineStoreBundle:
    """Build gateway stores, including delayed judge state."""
    resolved_backend = normalize_store_backend(
        "redis" if str(backend or "").strip().lower() in {"", "auto"} and redis_client is not None else backend,
        redis_url=redis_url,
    )
    if resolved_backend == "local":
        root_dir = resolve_local_store_dir(local_store_dir, record_dir=record_dir)
        return OnlineStoreBundle(
            backend="local",
            trajectory_store=LocalTrajectoryStore(root_dir),
            training_task_store=LocalTrainingTaskStore(root_dir),
            sft_store=LocalSFTStore(root_dir),
            pending_judge_store=LocalPendingJudgeStore(root_dir),
        )

    client = redis_client
    owns_client = client is None
    if client is None:
        if not redis_url:
            raise ValueError("redis trajectory store requires redis_url")
        from redis.asyncio import from_url as redis_from_url

        client = redis_from_url(redis_url, decode_responses=False)
    from openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.pending_judge_store import (
        PendingJudgeStore,
    )

    return OnlineStoreBundle(
        backend="redis",
        trajectory_store=RedisTrajectoryStore(client),
        training_task_store=TrainingTaskStore(client),
        sft_store=RedisSFTStore(client),
        pending_judge_store=PendingJudgeStore(redis=client),
        redis_client=client,
        owns_redis_client=owns_client,
    )


def build_scheduler_store_bundle(
    *,
    backend: str | None,
    redis_url: str | None,
    local_store_dir: str | None,
    record_dir: str = "records",
) -> OnlineStoreBundle:
    """Build scheduler stores for consuming pending samples."""
    resolved_backend = normalize_store_backend(backend, redis_url=redis_url)
    if resolved_backend == "local":
        root_dir = resolve_local_store_dir(local_store_dir, record_dir=record_dir)
        return OnlineStoreBundle(
            backend="local",
            trajectory_store=LocalTrajectoryStore(root_dir),
            training_task_store=LocalTrainingTaskStore(root_dir),
            sft_store=LocalSFTStore(root_dir),
        )

    if not redis_url:
        raise ValueError("redis trajectory store requires redis_url")
    from redis.asyncio import from_url as redis_from_url

    client = redis_from_url(redis_url, decode_responses=False)
    return OnlineStoreBundle(
        backend="redis",
        trajectory_store=RedisTrajectoryStore(client),
        training_task_store=TrainingTaskStore(client),
        sft_store=RedisSFTStore(client),
        redis_client=client,
        owns_redis_client=True,
    )


def backend_from_env() -> str:
    return os.environ.get("TRAJECTORY_STORE_BACKEND", "")


def local_store_dir_from_env() -> str:
    return os.environ.get("LOCAL_TRAJECTORY_STORE_DIR", "")
