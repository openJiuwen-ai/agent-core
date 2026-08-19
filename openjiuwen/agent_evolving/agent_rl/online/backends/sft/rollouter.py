# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Rollouter implementations for SFT post-training scenarios."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openjiuwen.agent_evolving.agent_rl.online.abstract.rollouter import TaskRolloutCommandSpec
from openjiuwen.agent_evolving.agent_rl.online.backends.rollouter.docker_runtime import (
    SFTJiuwenclawDockerRequest,
    build_jiuwenclaw_docker_command,
    default_jiuwenclaw_task_command,
    env_bool,
    run_docker_command_spec,
    sft_rollout_concurrency,
)
from openjiuwen.agent_evolving.agent_rl.online.backends.sft.sample_builder import (
    SFT_SAMPLE_PROTOCOL_VERSION,
    build_sft_sample,
    build_sft_samples_from_raw_steps,
    json_safe,
    normalize_assistant_message,
    normalize_messages,
    raw_user_id,
)
from openjiuwen.agent_evolving.agent_rl.online.backends.sft.supervisor_client import SupervisorClient

logger = logging.getLogger(__name__)
SUPERVISOR_ROLLOUT_RAW_PROTOCOL_VERSION = "sft-supervisor-rollout-raw-v1"


@dataclass
class SFTRolloutContext:
    """Runtime dependencies and defaults shared by SFT sample rollouters."""

    supervisor: Any = None
    default_user_id: str = ""
    target_model_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class SFTRollouter(ABC):
    """Base class for raw-trajectory to SFT-sample conversion."""

    scenario: str = ""

    @abstractmethod
    async def rollout(
        self,
        raw_trajectories: list[dict[str, Any]],
        context: SFTRolloutContext,
    ) -> list[dict[str, Any]]:
        """Generate normalized ``sft-sample-v1`` payloads."""


# Scenario 2-2 and 2-1 both consume raw trajectories, but they split them
# differently: 2-2 replays multi-turn LLM calls, 2-1 replays task containers
# and may fall back to uploaded raw samples or direct raw-step conversion.
class IdentityDistillRollouter(SFTRollouter):
    """Convert existing assistant outputs from raw trajectories into SFT samples."""

    scenario = "identity_distill"

    async def rollout(
        self,
        raw_trajectories: list[dict[str, Any]],
        context: SFTRolloutContext,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for raw in raw_trajectories:
            out.extend(_samples_from_raw_steps(raw, context=context, scenario=self.scenario))
        return out


class MultiTurnSupervisorRollouter(SFTRollouter):
    """Scenario 2-2: split a multi-turn session and ask the supervisor per LLM turn."""

    scenario = "multi_turn_supervisor"

    async def rollout(
        self,
        raw_trajectories: list[dict[str, Any]],
        context: SFTRolloutContext,
    ) -> list[dict[str, Any]]:
        if context.supervisor is None:
            raise ValueError("MultiTurnSupervisorRollouter requires supervisor client")
        out: list[dict[str, Any]] = []
        for raw in raw_trajectories:
            raw_id = str(raw.get("raw_id") or raw.get("trajectory_id") or "")
            for step in raw.get("steps") or []:
                if not isinstance(step, dict) or step.get("type") != "llm":
                    continue
                messages = normalize_messages(step.get("messages") or [])
                if not messages:
                    continue
                metadata = {
                    **context.metadata,
                    "raw_id": raw_id,
                    "step_index": step.get("step_index"),
                    "original_model_id": step.get("model_id") or raw.get("model_id"),
                }
                assistant_message = await context.supervisor.complete(
                    messages=messages,
                    tools=step.get("tools"),
                    metadata=metadata,
                )
                out.append(
                    build_sft_sample(
                        sample_id=f"{raw_id}:{step.get('step_index', len(out))}:supervisor",
                        user_id=_raw_user_id(raw, context),
                        session_id=str(raw.get("session_id") or ""),
                        source_raw_id=raw_id,
                        scenario=self.scenario,
                        model_id=context.target_model_id or str(raw.get("model_id") or ""),
                        messages=messages,
                        assistant_message=assistant_message,
                        tools=step.get("tools"),
                        metadata=metadata,
                    )
                )
        return out


class EndToEndImageRollouter(SFTRollouter):
    """Scenario 2-1: delegate a dataset-case session to a user-provided rollout image."""

    scenario = "end_to_end_image"

    async def rollout(
        self,
        raw_trajectories: list[dict[str, Any]],
        context: SFTRolloutContext,
    ) -> list[dict[str, Any]]:
        if context.supervisor is None:
            raise ValueError("EndToEndImageRollouter requires supervisor rollout endpoint")
        out: list[dict[str, Any]] = []
        for raw in raw_trajectories:
            response = await context.supervisor.rollout(raw_trajectory=raw, scenario=self.scenario)
            out.extend(_samples_from_rollout_response(response, raw=raw, context=context, scenario=self.scenario))
        return out


class DockerJiuwenSwarmRollouter(SFTRollouter):
    """Scenario 2-1: replay a recorded task in its dataset Docker image.

    ``SFT_DOCKER_ROLLOUT_COMMAND`` can provide the real jiuwenswarm command. The
    command receives ``SFT_TASK_PROMPT`` and should print either one
    ``sft-sample-v1`` object, ``{"samples": [...]}``, or ``{"trajectory": ...}``
    to stdout. Without a command, this rollouter only smoke-runs the container
    and falls back to the original raw trajectory's assistant outputs.

    The rollout container is CPU-only by default. vLLM and SFT training stay in
    the current backend environment; this container only supplies the SWE case
    workspace plus jiuwenswarm execution process.
    """

    scenario = "docker_jiuwenswarm"

    async def rollout(
        self,
        raw_trajectories: list[dict[str, Any]],
        context: SFTRolloutContext,
    ) -> list[dict[str, Any]]:
        concurrency = sft_rollout_concurrency()
        logger.info(
            "Starting Docker jiuwenclaw SFT rollout raw_count=%d concurrency=%d",
            len(raw_trajectories),
            concurrency,
        )
        semaphore = asyncio.Semaphore(concurrency)

        async def _run_one(raw: dict[str, Any]) -> list[dict[str, Any]]:
            async with semaphore:
                return await self._rollout_one_raw(raw, context)

        grouped = await asyncio.gather(*(_run_one(raw) for raw in raw_trajectories))
        samples = [sample for samples in grouped for sample in samples]
        logger.info(
            "Completed Docker jiuwenclaw SFT rollout raw_count=%d sample_count=%d",
            len(raw_trajectories),
            len(samples),
        )
        return samples

    async def _rollout_one_raw(self, raw: dict[str, Any], context: SFTRolloutContext) -> list[dict[str, Any]]:
        """Replay one raw task trajectory and normalize the supervisor output.

        This is intentionally the single per-raw workflow boundary for scenario
        2-1. The public rollout method only adds bounded concurrency; metadata,
        rollout-user isolation, uploaded-raw polling, and fallback conversion
        stay here so concurrent agent containers cannot mix state.
        """

        original_user_id = _raw_user_id(raw, context)
        rollout_user_id = _rollout_user_id(raw, original_user_id)
        raw_id = str(raw.get("raw_id") or raw.get("trajectory_id") or "")
        logger.info(
            "Starting SFT Docker replay raw_id=%s original_user=%s rollout_user=%s",
            raw_id,
            original_user_id,
            rollout_user_id,
        )
        result = await _run_docker_rollout(raw, rollout_user_id=rollout_user_id)
        samples = _samples_from_docker_stdout(result.get("stdout", ""), raw=raw, context=context)
        if not samples:
            samples = await _samples_from_uploaded_rollout_raw(
                rollout_user_id=rollout_user_id,
                original_user_id=original_user_id,
                source_raw=raw,
                context=context,
            )
        if not samples:
            samples = _samples_from_raw_steps(raw, context=context, scenario=self.scenario)
        for sample in samples:
            metadata = dict(sample.get("metadata") or {})
            metadata["docker_rollout"] = result
            sample["metadata"] = json_safe(metadata)
        logger.info(
            "Completed SFT Docker replay raw_id=%s rollout_user=%s exit=%s sample_count=%d skipped=%s",
            raw_id,
            rollout_user_id,
            result.get("exit_code"),
            len(samples),
            result.get("skipped", False),
        )
        return samples


class HintRewardpackRollouter(SFTRollouter):
    """Scenario 2-3 extension point; rewardpack/hint logic is intentionally deferred."""

    scenario = "hint_rewardpack"

    async def rollout(
        self,
        raw_trajectories: list[dict[str, Any]],
        context: SFTRolloutContext,
    ) -> list[dict[str, Any]]:
        del raw_trajectories, context
        raise NotImplementedError("hint_rewardpack rollout is reserved for a future rewardpack implementation")


def build_sft_rollouter(name: str) -> SFTRollouter:
    """Build a rollouter by config name."""
    normalized = (name or "multi_turn_supervisor").strip().lower()
    if normalized in {"identity", "identity_distill", "scene1", "scenario1"}:
        return IdentityDistillRollouter()
    if normalized in {"multi_turn_supervisor", "scenario2_2", "2-2"}:
        return MultiTurnSupervisorRollouter()
    if normalized in {"end_to_end_image", "e2e_image"}:
        return EndToEndImageRollouter()
    if normalized in {"docker_jiuwenswarm", "docker", "scenario2_1", "2-1", "scenario2_1_docker", "2-1-docker"}:
        return DockerJiuwenSwarmRollouter()
    if normalized in {"hint_rewardpack", "scenario2_3", "2-3"}:
        return HintRewardpackRollouter()
    raise ValueError(f"unsupported SFT rollouter: {name}")


# Helpers below are deliberately split by responsibility:
# raw user/session identity, raw-step normalization, Docker replay, and
# persistence/rehydration of supervisor-collected rollout raw data.
def _raw_user_id(raw: dict[str, Any], context: SFTRolloutContext) -> str:
    return raw_user_id(raw, default_user_id=context.default_user_id)


def _samples_from_raw_steps(
    raw: dict[str, Any],
    *,
    context: SFTRolloutContext,
    scenario: str,
) -> list[dict[str, Any]]:
    return build_sft_samples_from_raw_steps(
        raw,
        scenario=scenario,
        default_user_id=context.default_user_id,
        target_model_id=context.target_model_id,
        metadata=context.metadata,
    )


def _samples_from_rollout_response(
    response: dict[str, Any],
    *,
    raw: dict[str, Any],
    context: SFTRolloutContext,
    scenario: str,
) -> list[dict[str, Any]]:
    samples = response.get("samples")
    if isinstance(samples, list):
        out = []
        for idx, item in enumerate(samples):
            if not isinstance(item, dict):
                continue
            out.append(_normalize_external_sample(item, raw=raw, context=context, scenario=scenario, index=idx))
        return out

    if isinstance(response.get("messages"), list):
        return [
            build_sft_sample(
                user_id=_raw_user_id(raw, context),
                session_id=str(raw.get("session_id") or ""),
                source_raw_id=str(raw.get("raw_id") or raw.get("trajectory_id") or ""),
                scenario=scenario,
                model_id=context.target_model_id or str(raw.get("model_id") or ""),
                messages=normalize_messages(response.get("messages")),
                assistant_message=normalize_assistant_message(response.get("assistant_message") or response),
                tools=response.get("tools"),
                metadata={**context.metadata, "rollout_response": json_safe(response.get("metadata") or {})},
            )
        ]

    trajectory = response.get("trajectory")
    if isinstance(trajectory, dict):
        return _samples_from_raw_steps(trajectory, context=context, scenario=scenario)
    return _samples_from_raw_steps(raw, context=context, scenario=scenario)


def _normalize_external_sample(
    item: dict[str, Any],
    *,
    raw: dict[str, Any],
    context: SFTRolloutContext,
    scenario: str,
    index: int,
) -> dict[str, Any]:
    raw_id = str(raw.get("raw_id") or raw.get("trajectory_id") or "")
    item_metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    if item.get("protocol_version") == SFT_SAMPLE_PROTOCOL_VERSION:
        normalized = dict(json_safe(item))
        normalized.setdefault("user_id", _raw_user_id(raw, context))
        normalized.setdefault("session_id", str(raw.get("session_id") or ""))
        normalized.setdefault("source_raw_id", raw_id)
        normalized.setdefault("scenario", scenario)
        normalized.setdefault("sample_id", f"{raw_id}:external:{index}")
        return normalized
    return build_sft_sample(
        sample_id=str(item.get("sample_id") or f"{raw_id}:external:{index}"),
        user_id=str(item.get("user_id") or _raw_user_id(raw, context)),
        session_id=str(item.get("session_id") or raw.get("session_id") or ""),
        source_raw_id=str(item.get("source_raw_id") or raw_id),
        scenario=str(item.get("scenario") or scenario),
        model_id=str(item.get("model_id") or context.target_model_id or raw.get("model_id") or ""),
        messages=normalize_messages(item.get("messages") or []),
        assistant_message=normalize_assistant_message(item.get("assistant_message") or item.get("response") or item),
        tools=item.get("tools"),
        metadata={**context.metadata, **item_metadata},
    )


async def _run_docker_rollout(raw: dict[str, Any], *, rollout_user_id: str = "") -> dict[str, Any]:
    request = _docker_rollout_request_from_raw(raw, rollout_user_id=rollout_user_id)
    if request is None:
        return {"skipped": True, "reason": "missing docker image"}

    docker_cmd = build_jiuwenclaw_docker_command(request)
    logger.info("SFT docker rollout image=%s command=%s", request.image, shlex.join(docker_cmd[:8] + ["..."]))
    completed = await run_docker_command_spec(
        TaskRolloutCommandSpec(
            name=request.instance_id or str(raw.get("raw_id") or "case"),
            command=docker_cmd,
            timeout_seconds=int(os.getenv("SFT_DOCKER_ROLLOUT_TIMEOUT", "600")),
        )
    )
    if env_bool("SFT_DOCKER_ROLLOUT_DEBUG_LOG", False):
        logger.info(
            "SFT docker rollout completed image=%s exit=%s stdout_tail=%s stderr_tail=%s",
            request.image,
            completed.exit_code,
            completed.stdout_tail[-4000:],
            completed.stderr_tail[-4000:],
        )
    return {
        "image": request.image,
        "exit_code": completed.exit_code,
        "stdout": completed.stdout_tail,
        "stderr": completed.stderr_tail,
        "gpu_requested": False,
        "rollout_user_id": rollout_user_id,
        "host_conda_mounted": env_bool("SFT_DOCKER_USE_HOST_CONDA", True),
    }


def _docker_rollout_request_from_raw(
    raw: dict[str, Any],
    *,
    rollout_user_id: str = "",
) -> SFTJiuwenclawDockerRequest | None:
    """Translate one stored raw trajectory into a replayable Docker request."""

    dataset_case = raw.get("dataset_case") if isinstance(raw.get("dataset_case"), dict) else {}
    image = str(dataset_case.get("image") or dataset_case.get("docker_image") or raw.get("docker_image") or "").strip()
    task_prompt = str(raw.get("original_task") or dataset_case.get("task_prompt") or dataset_case.get("prompt") or "")
    instance_id = str(dataset_case.get("instance_id") or _instance_id_from_image(image) or "").strip()
    raw_id = str(raw.get("raw_id") or raw.get("trajectory_id") or "")
    if not image:
        logger.warning(
            "Skipping SFT Docker replay because docker image is missing raw_id=%s user=%s session=%s",
            raw_id,
            raw.get("user_id") or raw.get("tenant_id") or "",
            raw.get("session_id") or "",
        )
        return None

    command = os.getenv("SFT_DOCKER_ROLLOUT_COMMAND", "").strip()
    if not command:
        command = default_jiuwenclaw_task_command()

    effective_user_id = rollout_user_id or str(raw.get("user_id") or raw.get("tenant_id") or "")
    logger.info(
        "Prepared SFT Docker replay request raw_id=%s instance=%s image=%s "
        "original_user=%s rollout_user=%s task_chars=%d",
        raw_id,
        instance_id,
        image,
        raw.get("user_id") or raw.get("tenant_id") or "",
        effective_user_id,
        len(task_prompt),
    )
    return SFTJiuwenclawDockerRequest(
        image=image,
        task_prompt=task_prompt,
        instance_id=instance_id,
        dataset_case=dataset_case,
        gateway_url=os.getenv("TRAJECTORY_GATEWAY_URL", ""),
        supervisor_url=os.getenv("SUPERVISOR_URL", ""),
        supervisor_token=os.getenv("SUPERVISOR_TOKEN", "EMPTY"),
        supervisor_model=os.getenv("SUPERVISOR_MODEL", ""),
        tenant_id=effective_user_id,
        rollout_command=command,
        data_dir=f"/tmp/jiuwenswarm-rollout-{instance_id or 'case'}",
    )


async def _samples_from_uploaded_rollout_raw(
    *,
    rollout_user_id: str,
    original_user_id: str,
    source_raw: dict[str, Any],
    context: SFTRolloutContext,
) -> list[dict[str, Any]]:
    redis_url = os.getenv("REDIS_URL", "").strip()
    if not redis_url or not rollout_user_id:
        return []
    from redis.asyncio import from_url as redis_from_url

    from openjiuwen.agent_evolving.agent_rl.online.backends.sft.redis_store import RedisSFTStore

    redis = redis_from_url(redis_url, decode_responses=False)
    store = RedisSFTStore(redis)
    raw_items: list[dict[str, Any]] = []
    try:
        deadline = asyncio.get_running_loop().time() + float(os.getenv("SFT_DOCKER_ROLLOUT_FETCH_TIMEOUT", "30"))
        limit = int(os.getenv("SFT_DOCKER_ROLLOUT_FETCH_LIMIT", "8"))
        while asyncio.get_running_loop().time() < deadline:
            raw_items = await store.fetch_raw_and_mark_processing(rollout_user_id, limit)
            if raw_items:
                break
            await asyncio.sleep(1)
        if not raw_items:
            return []
        artifact_path: Path | None = None
        rollout_raw_items = raw_items
        if env_bool("SFT_PERSIST_SUPERVISOR_ROLLOUT_RAW", False):
            artifact_path = _persist_supervisor_rollout_raw(
                raw_items,
                rollout_user_id=rollout_user_id,
                original_user_id=original_user_id,
                source_raw=source_raw,
            )
            rollout_raw_items = _load_supervisor_rollout_raw(artifact_path)
        samples: list[dict[str, Any]] = []
        for item in rollout_raw_items:
            normalized = dict(item)
            normalized["user_id"] = original_user_id
            normalized["tenant_id"] = original_user_id
            samples.extend(_samples_from_raw_steps(normalized, context=context, scenario="docker_jiuwenswarm"))
        await store.mark_raw_processed(_ids(raw_items, "raw_id", "sample_id"))
        for sample in samples:
            metadata = dict(sample.get("metadata") or {})
            metadata["supervisor_rollout_user_id"] = rollout_user_id
            metadata["source_task_raw_id"] = source_raw.get("raw_id") or source_raw.get("trajectory_id")
            if artifact_path is not None:
                metadata["supervisor_rollout_raw_path"] = str(artifact_path)
            sample["metadata"] = json_safe(metadata)
        if artifact_path is not None:
            logger.info(
                "Loaded supervisor rollout raw artifact path=%s raw_count=%d sample_count=%d",
                artifact_path,
                len(rollout_raw_items),
                len(samples),
            )
        else:
            logger.info(
                "Converted supervisor rollout raw directly raw_count=%d sample_count=%d rollout_user=%s",
                len(rollout_raw_items),
                len(samples),
                rollout_user_id,
            )
        return samples
    finally:
        await redis.aclose()


def _persist_supervisor_rollout_raw(
    raw_items: list[dict[str, Any]],
    *,
    rollout_user_id: str,
    original_user_id: str,
    source_raw: dict[str, Any],
) -> Path:
    """Persist supervisor replay trajectories before converting them to SFT samples."""

    root = Path(os.getenv("SFT_ROLLOUT_ARTIFACT_DIR", "/tmp/agent_rl_online/sft_rollouts"))
    root.mkdir(parents=True, exist_ok=True)
    source_raw_id = str(source_raw.get("raw_id") or source_raw.get("trajectory_id") or "raw")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    filename = f"{timestamp}_{_safe_filename(original_user_id)}_{_safe_filename(source_raw_id)}.raw.json"
    path = root / filename
    payload = {
        "protocol_version": SUPERVISOR_ROLLOUT_RAW_PROTOCOL_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "rollout_user_id": rollout_user_id,
        "original_user_id": original_user_id,
        "source_raw_id": source_raw_id,
        "raw_trajectories": json_safe(raw_items),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(
        "Persisted supervisor rollout raw artifact path=%s raw_count=%d rollout_user=%s",
        path,
        len(raw_items),
        rollout_user_id,
    )
    return path


def _load_supervisor_rollout_raw(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("protocol_version") != SUPERVISOR_ROLLOUT_RAW_PROTOCOL_VERSION:
        raise ValueError(f"unsupported supervisor rollout raw artifact protocol: {payload.get('protocol_version')}")
    raw_items = payload.get("raw_trajectories")
    if not isinstance(raw_items, list):
        raise ValueError(f"supervisor rollout raw artifact missing raw_trajectories: {path}")
    return [item for item in raw_items if isinstance(item, dict)]


def _safe_filename(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return normalized.strip("._")[:120] or "unknown"


def _rollout_user_id(raw: dict[str, Any], original_user_id: str) -> str:
    raw_id = str(raw.get("raw_id") or raw.get("trajectory_id") or "raw").replace(":", "_")
    return f"{original_user_id or 'online'}:sft-rollout:{raw_id}"


def _ids(items: list[dict[str, Any]], *fields: str) -> list[str]:
    out: list[str] = []
    for item in items:
        for field_name in fields:
            value = item.get(field_name)
            if value:
                out.append(str(value))
                break
    return out


def _instance_id_from_image(image: str) -> str:
    name = image.rsplit("/", 1)[-1].split(":", 1)[0]
    if "_1776_" in name:
        return name.split("_1776_", 1)[1]
    return name


def _samples_from_docker_stdout(
    stdout: str,
    *,
    raw: dict[str, Any],
    context: SFTRolloutContext,
) -> list[dict[str, Any]]:
    text = stdout.strip()
    if not text:
        return []
    for candidate in (text, text.splitlines()[-1]):
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return _samples_from_rollout_response(payload, raw=raw, context=context, scenario="docker_jiuwenswarm")
        if isinstance(payload, list):
            return [
                _normalize_external_sample(item, raw=raw, context=context, scenario="docker_jiuwenswarm", index=idx)
                for idx, item in enumerate(payload)
                if isinstance(item, dict)
            ]
    return []
