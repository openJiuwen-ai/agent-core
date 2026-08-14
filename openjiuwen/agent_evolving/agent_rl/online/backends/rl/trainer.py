# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""PPO batch execution helpers for online training scheduler."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Optional

from ....storage.lora_repo import LoRAPublishRequest, LoRAVersion
from ...abstract.lora import LoRARepositoryProtocol
from ...core.training_process import release_accelerator_memory
from ...inference.notifier import InferenceNotifier
from ...scheduler.plugins import EvalRequest, call_evaler

logger = logging.getLogger("online_rl.scheduler")


class PPOTrainingExecutor:
    """Own PPO runner lifecycle and execute one training batch."""

    def __init__(
        self,
        *,
        base_model_path: str,
        lora_repo: Optional[LoRARepositoryProtocol],
        notifier: Optional[InferenceNotifier],
        nproc_per_node: int,
        training_gpu_ids: str,
        ppo_config_path: Optional[str],
        ppo_samples_per_step: int = 0,
        evaler: Any | None = None,
    ) -> None:
        self.base_model_path = base_model_path
        self.lora_repo = lora_repo
        self.notifier = notifier
        self.nproc_per_node = nproc_per_node
        self.training_gpu_ids = training_gpu_ids
        self.ppo_config_path = ppo_config_path
        self.ppo_samples_per_step = max(0, int(ppo_samples_per_step))
        self.evaler = evaler
        self._ppo_runner = None
        self._ppo_initialized = False
        self._ppo_config = None
        self._ppo_init_lora_adapter_path: str = ""

    async def aclose(self) -> None:
        if self.notifier is not None:
            try:
                await self.notifier.close()
            except Exception as exc:
                logger.debug("Failed to close inference notifier: %s", exc)
        self.close()

    def close(self) -> None:
        if self._ppo_runner is None:
            release_accelerator_memory()
            return
        try:
            import ray
            ray.kill(self._ppo_runner, no_restart=True)
        except Exception as exc:
            logger.debug("Failed to kill PPO runner (may already be dead): %s", exc)
        self._ppo_runner = None
        self._ppo_initialized = False
        self._ppo_init_lora_adapter_path = ""
        release_accelerator_memory()

    def request_stop(self) -> dict[str, object]:
        """Request stop for the current PPO actor and release accelerator memory."""

        had_runner = self._ppo_runner is not None
        self.close()
        return {"active": had_runner, "action": "ray.kill" if had_runner else "none", "name": "ppo"}

    def _resolve_init_lora_adapter_path(self, user_id: str) -> tuple[str, str, str, str]:
        if self.lora_repo is None:
            return "", "base_model", "", ""
        latest_available = self.lora_repo.get_latest_available(user_id)
        if latest_available is None:
            latest_any = self.lora_repo.get_latest(user_id)
            if latest_any is not None:
                logger.info(
                    "No available LoRA found for user=%s, falling back to base model; latest=%s status=%s",
                    user_id,
                    latest_any.version,
                    latest_any.availability_status,
                )
            return "", "base_model", "", ""
        logger.info(
            "Using available LoRA as training init user=%s version=%s status=%s path=%s",
            user_id,
            latest_available.version,
            latest_available.availability_status,
            latest_available.path,
        )
        return (
            latest_available.path,
            f"lora:{latest_available.version}",
            f"{user_id}:{latest_available.version}",
            latest_available.version,
        )

    async def train_batch(
        self,
        *,
        user_id: str,
        samples: list[dict[str, Any]],
        training_count: int,
        tmp_root: str,
    ) -> Optional[str]:
        run_dir = Path(tmp_root) / f"run_{training_count}_{uuid.uuid4().hex[:8]}"
        run_dir.mkdir(parents=True, exist_ok=True)
        try:
            (
                init_lora_adapter_path,
                training_source,
                parent_lora_id,
                parent_lora_version,
            ) = self._resolve_init_lora_adapter_path(user_id)
            published_lora = await asyncio.to_thread(
                self._run_ppo_training_sync,
                user_id=user_id,
                samples=samples,
                run_dir=run_dir,
                init_lora_adapter_path=init_lora_adapter_path,
                training_source=training_source,
                parent_lora_id=parent_lora_id,
                parent_lora_version=parent_lora_version,
            )
            if published_lora is not None:
                published_lora = await self._maybe_eval_lora(
                    user_id=user_id,
                    samples=samples,
                    training_count=training_count,
                    version=published_lora,
                )
            published_lora_path = published_lora.path if published_lora is not None else None
            if self.notifier and published_lora_path:
                try:
                    await self.notifier.notify_update(user_id, published_lora_path)
                except Exception:
                    logger.warning("Failed to notify vLLM for LoRA hot-load (non-fatal)")
            return published_lora_path
        finally:
            shutil.rmtree(str(run_dir / "fsdp_ckpt"), ignore_errors=True)

    def _init_ppo_trainer(self, *, init_lora_adapter_path: str = "") -> None:
        """Initialize Ray and the OnlineTaskRunner for PPO training."""
        if self._ppo_initialized and self._ppo_init_lora_adapter_path == init_lora_adapter_path:
            return
        if self._ppo_initialized:
            self.close()

        import ray

        from openjiuwen.agent_evolving.agent_rl.optimizer.task_runner import OnlineTaskRunner, get_ppo_ray_runtime_env

        from ...scheduler.ppo_config import compose_online_ppo_config

        if not ray.is_initialized():
            runtime_env = get_ppo_ray_runtime_env()
            if self.training_gpu_ids:
                visible_devices_env = os.getenv("ONLINE_RL_VISIBLE_DEVICES_ENV", "CUDA_VISIBLE_DEVICES")
                runtime_env.setdefault("env_vars", {})[visible_devices_env] = self.training_gpu_ids
            ray.init(runtime_env=runtime_env, namespace="OnlineRL")
            logger.info("Ray initialized for online PPO (%s=%s)", visible_devices_env, self.training_gpu_ids)

        config = compose_online_ppo_config(
            model_path=self.base_model_path,
            n_gpus_per_node=self.nproc_per_node,
            config_path=self.ppo_config_path,
            init_lora_adapter_path=init_lora_adapter_path or None,
        )
        self._ppo_config = config

        self._ppo_runner = OnlineTaskRunner.options(
            name="online_ppo_runner", lifetime="detached",
        ).remote()
        ray.get(self._ppo_runner.init_trainer.remote(config))
        self._ppo_initialized = True
        self._ppo_init_lora_adapter_path = init_lora_adapter_path
        logger.info("OnlineTaskRunner (PPO) initialized")

    def _run_ppo_training_sync(
        self,
        *,
        user_id: str,
        samples: list[dict[str, Any]],
        run_dir: Path,
        init_lora_adapter_path: str = "",
        training_source: str = "base_model",
        parent_lora_id: str = "",
        parent_lora_version: str = "",
    ) -> Optional[LoRAVersion]:
        """Convert samples to DataProto, run PPO train_step(s), export one LoRA."""
        import ray

        from ....rl_trainer.verl_converter import VerlDataProtoConverter

        if not samples:
            raise ValueError("PPO training requires at least one sample")
        self._init_ppo_trainer(init_lora_adapter_path=init_lora_adapter_path)

        pad_token_id = 0
        try:
            from transformers import AutoTokenizer
            tokenizer = AutoTokenizer.from_pretrained(self.base_model_path, trust_remote_code=True)
            pad_token_id = tokenizer.pad_token_id or 0
        except Exception:
            logger.debug("Could not load tokenizer for pad_token_id, using 0")

        ppo_config = self._ppo_config
        data_cfg = getattr(ppo_config, "data", None)
        max_prompt_length = (
            int(data_cfg.max_prompt_length)
            if data_cfg and data_cfg.get("max_prompt_length")
            else None
        )
        max_response_length = (
            int(data_cfg.max_response_length)
            if data_cfg and data_cfg.get("max_response_length")
            else None
        )
        truncation = str(data_cfg.get("truncation", "truncate")) if data_cfg else "truncate"
        filter_overlong_prompts = bool(data_cfg.get("filter_overlong_prompts", False)) if data_cfg else False

        raw_prompt_max = max((len((s.get("trajectory") or {}).get("prompt_ids") or []) for s in samples), default=0)
        raw_response_max = max((len((s.get("trajectory") or {}).get("response_ids") or []) for s in samples), default=0)
        logger.info(
            "Preparing DataProto: raw_prompt_max=%d raw_response_max=%d "
            "cfg_prompt_max=%s cfg_response_max=%s truncation=%s filter_overlong_prompts=%s",
            raw_prompt_max,
            raw_response_max,
            max_prompt_length,
            max_response_length,
            truncation,
            filter_overlong_prompts,
        )

        converter = VerlDataProtoConverter(
            pad_token_id=pad_token_id,
            max_prompt_length=max_prompt_length,
            max_response_length=max_response_length,
            truncation=truncation,
            filter_overlong_prompts=filter_overlong_prompts,
        )
        chunks = self._sample_chunks(samples)
        step_metrics: list[dict[str, float | int]] = []
        logger.info(
            "PPO run user=%s total_samples=%d ppo_steps=%d samples_per_step=%s",
            user_id,
            len(samples),
            len(chunks),
            self.ppo_samples_per_step or "all",
        )

        for step_index, chunk in enumerate(chunks, start=1):
            data_proto = converter.convert_samples(chunk)
            logger.info(
                "Converted PPO chunk %d/%d: samples=%d batch_size=%d prompt_width=%d "
                "response_width=%d dropped=%s prompt_truncated=%s response_truncated=%s",
                step_index,
                len(chunks),
                len(chunk),
                len(data_proto),
                int(data_proto.batch["prompts"].shape[-1]),
                int(data_proto.batch["responses"].shape[-1]),
                data_proto.meta_info.get("dropped_samples"),
                data_proto.meta_info.get("prompt_truncated_samples"),
                data_proto.meta_info.get("response_truncated_samples"),
            )

            try:
                metrics = ray.get(self._ppo_runner.train_on_batch.remote(data_proto))
            except Exception:
                logger.exception("PPO train_step %d/%d failed", step_index, len(chunks))
                raise
            numeric_metrics = {k: v for k, v in metrics.items() if isinstance(v, (int, float))}
            step_metrics.append(numeric_metrics)
            logger.info("PPO train_step %d/%d metrics: %s", step_index, len(chunks), numeric_metrics)

        peft_dir = ray.get(self._ppo_runner.export_lora.remote(
            str(run_dir), self.base_model_path,
        ))

        if self.lora_repo:
            scores = [s.get("judge", {}).get("score", 0.0) for s in samples]
            avg_score = sum(scores) / len(scores) if scores else 0.0
            version = self.lora_repo.publish(
                LoRAPublishRequest(
                    user_id=user_id,
                    lora_path=peft_dir,
                    metadata={
                        "sample_count": len(samples),
                        "avg_score": avg_score,
                        "training_mode": "ppo",
                        "ppo_steps": len(chunks),
                        "ppo_samples_per_step": self.ppo_samples_per_step or len(samples),
                        "ppo_metrics": step_metrics[-1] if step_metrics else {},
                        "ppo_step_metrics": step_metrics,
                        "training_source": training_source,
                    },
                    base_model=self.base_model_path,
                    parent_lora_id=parent_lora_id,
                    parent_lora_version=parent_lora_version,
                    parent_lora_path=init_lora_adapter_path,
                    availability_status="pending",
                    training_source=training_source,
                )
            )
            logger.info("Published PPO LoRA user=%s version=%s avg_score=%.3f", user_id, version.version, avg_score)
            return version
        return None

    def _sample_chunks(self, samples: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        if self.ppo_samples_per_step <= 0 or self.ppo_samples_per_step >= len(samples):
            return [samples]
        return [
            samples[offset: offset + self.ppo_samples_per_step]
            for offset in range(0, len(samples), self.ppo_samples_per_step)
        ]

    async def _maybe_eval_lora(
        self,
        *,
        user_id: str,
        samples: list[dict[str, Any]],
        training_count: int,
        version: LoRAVersion,
    ) -> LoRAVersion:
        if self.evaler is None or self.lora_repo is None:
            return version

        request = EvalRequest(
            user_id=user_id,
            lora_id=f"{user_id}:{version.version}",
            lora_version=version.version,
            lora_path=version.path,
            base_model_path=self.base_model_path,
            samples=samples,
            training_count=training_count,
            metadata={
                "trajectory_count": version.trajectory_count,
                "reward_avg": version.reward_avg,
            },
        )
        try:
            result = await call_evaler(self.evaler, request)
        except Exception as exc:
            logger.exception("Evaler failed for LoRA user=%s version=%s", user_id, version.version)
            return self.lora_repo.set_availability(
                user_id,
                version.version,
                available=False,
                reason=f"evaler exception: {exc}",
            )

        reason = result.reason or (
            f"score={result.score} target={result.target_score}"
            if result.score is not None or result.target_score is not None
            else "eval passed"
        )
        evaluated = self.lora_repo.set_availability(
            user_id,
            version.version,
            available=result.passed,
            reason=reason,
        )
        logger.info(
            "Evaler completed for LoRA user=%s version=%s passed=%s score=%s target=%s reason=%s metrics=%s",
            user_id,
            version.version,
            result.passed,
            result.score,
            result.target_score,
            result.reason,
            result.metrics,
        )
        return evaluated
