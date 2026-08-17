# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SFT training executor used by the online scheduler."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

import httpx

from openjiuwen.agent_evolving.agent_rl.online.abstract.lora import LoRARepositoryProtocol
from openjiuwen.agent_evolving.agent_rl.online.backends.sft.rollouter import (
    SFTRolloutContext,
    build_sft_rollouter,
)
from openjiuwen.agent_evolving.agent_rl.online.backends.sft.supervisor_client import SupervisorClient
from openjiuwen.agent_evolving.agent_rl.online.core.training_process import ManagedTrainingProcess
from openjiuwen.agent_evolving.agent_rl.online.inference.notifier import InferenceNotifier
from openjiuwen.agent_evolving.agent_rl.storage.lora_repo import LoRAPublishRequest

logger = logging.getLogger("online_rl.scheduler")


class SFTTrainingExecutor:
    """Own SFT sample generation, dataset building, trainer command, and LoRA publish."""

    def __init__(
        self,
        *,
        base_model_path: str,
        lora_repo: LoRARepositoryProtocol | None,
        notifier: InferenceNotifier | None,
        training_gpu_ids: str,
        rollouter_name: str = "multi_turn_supervisor",
        supervisor_url: str = "",
        supervisor_token: str = "",
        supervisor_model: str = "",
        target_model_id: str = "",
        trainer_command: str = "",
        dry_run: bool = False,
    ) -> None:
        self.base_model_path = base_model_path
        self.lora_repo = lora_repo
        self.notifier = notifier
        self.training_gpu_ids = training_gpu_ids
        self.rollouter = build_sft_rollouter(rollouter_name)
        self.target_model_id = target_model_id
        self.trainer_command = trainer_command
        self.dry_run = bool(dry_run)
        self._process_runner = ManagedTrainingProcess("sft")
        self._stop_requested = False
        self._supervisor = (
            SupervisorClient(supervisor_url, token=supervisor_token, model=supervisor_model)
            if supervisor_url
            else None
        )

    async def aclose(self) -> None:
        if self._supervisor is not None:
            await self._supervisor.aclose()
        if self.notifier is not None:
            try:
                await self.notifier.close()
            except (httpx.HTTPError, OSError, RuntimeError) as exc:
                logger.debug("Failed to close inference notifier: %s", exc)

    def close(self) -> None:
        """Stop any active trainer subprocess owned by this executor."""

        self._process_runner.force_kill()

    def request_stop(self) -> dict[str, object]:
        """Request graceful stop for the currently running SFT trainer process."""

        self._stop_requested = True
        return self._process_runner.request_stop()

    async def build_samples_from_raw(
        self,
        *,
        user_id: str,
        raw_trajectories: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Run the configured rollouter and return normalized SFT samples."""
        context = SFTRolloutContext(
            supervisor=self._supervisor,
            default_user_id=user_id,
            target_model_id=self.target_model_id,
            metadata={
                "base_model_path": self.base_model_path,
                "rollouter": self.rollouter.scenario,
            },
        )
        return await self.rollouter.rollout(raw_trajectories, context)

    async def train_batch(
        self,
        *,
        user_id: str,
        samples: list[dict[str, Any]],
        training_count: int,
        tmp_root: str,
    ) -> str | None:
        """Write one SFT dataset and run the configured trainer command."""
        if not samples:
            raise ValueError("SFT training requires at least one sample")
        self._stop_requested = False
        run_dir = Path(tmp_root) / f"sft_run_{training_count}_{uuid.uuid4().hex[:8]}"
        run_dir.mkdir(parents=True, exist_ok=True)
        output_dir = run_dir / "lora"
        logger.info(
            "SFT run user=%s samples=%d run_dir=%s rollouter=%s trainer=%s",
            user_id,
            len(samples),
            run_dir,
            self.rollouter.scenario,
            "custom-command" if self.trainer_command else "none",
        )

        dataset_path = run_dir / "train.json"
        if self.dry_run:
            self._write_dataset(dataset_path, samples)
            return str(dataset_path)

        if not self.trainer_command:
            raise ValueError("SFT trainer_command is required when dry_run is disabled")

        try:
            self._write_dataset(dataset_path, samples)
            await asyncio.to_thread(
                self._run_trainer_command,
                user_id=user_id,
                dataset_path=dataset_path,
                output_dir=output_dir,
                run_dir=run_dir,
            )
        except subprocess.CalledProcessError as exc:
            publish_dir = self._resolve_publish_dir(output_dir)
            if self._stop_requested and self._is_publishable_lora_dir(publish_dir):
                logger.warning(
                    "SFT trainer stopped after request; publishing partial LoRA from %s",
                    publish_dir,
                )
            else:
                raise RuntimeError("SFT trainer stopped before producing a publishable LoRA") from exc

        publish_dir = self._resolve_publish_dir(output_dir)
        if not publish_dir.exists():
            raise FileNotFoundError(f"SFT trainer did not create output_dir: {output_dir}")

        published_path = self._publish_lora(user_id=user_id, samples=samples, output_dir=publish_dir)
        if self.notifier and published_path:
            try:
                await self.notifier.notify_update(user_id, published_path)
            except (httpx.HTTPError, OSError, RuntimeError):
                logger.warning("Failed to notify vLLM for SFT LoRA hot-load (non-fatal)")
        shutil.rmtree(str(run_dir / "checkpoint_tmp"), ignore_errors=True)
        return published_path

    @staticmethod
    def _write_dataset(dataset_path: Path, samples: list[dict[str, Any]]) -> None:
        normalized: list[dict[str, Any]] = []
        for sample in samples:
            messages = []
            for message in list(sample.get("messages") or []):
                item = dict(message)
                item["loss_mask"] = 0
                messages.append(item)
            assistant_message = dict(sample.get("assistant_message") or {})
            if assistant_message:
                assistant_message.setdefault("role", "assistant")
                assistant_message["loss_mask"] = 1
                messages.append(assistant_message)
            normalized.append(
                {
                    "sample_id": sample.get("sample_id"),
                    "session_id": sample.get("session_id"),
                    "messages": messages,
                    "tools": sample.get("tools"),
                    "metadata": {
                        "sample_id": sample.get("sample_id"),
                        "source_raw_id": sample.get("source_raw_id"),
                        "scenario": sample.get("scenario"),
                        **dict(sample.get("metadata") or {}),
                    },
                }
            )
        dataset_path.write_text(json.dumps({"samples": normalized}, ensure_ascii=False, indent=2), encoding="utf-8")

    def _run_trainer_command(
        self,
        *,
        user_id: str,
        dataset_path: Path,
        output_dir: Path,
        run_dir: Path,
    ) -> None:
        command_text = self.trainer_command.format(
            base_model_path=self.base_model_path,
            dataset_path=str(dataset_path),
            output_dir=str(output_dir),
            run_dir=str(run_dir),
            user_id=user_id,
            training_gpu_ids=self.training_gpu_ids,
        )
        env = os.environ.copy()
        if self.training_gpu_ids:
            visible_devices_env = os.getenv("ONLINE_RL_VISIBLE_DEVICES_ENV", "CUDA_VISIBLE_DEVICES").strip()
            env[visible_devices_env or "CUDA_VISIBLE_DEVICES"] = self.training_gpu_ids
        self._process_runner.run(command_text, cwd=run_dir, env=env, shell=True)

    @staticmethod
    def _resolve_publish_dir(output_dir: Path) -> Path:
        if (output_dir / "adapter_config.json").exists():
            return output_dir
        if (output_dir / "adapter" / "adapter_config.json").exists():
            return output_dir / "adapter"
        adapters = sorted(
            output_dir.glob("run_*/adapter"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for adapter in adapters:
            if (adapter / "adapter_config.json").exists():
                return adapter
        checkpoints = sorted(
            output_dir.glob("run_*/checkpoints/global_step_*"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for checkpoint in checkpoints:
            if any(checkpoint.iterdir()):
                return checkpoint
        adapter_configs = sorted(
            output_dir.rglob("adapter_config.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if adapter_configs:
            return adapter_configs[0].parent
        return output_dir

    @staticmethod
    def _is_publishable_lora_dir(path: Path) -> bool:
        return (
            path.exists()
            and (path / "adapter_config.json").exists()
            and (
                (path / "adapter_model.safetensors").exists()
                or (path / "adapter_model.bin").exists()
                or any(path.glob("*.safetensors"))
            )
        )

    def _publish_lora(self, *, user_id: str, samples: list[dict[str, Any]], output_dir: Path) -> str | None:
        if self.lora_repo is None:
            return str(output_dir)
        version = self.lora_repo.publish(
            LoRAPublishRequest(
                user_id=user_id,
                lora_path=str(output_dir),
                base_model=self.base_model_path,
                metadata={
                    "training_backend": "SFT",
                    "training_source": f"sft:{self.rollouter.scenario}",
                    "trajectory_count": len(samples),
                    "sample_count": len(samples),
                    "stopped_by_request": self._stop_requested,
                },
            )
        )
        logger.info("Published SFT LoRA user=%s version=%s path=%s", user_id, version.version, version.path)
        return version.path
