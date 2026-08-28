# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Loopback HTTP assembly for the independent online-RL Service."""

from __future__ import annotations

import argparse
import os
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import Body, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from redis.asyncio import Redis

from openjiuwen.agent_evolving.agent_rl.online.capture_pipeline import CapturePipeline
from openjiuwen.agent_evolving.agent_rl.online.task_registry import (
    FinishReason,
    RewardMode,
    TaskConflictError,
    TaskNotFoundError,
    TaskRegistry,
    TaskSpec,
)
from openjiuwen.agent_evolving.agent_rl.online.training_runner import TrainingRunner
from openjiuwen.agent_evolving.agent_rl.storage.trajectory_store import TrajectorySampleStore
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ValidationError as JiuwenValidationError
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.logging.log_config import configure_log_config


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _task_error(exc: TaskConflictError, code: str) -> HTTPException:
    status = 404 if isinstance(exc, TaskNotFoundError) else 409
    return _error(status, code, str(exc))


def _record(value: Any) -> dict[str, Any]:
    if hasattr(value, "public_dict"):
        return value.public_dict()
    if hasattr(value, "to_dict"):
        return value.to_dict()
    raise build_error(StatusCode.AGENT_RL_RECORD_TYPE_ERROR, record_type=type(value).__name__)


def build_rl_service_app(  # pylint: disable=too-many-arguments,too-many-locals,too-many-statements
    *,
    model_id: str,
    redis: Redis,
    trajectory_store: TrajectorySampleStore,
    task_registry: TaskRegistry,
    capture_pipeline: CapturePipeline,
    training_runner: TrainingRunner,
    trajectory_api: Any,
    close_resources: Callable[[], Awaitable[None]] | None = None,
) -> FastAPI:
    """Bind the complete loopback RL Service transport surface."""

    if not model_id.strip():
        raise build_error(StatusCode.AGENT_RL_SERVICE_PARAM_ERROR, error_msg="model_id is required")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await task_registry.recover_active()
        await training_runner.recover()
        try:
            yield
        finally:
            active_run = await training_runner.get_active()
            if active_run is not None:
                await training_runner.stop(active_run.training_run_id)
            for task in await task_registry.active_tasks():
                try:
                    await capture_pipeline.finish(task.rl_task_id, FinishReason.SERVICE_STOPPED)
                except TaskConflictError:
                    await capture_pipeline.abort(task.rl_task_id, FinishReason.TIMEOUT)
            if close_resources is not None:
                await close_resources()

    app = FastAPI(title="OpenJiuwen RL Service", lifespan=lifespan)

    @app.exception_handler(HTTPException)
    async def http_error_handler(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "request_failed", "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content={"error": detail})

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "invalid_request", "message": str(exc)}},
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled RL Service error: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error", "message": "internal service error"}},
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        try:
            await redis.ping()
            await trajectory_store.stats()
            training_runner.check_health()
        except Exception as exc:
            raise _error(503, "service_not_ready", str(exc)) from exc
        return {"status": "ready", "model_id": model_id}

    @app.post("/v1/rl/tasks/start")
    async def start_task(
        payload: dict[str, Any] = Body(...),
        agent_session_id: str | None = Header(default=None, alias="X-Agent-Session-Id"),
        gateway_task_id: str | None = Header(default=None, alias="X-AIGW-RL-Task-Id"),
        gateway_policy_name: str | None = Header(default=None, alias="X-AIGW-RL-Policy-Name"),
        gateway_policy_model: str | None = Header(default=None, alias="X-AIGW-RL-Policy-Model"),
    ) -> JSONResponse:
        if not str(agent_session_id or "").strip():
            raise _error(400, "missing_session_id", "X-Agent-Session-Id is required")
        if set(payload) != {"reward_mode"}:
            raise _error(400, "invalid_task_request", "body must contain only reward_mode")
        try:
            reward_mode = RewardMode(payload["reward_mode"])
        except (KeyError, ValueError) as exc:
            raise _error(400, "invalid_reward_mode", "reward_mode must be terminal or delayed_feedback") from exc
        task_id = str(gateway_task_id or "").strip()
        policy_name = str(gateway_policy_name or "").strip()
        policy_model = str(gateway_policy_model or "").strip()
        if not task_id or not policy_name or not policy_model:
            raise _error(400, "invalid_gateway_task", "gateway Task ID, policy name and policy model are required")
        try:
            spec = TaskSpec(
                rl_task_id=task_id,
                agent_session_id=str(agent_session_id),
                model_id=model_id,
                policy_lora_name=policy_name,
                reward_mode=reward_mode,
                policy_model=policy_model,
            )
        except ValueError as exc:
            raise _error(400, "invalid_gateway_task", str(exc)) from exc
        try:
            result = await task_registry.start(spec)
        except TaskConflictError as exc:
            raise _error(409, "task_conflict", str(exc)) from exc
        return JSONResponse(status_code=201 if result.created else 200, content=result.task.to_dict())

    @app.get("/v1/rl/tasks/{rl_task_id}")
    async def get_task(rl_task_id: str) -> dict[str, Any]:
        task = await task_registry.get(rl_task_id)
        if task is None:
            raise _error(404, "task_not_found", "RL Task not found")
        return task.to_dict()

    @app.post("/v1/rl/tasks/{rl_task_id}/stop")
    async def stop_task(rl_task_id: str) -> dict[str, Any]:
        if await task_registry.get(rl_task_id) is None:
            raise _error(404, "task_not_found", "RL Task not found")
        try:
            return (await capture_pipeline.finish(rl_task_id, FinishReason.USER_STOPPED)).to_dict()
        except TaskConflictError as exc:
            raise _error(409, "task_conflict", str(exc)) from exc

    @app.post("/v1/rl/tasks/{rl_task_id}/reward")
    async def reward_task(rl_task_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, int]:
        if set(payload) != {"reward"}:
            raise _error(400, "invalid_reward_request", "body must contain only reward")
        if await task_registry.get(rl_task_id) is None:
            raise _error(404, "task_not_found", "RL Task not found")
        try:
            return {"sample_count": await capture_pipeline.submit_reward(rl_task_id, payload["reward"])}
        except ValueError as exc:
            raise _error(400, "invalid_reward", str(exc)) from exc
        except TaskConflictError as exc:
            raise _error(409, "reward_conflict", str(exc)) from exc

    @app.post("/v1/rl/training/runs")
    async def start_training_run(payload: dict[str, Any] | None = Body(default=None)) -> JSONResponse:
        if payload not in (None, {}):
            raise _error(400, "invalid_training_request", "Training Run body must be empty")
        try:
            result = await training_runner.start()
        except JiuwenValidationError as exc:
            raise _error(409, "insufficient_samples", str(exc)) from exc
        return JSONResponse(status_code=201 if result.created else 200, content=_record(result.run))

    @app.get("/v1/rl/training/runs/{training_run_id}")
    async def get_training_run(training_run_id: str) -> dict[str, Any]:
        run = await training_runner.get(training_run_id)
        if run is None:
            raise _error(404, "training_run_not_found", "Training Run not found")
        return _record(run)

    @app.post("/v1/rl/training/runs/{training_run_id}/stop")
    async def stop_training_run(training_run_id: str) -> dict[str, Any]:
        try:
            return _record(await training_runner.stop(training_run_id))
        except (JiuwenValidationError, KeyError) as exc:
            raise _error(404, "training_run_not_found", "Training Run not found") from exc

    @app.post("/internal/v1/completions:before")
    async def before_completion(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        try:
            request = await capture_pipeline.before(
                str(payload["rl_task_id"]),
                str(payload["capture_id"]),
                payload.get("agent_turn_id"),
                payload["request"],
            )
        except KeyError as exc:
            raise _error(400, "invalid_completion", f"missing field: {exc.args[0]}") from exc
        except ValueError as exc:
            raise _error(400, "invalid_completion", str(exc)) from exc
        except TaskConflictError as exc:
            raise _task_error(exc, "completion_conflict") from exc
        return {"request": request}

    @app.post("/internal/v1/completions:after", status_code=204)
    async def after_completion(payload: dict[str, Any] = Body(...)) -> Response:
        try:
            await capture_pipeline.after(
                str(payload["rl_task_id"]),
                str(payload["capture_id"]),
                payload.get("agent_turn_id"),
                payload["request"],
                payload["response"],
            )
        except KeyError as exc:
            raise _error(400, "invalid_completion", f"missing field: {exc.args[0]}") from exc
        except ValueError as exc:
            raise _error(400, "invalid_completion", str(exc)) from exc
        except TaskConflictError as exc:
            raise _task_error(exc, "completion_conflict") from exc
        return Response(status_code=204)

    @app.post("/internal/v1/completions:discard", status_code=204)
    async def discard_completion(payload: dict[str, Any] = Body(...)) -> Response:
        try:
            await capture_pipeline.discard(
                str(payload["rl_task_id"]),
                str(payload["capture_id"]),
                payload.get("agent_turn_id"),
            )
        except KeyError as exc:
            raise _error(400, "invalid_completion", f"missing field: {exc.args[0]}") from exc
        except TaskConflictError as exc:
            raise _task_error(exc, "completion_conflict") from exc
        return Response(status_code=204)

    @app.post("/internal/v1/rl/tasks/{rl_task_id}:finish")
    async def finish_task(rl_task_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        if set(payload) != {"reason"} or payload["reason"] != FinishReason.SERVICE_STOPPED.value:
            raise _error(400, "invalid_finish_reason", "reason must be service_stopped")
        try:
            return (await capture_pipeline.finish(rl_task_id, FinishReason.SERVICE_STOPPED)).to_dict()
        except TaskConflictError as exc:
            raise _task_error(exc, "task_conflict") from exc

    @app.post("/internal/v1/rl/tasks/{rl_task_id}:abort")
    async def abort_task(rl_task_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        try:
            reason = FinishReason(payload["reason"])
        except (KeyError, ValueError) as exc:
            raise _error(400, "invalid_abort_reason", "reason must be capture_failed or timeout") from exc
        if set(payload) != {"reason"} or reason not in {FinishReason.CAPTURE_FAILED, FinishReason.TIMEOUT}:
            raise _error(400, "invalid_abort_reason", "reason must be capture_failed or timeout")
        try:
            return (await capture_pipeline.abort(rl_task_id, reason)).to_dict()
        except TaskConflictError as exc:
            raise _task_error(exc, "task_conflict") from exc

    @app.post("/v1/gateway/upload/batch")
    async def upload_rail(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        try:
            result = await trajectory_api.rail_ingestor.ingest_rail_batch(payload)
        except ValueError as exc:
            raise _error(400, "invalid_rail_batch", str(exc)) from exc
        return {"ok": True, "result": result}

    @app.get("/v1/rl/trajectories/stats")
    async def trajectory_stats() -> dict[str, Any]:
        return await trajectory_api.trajectory_management_stats(model_id=model_id, user_id=model_id)

    @app.get("/v1/rl/trajectories")
    async def list_trajectories(
        status: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1),
    ) -> dict[str, Any]:
        return await trajectory_api.list_trajectories(
            model_id=model_id,
            status=status,
            user_id=model_id,
            limit=limit,
        )

    @app.get("/v1/rl/trajectories/{trajectory_id}")
    async def get_trajectory(trajectory_id: str) -> dict[str, Any]:
        trajectory = await trajectory_api.get_trajectory(trajectory_id)
        if trajectory is None:
            raise _error(404, "trajectory_not_found", "trajectory not found")
        return trajectory

    return app


def configure_rl_service_logging(*, path: str, max_bytes: int, backup_count: int, level: str) -> None:
    """Send RL business logs only to the Service's rotating log file."""

    log_path = Path(path)
    configure_log_config(
        {
            "backend": "default",
            "level": level,
            "output": ["file"],
            "log_path": str(log_path.parent),
            "log_file": log_path.name,
            "max_bytes": max_bytes,
            "backup_count": backup_count,
            "propagate": False,
        }
    )


def build_app_from_config(config: Any) -> FastAPI:  # pylint: disable=too-many-locals
    """Assemble production RL Service dependencies from static config."""

    import httpx
    from redis.asyncio import from_url as redis_from_url

    from openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.pending_judge_store import PendingJudgeStore
    from openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.persistence import GatewayTrajectoryRuntime
    from openjiuwen.agent_evolving.agent_rl.online.judge.judge_scorer import JudgeScorer
    from openjiuwen.agent_evolving.agent_rl.online.lora_client import AIGWLoRAClient
    from openjiuwen.agent_evolving.agent_rl.online.scheduler.ppo_executor import PPOTrainingExecutor
    from openjiuwen.agent_evolving.agent_rl.storage.lora_repo import LoRARepository
    from openjiuwen.agent_evolving.agent_rl.storage.redis_trajectory_store import RedisTrajectoryStore

    redis = redis_from_url(config.redis_url, decode_responses=False)
    trajectory_store = RedisTrajectoryStore(redis)
    registry = TaskRegistry(redis=redis)
    http_client = httpx.AsyncClient(timeout=config.judge_timeout)
    judge = None
    if config.judge_endpoint:
        judge = JudgeScorer(
            judge_url=config.judge_endpoint,
            judge_model=config.judge_model,
            api_key=config.judge_api_key or "EMPTY",
            timeout=config.judge_timeout,
            num_votes=config.judge_votes,
            max_retries=config.judge_retries,
            http_client=http_client,
        )
    pipeline = CapturePipeline(registry=registry, trajectory_store=trajectory_store, judge=judge)
    pending_judge_store = PendingJudgeStore(redis=redis, ttl_sec=config.trajectory_retention_seconds)
    trajectory_api = GatewayTrajectoryRuntime(
        SimpleNamespace(
            record_dir=config.record_dir,
            dump_token_ids=False,
            single_user_default=False,
            fixed_user_id=config.model_id,
            fixed_model_id=config.model_id,
        ),
        trajectory_store=trajectory_store,
        pending_judge_store=pending_judge_store,
    )
    trajectory_api.set_judge_scorer(judge)
    lora_repo = LoRARepository(config.lora_repository_path)
    ppo = PPOTrainingExecutor(
        base_model_path=config.base_model_path,
        lora_repo=lora_repo,
        nproc_per_node=config.nproc_per_node,
        training_gpu_ids=config.training_gpu_ids,
        ppo_config_path=config.ppo_config_path,
        ppo_samples_per_step=config.ppo_samples_per_step,
    )
    lora_client = AIGWLoRAClient(
        endpoint=config.aigw_endpoint,
        model_id=config.model_id,
        timeout=config.lora_activation_timeout,
        http_client=http_client,
    )
    runner = TrainingRunner(
        redis=redis,
        trajectory_store=trajectory_store,
        ppo=ppo,
        activator=lora_client,
        model_id=config.model_id,
        base_model_path=config.base_model_path,
        min_samples_for_training=config.min_samples_for_training,
        max_samples_per_run=config.max_samples_per_run,
        active_policy=lora_client.active_policy,
    )

    async def close_resources() -> None:
        await ppo.aclose()
        await http_client.aclose()
        await redis.aclose()

    return build_rl_service_app(
        model_id=config.model_id,
        redis=redis,
        trajectory_store=trajectory_store,
        task_registry=registry,
        capture_pipeline=pipeline,
        training_runner=runner,
        trajectory_api=trajectory_api,
        close_resources=close_resources,
    )


def create_app() -> FastAPI:
    """Uvicorn factory using the supervisor-provided static config path."""

    from openjiuwen.agent_evolving.agent_rl.config.service_config import RLServiceConfig

    config_path = os.environ.get("RL_SERVICE_CONFIG", "").strip()
    if not config_path:
        raise build_error(StatusCode.AGENT_RL_SERVICE_PARAM_ERROR, error_msg="RL_SERVICE_CONFIG is required")
    config = RLServiceConfig.from_yaml(config_path)
    configure_rl_service_logging(
        path=config.log_path,
        max_bytes=config.log_max_bytes,
        backup_count=config.log_backup_count,
        level=config.log_level,
    )
    return build_app_from_config(config)


def main() -> None:
    """Run the independent loopback RL Service."""

    import uvicorn

    from openjiuwen.agent_evolving.agent_rl.config.service_config import RLServiceConfig

    parser = argparse.ArgumentParser(description="OpenJiuwen online-RL Service")
    parser.add_argument("--config", default=os.environ.get("RL_SERVICE_CONFIG", ""))
    args = parser.parse_args()
    if not args.config:
        parser.error("--config or RL_SERVICE_CONFIG is required")
    config = RLServiceConfig.from_yaml(args.config)
    configure_rl_service_logging(
        path=config.log_path,
        max_bytes=config.log_max_bytes,
        backup_count=config.log_backup_count,
        level=config.log_level,
    )
    uvicorn.run(build_app_from_config(config), host=config.listen_host, port=config.listen_port, log_config=None)


if __name__ == "__main__":
    main()


__all__ = [
    "build_app_from_config",
    "build_rl_service_app",
    "configure_rl_service_logging",
    "create_app",
    "main",
]
