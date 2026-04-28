# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""PPO batch execution helpers for online training scheduler."""

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from pathlib import Path
from typing import Any, Optional

from ..inference.notifier import InferenceNotifier
from ...storage.lora_repo import LoRARepository

logger = logging.getLogger("online_rl.scheduler")


class PPOTrainingExecutor:
    """Own PPO runner lifecycle and execute one training batch."""

    def __init__(
        self,
        *,
        base_model_path: str,
        lora_repo: Optional[LoRARepository],
        notifier: Optional[InferenceNotifier],
        nproc_per_node: int,
        training_gpu_ids: str,
        ppo_config_path: Optional[str],
    ) -> None:
        self.base_model_path = base_model_path
        self.lora_repo = lora_repo
        self.notifier = notifier
        self.nproc_per_node = nproc_per_node
        self.training_gpu_ids = training_gpu_ids
        self.ppo_config_path = ppo_config_path
        self._ppo_runner = None
        self._ppo_initialized = False
        self._ppo_config = None

    async def aclose(self) -> None:
        if self.notifier is not None:
            try:
                await self.notifier.close()
            except Exception as exc:
                logger.debug("Failed to close inference notifier: %s", exc)
        self.close()

    def close(self) -> None:
        if self._ppo_runner is None:
            return
        try:
            import ray
            ray.kill(self._ppo_runner, no_restart=True)
        except Exception as exc:
            logger.debug("Failed to kill PPO runner (may already be dead): %s", exc)
        self._ppo_runner = None
        self._ppo_initialized = False

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
            published_lora_path = await asyncio.to_thread(
                self._run_ppo_training_sync,
                user_id=user_id,
                samples=samples,
                run_dir=run_dir,
            )
            if self.notifier and published_lora_path:
                try:
                    await self.notifier.notify_update(user_id, published_lora_path)
                except Exception:
                    logger.warning("Failed to notify vLLM for LoRA hot-load (non-fatal)")
            return published_lora_path
        finally:
            shutil.rmtree(str(run_dir / "fsdp_ckpt"), ignore_errors=True)

    def _init_ppo_trainer(self) -> None:
        """Initialize Ray and the OnlineTaskRunner for PPO training."""
        if self._ppo_initialized:
            return

        import ray
        from .ppo_config import compose_online_ppo_config
        from openjiuwen.agent_evolving.agent_rl.optimizer.task_runner import OnlineTaskRunner
        from openjiuwen.agent_evolving.agent_rl.optimizer.task_runner import get_ppo_ray_runtime_env

        if not ray.is_initialized():
            runtime_env = get_ppo_ray_runtime_env()
            if self.training_gpu_ids:
                runtime_env.setdefault("env_vars", {})["CUDA_VISIBLE_DEVICES"] = self.training_gpu_ids
            ray.init(runtime_env=runtime_env, namespace="OnlineRL")
            logger.info("Ray initialized for online PPO (GPUs: %s)", self.training_gpu_ids)

        config = compose_online_ppo_config(
            model_path=self.base_model_path,
            n_gpus_per_node=self.nproc_per_node,
            config_path=self.ppo_config_path,
        )
        self._ppo_config = config

        self._ppo_runner = OnlineTaskRunner.options(
            name="online_ppo_runner", lifetime="detached",
        ).remote()
        ray.get(self._ppo_runner.init_trainer.remote(config))
        self._ppo_initialized = True
        logger.info("OnlineTaskRunner (PPO) initialized")

    def _run_ppo_training_sync(
        self,
        *,
        user_id: str,
        samples: list[dict[str, Any]],
        run_dir: Path,
    ) -> Optional[str]:
        """Convert samples to DataProto, run PPO train_step, export LoRA."""
        import ray
        from ...rl_trainer.verl_converter import VerlDataProtoConverter

        self._init_ppo_trainer()

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
        data_proto = converter.convert_samples(samples)
        logger.info(
            "Converted %d samples to DataProto (batch_size=%d, prompt_width=%d, "
            "response_width=%d, dropped=%s, prompt_truncated=%s, response_truncated=%s)",
            len(samples),
            len(data_proto),
            int(data_proto.batch["prompts"].shape[-1]),
            int(data_proto.batch["responses"].shape[-1]),
            data_proto.meta_info.get("dropped_samples"),
            data_proto.meta_info.get("prompt_truncated_samples"),
            data_proto.meta_info.get("response_truncated_samples"),
        )

        metrics = ray.get(self._ppo_runner.train_on_batch.remote(data_proto))
        logger.info("PPO train_step metrics: %s", {
            k: v for k, v in metrics.items() if isinstance(v, (int, float))
        })

        peft_dir = ray.get(self._ppo_runner.export_lora.remote(
            str(run_dir), self.base_model_path,
        ))

        if self.lora_repo:
            scores = [s.get("judge", {}).get("score", 0.0) for s in samples]
            avg_score = sum(scores) / len(scores) if scores else 0.0
            version = self.lora_repo.publish(
                user_id=user_id,
                lora_path=peft_dir,
                metadata={
                    "sample_count": len(samples),
                    "avg_score": avg_score,
                    "training_mode": "ppo",
                    "ppo_metrics": {k: v for k, v in metrics.items() if isinstance(v, (int, float))},
                },
                base_model=self.base_model_path,
            )
            logger.info("Published PPO LoRA user=%s version=%s avg_score=%.3f", user_id, version.version, avg_score)
            return version.path
        return None
