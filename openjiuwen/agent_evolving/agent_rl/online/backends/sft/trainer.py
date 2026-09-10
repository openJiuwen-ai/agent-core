# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SFT training executor used by the online scheduler.

Operational defaults target the veRL 0.8.0 SFT trainer. ``SFT_VERL_SAVE_FREQ``
accepts either an integer step interval or ``after_each_epoch``; use an integer
when running older local veRL forks that do not support epoch literals.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx
import yaml

from openjiuwen.agent_evolving.agent_rl.online.abstract.lora import LoRARepositoryProtocol
from openjiuwen.agent_evolving.agent_rl.online.backends.sft.rollouter import (
    SFTRolloutContext,
    build_sft_rollouter,
)
from openjiuwen.agent_evolving.agent_rl.online.backends.sft.sft_data_formatter import write_sft_parquet
from openjiuwen.agent_evolving.agent_rl.online.backends.sft.supervisor_client import SupervisorClient
from openjiuwen.agent_evolving.agent_rl.online.core.training_process import ManagedTrainingProcess
from openjiuwen.agent_evolving.agent_rl.storage.lora_repo import LoRAPublishRequest

logger = logging.getLogger("online_rl.scheduler")


class SFTTrainingExecutor:
    """Own SFT sample generation, veRL training, and LoRA publish."""

    def __init__(
        self,
        *,
        base_model_path: str,
        lora_repo: LoRARepositoryProtocol | None,
        notifier: Any | None,
        training_gpu_ids: str,
        rollouter_name: str = "multi_turn_supervisor",
        supervisor_url: str = "",
        supervisor_token: str = "",
        supervisor_model: str = "",
        target_model_id: str = "",
        dry_run: bool = False,
    ) -> None:
        self.base_model_path = base_model_path
        self.lora_repo = lora_repo
        self.notifier = notifier
        self.training_gpu_ids = training_gpu_ids
        self.rollouter = build_sft_rollouter(rollouter_name)
        self.target_model_id = target_model_id
        self.dry_run = bool(dry_run)
        self._process_runner = ManagedTrainingProcess("sft")
        self._stop_requested = False
        self._supervisor = (
            SupervisorClient(
                supervisor_url,
                token=supervisor_token,
                model=supervisor_model,
                timeout=self._env_float("SFT_SUPERVISOR_TIMEOUT", 120.0),
            )
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
        """Write one v1-compatible parquet dataset and run veRL SFT."""
        if not samples:
            raise ValueError("SFT training requires at least one sample")
        self._stop_requested = False
        run_name = f"sft_run_{training_count}_{uuid.uuid4().hex[:8]}"
        run_dir = self._sft_run_root(tmp_root) / run_name
        run_dir.mkdir(parents=True, exist_ok=True)
        output_dir = run_dir / "lora"
        logger.info(
            "SFT run user=%s samples=%d run_dir=%s output_dir=%s rollouter=%s trainer=verl_sft",
            user_id,
            len(samples),
            run_dir,
            output_dir,
            self.rollouter.scenario,
        )

        dataset_path = run_dir / "train.parquet"
        if self.dry_run:
            self._write_sft_artifacts(
                user_id=user_id,
                samples=samples,
                run_dir=run_dir,
                output_dir=output_dir,
                training_count=training_count,
            )
            return str(dataset_path)

        try:
            _, config_path = self._write_sft_artifacts(
                user_id=user_id,
                samples=samples,
                run_dir=run_dir,
                output_dir=output_dir,
                training_count=training_count,
            )
            await asyncio.to_thread(
                self._run_sft_trainer,
                config_path=config_path,
                run_dir=run_dir,
            )
        except subprocess.CalledProcessError as exc:
            if not self._stop_requested:
                # Ascend NPU only: verl's sft_trainer completes training and saves a full
                # valid checkpoint, then crashes during teardown / distributed cleanup with
                # a glibc heap corruption ("corrupted size vs. prev_size", SIGABRT -6) on
                # torch_npu + CANN. This is an environment-level bug, NOT a training
                # failure. On Ascend, if a valid checkpoint exists, downgrade to warning and
                # continue to export the LoRA (mirrors the verified handling in old/).
                # On GPU/CUDA this teardown bug does not occur, so any trainer failure
                # there is a real failure and must be raised as before.
                if self._is_ascend_env() and self._latest_sft_checkpoint_dir(output_dir) is not None:
                    checkpoint_dir = self._latest_sft_checkpoint_dir(output_dir)
                    logger.warning(
                        "SFT trainer exited with code %s after successful training; "
                        "continuing with latest checkpoint %s (known Ascend NPU teardown bug)",
                        exc.returncode,
                        checkpoint_dir,
                    )
                else:
                    raise RuntimeError("SFT trainer stopped before producing a publishable LoRA") from exc
            else:
                logger.warning("SFT trainer stopped after request; trying to export the latest checkpoint")

        publish_dir = self._export_sft_lora_adapter(output_dir=output_dir, run_dir=run_dir)

        published_path = self._publish_lora(user_id=user_id, samples=samples, output_dir=publish_dir)
        if self.notifier and published_path:
            try:
                await self.notifier.notify_update(user_id, published_path)
            except (httpx.HTTPError, OSError, RuntimeError):
                logger.warning("Failed to notify vLLM for SFT LoRA hot-load (non-fatal)")
        shutil.rmtree(str(run_dir / "checkpoint_tmp"), ignore_errors=True)
        return published_path

    @staticmethod
    def _verl_config_group_exists(*parts: str) -> bool:
        spec = importlib.util.find_spec("verl")
        if spec is None or not spec.submodule_search_locations or not parts:
            return False
        for location in spec.submodule_search_locations:
            candidate = Path(location) / "trainer" / "config" / Path(*parts[:-1]) / f"{parts[-1]}.yaml"
            if candidate.exists():
                return True
        return False

    def _write_sft_artifacts(
        self,
        *,
        user_id: str,
        samples: list[dict[str, Any]],
        run_dir: Path,
        output_dir: Path,
        training_count: int,
    ) -> tuple[Path, Path]:
        dataset_path = run_dir / "train.parquet"
        stats = write_sft_parquet(
            samples=samples,
            output_path=dataset_path,
            model_path=self.base_model_path,
            loss_norm=os.getenv("SFT_VERL_LOSS_NORM", "sqrt"),
            supervise=os.getenv("SFT_VERL_SUPERVISE", "last"),
            max_samples=self._env_int("SFT_VERL_MAX_SAMPLES", -1),
            no_filter=self._env_bool("SFT_VERL_NO_FILTER", False),
        )
        if stats.rows <= 0:
            raise ValueError(
                "SFT dataset has no trainable rows "
                f"(filtered_multimodal={stats.filtered_multimodal}, "
                f"filtered_no_assistant={stats.filtered_no_assistant}, skipped={stats.skipped})"
            )

        config = self._build_sft_config(
            user_id=user_id,
            dataset_path=dataset_path,
            output_dir=output_dir,
            custom_cls_path=self._write_sft_custom_cls(run_dir),
            training_count=training_count,
        )
        config_path = run_dir / "train_verl_sft.yaml"
        config_path.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")
        (run_dir / "dataset_stats.json").write_text(json.dumps(stats.__dict__, indent=2), encoding="utf-8")
        return dataset_path, config_path

    def _build_sft_config(
        self,
        *,
        user_id: str,
        dataset_path: Path,
        output_dir: Path,
        custom_cls_path: Path,
        training_count: int,
    ) -> dict[str, Any]:
        gpu_count = self._gpu_count()
        max_length = self._env_int("SFT_VERL_MAX_LENGTH", 49152)
        sequence_parallel_size = self._env_int("SFT_VERL_SEQUENCE_PARALLEL_SIZE", gpu_count)
        max_token_len_per_gpu = self._env_int(
            "SFT_VERL_MAX_TOKEN_LEN_PER_GPU",
            max(1, max_length // max(1, sequence_parallel_size)),
        )
        lora_rank = self._env_int("SFT_VERL_LORA_RANK", 64)
        lora_alpha = self._env_int("SFT_VERL_LORA_ALPHA", lora_rank)
        train_batch_size = max(
            gpu_count,
            self._env_int("SFT_VERL_TRAIN_BATCH_SIZE", max(2, gpu_count)),
        )
        experiment_name = f"sft-{self._slug(user_id)}-{training_count}"
        defaults: list[Any] = [
            {"model@model": "hf_model"},
            {"engine@engine": "fsdp"},
            {"optim@optim": "fsdp"},
        ]
        if self._verl_config_group_exists("profiler", "profiler"):
            defaults.append({"profiler@profiler": "profiler"})
        defaults.append("_self_")

        return {
            "hydra": {"searchpath": ["pkg://verl.trainer.config"]},
            "defaults": defaults,
            "model": {
                "_target_": "verl.workers.config.HFModelConfig",
                "path": self.base_model_path,
                "tokenizer_path": self.base_model_path,
                "trust_remote_code": True,
                "enable_gradient_checkpointing": True,
                "use_remove_padding": True,
                "use_liger": self._env_bool("SFT_VERL_USE_LIGER", True),
                "override_config": {
                    "attn_implementation": os.getenv("SFT_VERL_ATTN_IMPLEMENTATION", "flash_attention_2"),
                },
                "lora_rank": lora_rank,
                "lora_alpha": lora_alpha,
                "target_modules": os.getenv("SFT_VERL_TARGET_MODULES", "all-linear"),
            },
            "engine": {
                "_target_": "verl.workers.config.FSDPEngineConfig",
                "strategy": "fsdp",
                "seed": self._env_int("SFT_VERL_SEED", 42),
                "dtype": os.getenv("SFT_VERL_DTYPE", "bfloat16"),
                "use_torch_compile": self._env_bool("SFT_VERL_USE_TORCH_COMPILE", False),
                "reshard_after_forward": True,
                "param_offload": self._env_bool("SFT_VERL_PARAM_OFFLOAD", False),
                "optimizer_offload": self._env_bool("SFT_VERL_OPTIMIZER_OFFLOAD", False),
                "offload_policy": False,
                "fsdp_size": -1,
                "ulysses_sequence_parallel_size": sequence_parallel_size,
            },
            "optim": {
                "_target_": "verl.workers.config.FSDPOptimizerConfig",
                "optimizer": "AdamW",
                "optimizer_impl": "torch.optim",
                "lr": float(os.getenv("SFT_VERL_LR", "5.0e-5")),
                "weight_decay": float(os.getenv("SFT_VERL_WEIGHT_DECAY", "0.01")),
                "clip_grad": float(os.getenv("SFT_VERL_CLIP_GRAD", "1.0")),
                "lr_scheduler_type": os.getenv("SFT_VERL_LR_SCHEDULER", "cosine"),
                "lr_warmup_steps_ratio": float(os.getenv("SFT_VERL_WARMUP_RATIO", "0.1")),
                "min_lr_ratio": 0.0,
                "num_cycles": 0.5,
            },
            "data": {
                "train_batch_size": train_batch_size,
                "micro_batch_size_per_gpu": self._env_int("SFT_VERL_MICRO_BATCH_SIZE_PER_GPU", 1),
                "max_token_len_per_gpu": max_token_len_per_gpu,
                "use_dynamic_bsz": True,
                "train_files": str(dataset_path),
                "val_files": None,
                "train_max_samples": -1,
                "val_max_samples": -1,
                "messages_key": "messages",
                "loss_mask_key": "loss_mask",
                "turn_offsets_key": "turn_offsets",
                "pad_mode": "no_padding",
                "max_length": max_length,
                "truncation": os.getenv("SFT_VERL_TRUNCATION", "left"),
                "use_shm": False,
                "apply_chat_template_kwargs": {},
                "num_workers": self._env_int("SFT_VERL_NUM_WORKERS", 4),
                "window_length": self._env_int("SFT_VERL_WINDOW_LENGTH", 0),
                "window_overlap_turns": self._env_int("SFT_VERL_WINDOW_OVERLAP_TURNS", 2),
                "custom_cls": {
                    "path": str(custom_cls_path),
                    "name": "QwenMultiTurnSFTDataset",
                },
            },
            "checkpoint": {
                "_target_": "verl.trainer.config.CheckpointConfig",
                "save_contents": ["model", "optimizer", "extra"],
                "load_contents": ["model", "optimizer", "extra"],
                "mbridge_config": {},
            },
            "trainer": {
                "default_local_dir": str(output_dir),
                "default_hdfs_dir": None,
                "project_name": os.getenv("SFT_VERL_PROJECT_NAME", "agentos-sft-verl-new"),
                "experiment_name": experiment_name,
                "total_epochs": self._env_int("SFT_VERL_TOTAL_EPOCHS", 1),
                "total_training_steps": None,
                "logger": ["console"],
                "seed": self._env_int("SFT_VERL_SEED", 42),
                "save_freq": self._env_int_or_literal(
                    "SFT_VERL_SAVE_FREQ",
                    "after_each_epoch",
                    {"after_each_epoch"},
                ),
                "test_freq": -1,
                "max_ckpt_to_keep": self._env_int("SFT_VERL_MAX_CKPT_TO_KEEP", 5),
                "balance_batch": True,
                "resume_mode": os.getenv("SFT_VERL_RESUME_MODE", "disable"),
                "resume_from_path": None,
                "device": os.getenv("SFT_VERL_DEVICE", "cuda"),
                "nnodes": self._env_int("SFT_VERL_NNODES", 1),
                "n_gpus_per_node": gpu_count,
                "profile_interval": [-1, -1],
            },
            "profiler": {
                "_target_": "verl.utils.profiler.ProfilerConfig",
                "enable": False,
            },
        }

    def _run_sft_trainer(self, *, config_path: Path, run_dir: Path) -> None:
        python_bin = os.getenv("SFT_VERL_PYTHON") or os.getenv("VERL_PYTHON") or sys.executable
        entry = os.getenv("SFT_VERL_ENTRY", "verl.trainer.sft_trainer")
        command_parts = [
            python_bin,
            "-m",
            "torch.distributed.run",
            *self._torchrun_distributed_args(),
            "-m",
            entry,
            f"--config-path={run_dir!s}",
            f"--config-name={config_path.stem}",
        ]
        command_text = " ".join(shlex.quote(part) for part in command_parts)
        env = os.environ.copy()
        if self.training_gpu_ids:
            env[self._visible_devices_env_name()] = self.training_gpu_ids
        self._process_runner.run(command_text, cwd=run_dir, env=env, shell=True)

    def _export_sft_lora_adapter(self, *, output_dir: Path, run_dir: Path) -> Path:
        checkpoint_dir = self._latest_sft_checkpoint_dir(output_dir)
        if checkpoint_dir is None:
            raise FileNotFoundError(f"SFT veRL trainer did not create a checkpoint under: {output_dir}")

        python_bin = os.getenv("SFT_VERL_PYTHON") or os.getenv("VERL_PYTHON") or sys.executable
        target_dir = run_dir / "merged_hf"
        shutil.rmtree(target_dir, ignore_errors=True)
        command_parts = [
            python_bin,
            "-m",
            os.getenv("SFT_VERL_MERGER_ENTRY", "verl.model_merger"),
            "merge",
            "--backend",
            "fsdp",
            "--local_dir",
            str(checkpoint_dir),
            "--target_dir",
            str(target_dir),
            "--trust-remote-code",
            "--use_cpu_initialization",
        ]
        command_text = " ".join(shlex.quote(part) for part in command_parts)
        self._process_runner.run(command_text, cwd=run_dir, env=os.environ.copy(), shell=True)
        adapter_dir = target_dir / "lora_adapter"
        if not self._is_publishable_lora_dir(adapter_dir):
            raise FileNotFoundError(f"SFT veRL merger did not create a PEFT adapter: {adapter_dir}")
        return adapter_dir

    def _torchrun_distributed_args(self) -> list[str]:
        nnodes = self._env_int("SFT_VERL_NNODES", 1)
        nproc = self._gpu_count()
        if nnodes <= 1:
            return ["--standalone", "--nnodes=1", f"--nproc_per_node={nproc}"]

        node_rank = os.getenv("SFT_VERL_NODE_RANK", "").strip()
        master_addr = os.getenv("SFT_VERL_MASTER_ADDR", "").strip()
        master_port = os.getenv("SFT_VERL_MASTER_PORT", "29500").strip() or "29500"
        if not node_rank or not master_addr:
            raise ValueError("SFT_VERL_NODE_RANK and SFT_VERL_MASTER_ADDR are required when SFT_VERL_NNODES > 1")
        return [
            f"--nnodes={nnodes}",
            f"--node_rank={node_rank}",
            f"--master_addr={master_addr}",
            f"--master_port={master_port}",
            f"--nproc_per_node={nproc}",
        ]

    @staticmethod
    def _sft_run_root(tmp_root: str) -> Path:
        return Path(os.getenv("SFT_VERL_RUN_ROOT") or tmp_root).expanduser()

    @staticmethod
    def _write_sft_custom_cls(run_dir: Path) -> Path:
        source = Path(__file__).with_name("sft_verl_dataset.py").resolve()
        target = run_dir / source.name
        if target.resolve(strict=False) != source:
            shutil.copy2(source, target)
        return target

    def _gpu_count(self) -> int:
        return len([gpu for gpu in self.training_gpu_ids.split(",") if gpu.strip()]) or 1

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        raw = os.getenv(name, "").strip()
        if not raw:
            return default
        return int(raw)

    @staticmethod
    def _env_float(name: str, default: float) -> float:
        raw = os.getenv(name, "").strip()
        if not raw:
            return default
        return float(raw)

    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        raw = os.getenv(name, "").strip().lower()
        if not raw:
            return default
        return raw in {"1", "true", "yes", "on"}

    @staticmethod
    def _env_int_or_literal(name: str, default: int | str, literals: set[str]) -> int | str:
        raw = os.getenv(name, "").strip()
        if not raw:
            return default
        if raw in literals:
            return raw
        return int(raw)

    @staticmethod
    def _visible_devices_env_name() -> str:
        return os.getenv("ONLINE_RL_VISIBLE_DEVICES_ENV", "CUDA_VISIBLE_DEVICES").strip() or "CUDA_VISIBLE_DEVICES"

    @staticmethod
    def _is_ascend_env() -> bool:
        """Detect the Ascend NPU runtime via explicit knobs (no torch_npu import needed)."""
        device = os.getenv("SFT_VERL_DEVICE") or os.getenv("VERL_DEVICE") or ""
        if device.strip().lower() in {"ascend", "npu"}:
            return True
        return os.getenv("ONLINE_RL_VISIBLE_DEVICES_ENV", "").strip() == "ASCEND_RT_VISIBLE_DEVICES"

    @staticmethod
    def _slug(value: str) -> str:
        slug = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value.strip())
        return slug.strip("-")[:64] or "user"

    @staticmethod
    def _latest_sft_checkpoint_dir(output_dir: Path) -> Path | None:
        candidates = []
        for pattern in ("global_step_*", "run_*/checkpoints/global_step_*"):
            for path in output_dir.glob(pattern):
                if path.is_dir() and (path / "fsdp_config.json").exists():
                    candidates.append(path)
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime)

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
