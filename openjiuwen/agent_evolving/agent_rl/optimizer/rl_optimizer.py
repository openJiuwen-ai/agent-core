# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.agent_evolving.agent_rl.config.offline_config import RLConfig


class BaseRLOptimizer(ABC):
    def __init__(self, config: RLConfig) -> None:
        self.config = config

        timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.run_name = f"{config.training.experiment_name}_{timestamp}"
        logger.info(
            "Run name: %s (project: %s)", self.run_name, config.training.project_name
        )

    def _setup_environment(self) -> None:
        train_cfg = self.config.training
        os.environ["HYDRA_FULL_ERROR"] = "1"
        os.environ["VLLM_PREFIX_CACHING"] = "0"
        os.environ["ENABLE_PREFIX_CACHE"] = "false"
        os.environ["TORCHINDUCTOR_COMPILE"] = "0"
        os.environ["TORCHDYNAMO_DISABLE"] = "1"
        os.environ["VLLM_ASCEND_DISABLE_CAMEM"] = "1"
        os.environ["DISABLE_CAMEM_ALLOCATOR"] = "1"
        os.environ["VLLM_DISABLE_COMPILE_CACHE"] = "1"
        os.environ["VLLM_ASCEND_CAMEM_ENABLE"] = "0"
        os.environ["ASCEND_LAUNCHING_BLOCKING"] = "1"
        os.environ["ASCEND_RT_VISIBLE_DEVICES"] = train_cfg.visible_device
        os.environ["VLLM_USE_V1"] = "1"
        for k in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"]:
            os.environ.pop(k, None)
        os.environ["no_proxy"] = "127.0.0.1,localhost"
        os.environ["NO_PROXY"] = "127.0.0.1,localhost"

    @staticmethod
    def _init_ray(cfg) -> None:
        from omegaconf import OmegaConf

        import ray_adapter as ray
        from openjiuwen.agent_evolving.agent_rl.optimizer.task_runner import (
            get_ppo_ray_runtime_env,
        )

        if ray.is_initialized():
            return
        default_runtime_env = get_ppo_ray_runtime_env()
        ray_init_kwargs = cfg.ray_kwargs.get("ray_init", {})
        runtime_env_kwargs = ray_init_kwargs.get("runtime_env", {})
        runtime_env = OmegaConf.merge(default_runtime_env, runtime_env_kwargs)
        ray_init_kwargs = OmegaConf.create(
            {**ray_init_kwargs, "runtime_env": runtime_env}
        )
        logger.info("ray init kwargs: %s", ray_init_kwargs)
        ray.init(**OmegaConf.to_container(ray_init_kwargs))

    @abstractmethod
    def init_trainer(self) -> None:
        pass

    @abstractmethod
    def start_training(self) -> None:
        pass

    @abstractmethod
    def stop(self) -> None:
        pass

    def train(self) -> None:
        self.init_trainer()
        self.start_training()


class OfflineRLOptimizer(BaseRLOptimizer):
    def __init__(self, config: RLConfig) -> None:
        super().__init__(config)
        self._runner = None
        self._agent_factory = None
        self._task_data_fn = None
        self._reward_fn_name = None
        self._reward_fn = None
        self._tools = []
        self._tool_names = []

    def set_tools(self, tools: list) -> None:
        self._tools = tools
        self._tool_names = [
            getattr(t, "name", getattr(t, "__name__", str(t))) for t in tools
        ]

    def set_task_data_fn(
        self, fn: Callable[[Dict[str, Any]], Dict[str, Any]]
    ) -> None:
        self._task_data_fn = fn

    def register_reward(self, fn, name: Optional[str] = None) -> None:
        from openjiuwen.agent_evolving.agent_rl.reward import reward_registry

        reward_name = name or fn.__name__
        reward_registry.register(reward_name, fn)
        self._reward_fn_name = reward_name
        self._reward_fn = fn
        logger.info("Registered reward function: %s", reward_name)

    def set_agent_factory(self, factory: Callable) -> None:
        self._agent_factory = factory

    def _get_rollout_reward_fn(self):
        return getattr(self, "_reward_fn", None)

    def _resolve_agent_factory(self):
        if self._agent_factory is not None:
            return self._agent_factory
        if self._tools:
            from openjiuwen.agent_evolving.agent_rl.offline.runtime.agent_factory import (
                build_agent_factory,
            )
            return build_agent_factory(
                self.config.runtime, self._tools, self._tool_names
            )
        return None

    def _build_persistence(self, config: RLConfig):
        from openjiuwen.agent_evolving.agent_rl.offline.store.base import RolloutPersistence
        from openjiuwen.agent_evolving.agent_rl.offline.store.file_store import FileRolloutStore
        from openjiuwen.agent_evolving.agent_rl.offline.store.null_store import NullRolloutStore

        p_cfg = config.persistence
        if not p_cfg.enabled:
            return NullRolloutStore()
        save_path = os.path.join(p_cfg.save_path, self.run_name)
        return FileRolloutStore(
            save_path=save_path, flush_interval=p_cfg.flush_interval,
        )

    def _build_metrics_tracker(self, config: RLConfig):
        from openjiuwen.agent_evolving.agent_rl.offline.store.metrics_tracker import (
            RLMetricsTracker,
        )

        return RLMetricsTracker(
            project_name=config.training.project_name,
            experiment_name=self.run_name,
            backends=config.training.logger,
            config=config.model_dump() if hasattr(config, "model_dump") else {},
        )

    def _compose_hydra_config(self):
        from omegaconf import OmegaConf

        from hydra import compose, initialize
        from openjiuwen.agent_evolving.agent_rl.config.offline_config import VerlHydraOverlay

        train_cfg = self.config.training
        rollout_cfg = self.config.rollout

        with initialize(version_base=None, config_path="pkg://verl.trainer.config"):
            ppo_cfg = compose(config_name="ppo_trainer")

        OmegaConf.set_struct(ppo_cfg, False)
        overlay = OmegaConf.create(VerlHydraOverlay().model_dump())
        OmegaConf.set_struct(overlay, False)
        base_cfg = OmegaConf.merge(ppo_cfg, overlay)
        OmegaConf.set_struct(base_cfg, False)

        dynamic = {
            "algorithm": {
                "adv_estimator": train_cfg.algorithm_adv_estimator,
                "use_kl_in_reward": train_cfg.algorithm_use_kl_in_reward,
                "filter_groups": train_cfg.algorithm_filter_groups,
                "norm_adv_by_std_in_grpo": train_cfg.algorithm_norm_adv_by_std_in_grpo,
            },
            "data": {
                "train_files": train_cfg.resolved_train_files,
                "val_files": train_cfg.resolved_val_files,
                "train_batch_size": train_cfg.train_batch_size,
                "max_prompt_length": train_cfg.max_prompt_length,
                "max_response_length": train_cfg.max_response_length,
                "truncation": train_cfg.truncation,
            },
            "actor_rollout_ref": {
                "model": {"path": train_cfg.model_path},
                "actor": {
                    "ppo_micro_batch_size_per_gpu": train_cfg.micro_batch_size_per_gpu,
                    "optim": {"lr": rollout_cfg.actor_optimizer_lr},
                    "use_kl_loss": rollout_cfg.actor_use_kl_loss,
                    "kl_loss_coef": rollout_cfg.actor_kl_loss_coef,
                    "entropy_coeff": rollout_cfg.actor_entropy_coef,
                    "clip_ratio_low": rollout_cfg.actor_clip_ratio_low,
                    "clip_ratio_high": rollout_cfg.actor_clip_ratio_high,
                    "loss_agg_mode": rollout_cfg.actor_loss_agg_mode,
                },
                "rollout": {
                    "n": rollout_cfg.rollout_n,
                    "log_prob_micro_batch_size_per_gpu": train_cfg.micro_batch_size_per_gpu,
                },
                "ref": {
                    "log_prob_micro_batch_size_per_gpu": train_cfg.micro_batch_size_per_gpu,
                },
            },
            "trainer": {
                "val_before_train": train_cfg.val_before_train,
                "critic_warmup": train_cfg.critic_warmup,
                "logger": train_cfg.logger,
                "project_name": train_cfg.project_name,
                "experiment_name": self.run_name,
                "nnodes": train_cfg.nnodes,
                "save_freq": train_cfg.save_freq,
                "test_freq": train_cfg.test_freq,
                "default_local_dir": train_cfg.save_path,
                "total_epochs": train_cfg.total_epochs,
                "n_gpus_per_node": train_cfg.n_gpus_per_node,
                "runtime_parallel_num": train_cfg.rollout_concurrency,
            },
            "JiuwenRL": {
                "whole_trajectory": train_cfg.whole_trajectory,
            },
        }

        if self.config.ada is not None:
            ada_cfg = self.config.ada
            dynamic["trainer"]["rollout_max_round"] = ada_cfg.rollout_max_round
            dynamic["JiuwenRL"]["custom_fn"] = {
                "classifier": "default_classify_rollouts",
                "validator": "validate_stop_balanced",
                "sampler": "sampling_ada",
            }
            dynamic["JiuwenRL"]["final_keep_per_prompt"] = ada_cfg.final_keep_per_prompt

        return OmegaConf.merge(base_cfg, dynamic)

    def init_trainer(self) -> None:
        import ray_adapter as ray
        from openjiuwen.agent_evolving.agent_rl.optimizer.task_runner import OfflineTaskRunner

        cfg = self._compose_hydra_config()
        self._setup_environment()
        self._init_ray(cfg)

        persistence = self._build_persistence(self.config)
        metrics_tracker = self._build_metrics_tracker(self.config)
        agent_factory = self._resolve_agent_factory()

        self._runner = OfflineTaskRunner.options(
            name="ppo_task_runner", lifetime="detached"
        ).remote()
        ray.get(
            self._runner.init_trainer.remote(
                cfg,
                agent_factory=agent_factory,
                task_data_fn=self._task_data_fn,
                reward_fn=self._get_rollout_reward_fn(),
                metrics_tracker=metrics_tracker,
                persistence=persistence,
            )
        )
        logger.info("Offline trainer initialized successfully")

    def start_training(self) -> None:
        import ray_adapter as ray
        from openjiuwen.core.common.exception.codes import StatusCode
        from openjiuwen.core.common.exception.errors import build_error

        if self._runner is None:
            try:
                self._runner = ray.get_actor("ppo_task_runner")
            except ValueError as e:
                raise build_error(
                    StatusCode.AGENT_RL_TRAINER_NOT_INITIALIZED,
                    error_msg="call init_trainer() first",
                    cause=e,
                ) from e
        ray.get(self._runner.start_trainer.remote())
        logger.info("Offline training completed")

    def stop(self) -> None:
        import ray_adapter as ray

        try:
            actor = ray.get_actor("ppo_task_runner")
            ray.kill(actor)
        except ValueError:
            logger.info("No Ray actor named 'ppo_task_runner' running")
        self._runner = None
        ray.shutdown()
        logger.info("Ray shutdown complete")


class OnlineRLOptimizer(BaseRLOptimizer):
    def __init__(self, config: RLConfig) -> None:
        super().__init__(config)
        self._redis_url: str = ""
        self._lora_repo_root: str = ""
        self._vllm_url: str = ""
        self._poll_interval: float = 30.0
        self._min_samples: int = 4
        self._ppo_config_path: Optional[str] = None
        self._training_gpu_ids: str = ""
        self._nproc_per_node: int = 1
        self._scheduler = None
        self._ppo_runner = None

    def setup_redis(
        self,
        redis_url: str,
        poll_interval: float = 30.0,
        min_samples: int = 4,
    ) -> "OnlineRLOptimizer":
        self._redis_url = redis_url.rstrip("/")
        self._poll_interval = poll_interval
        self._min_samples = min_samples
        return self

    def setup_gateway(
        self,
        gateway_url: str,
        poll_interval: float = 30.0,
        min_samples: int = 4,
    ) -> "OnlineRLOptimizer":
        logger.warning(
            "setup_gateway() is deprecated for OnlineRLOptimizer; use setup_redis() instead"
        )
        normalized = gateway_url.strip()
        if not normalized.startswith(("redis://", "rediss://")):
            raise ValueError(
                "setup_gateway() no longer accepts HTTP Gateway URLs. "
                "OnlineRLOptimizer now polls Redis directly; call setup_redis() "
                "with a redis:// or rediss:// URL instead."
            )
        return self.setup_redis(
            normalized,
            poll_interval=poll_interval,
            min_samples=min_samples,
        )

    def setup_lora_repo(self, lora_repo_root: str) -> "OnlineRLOptimizer":
        self._lora_repo_root = lora_repo_root
        return self

    def setup_inference(self, vllm_url: str) -> "OnlineRLOptimizer":
        self._vllm_url = vllm_url
        return self

    def setup_training_gpu(
        self,
        gpu_ids: str,
        nproc_per_node: int = 1,
    ) -> "OnlineRLOptimizer":
        self._training_gpu_ids = gpu_ids
        self._nproc_per_node = nproc_per_node
        return self

    def setup_ppo_config(self, ppo_config_path: str) -> "OnlineRLOptimizer":
        self._ppo_config_path = ppo_config_path
        return self

    def init_trainer(self) -> None:
        import ray_adapter as ray
        from openjiuwen.agent_evolving.agent_rl.optimizer.task_runner import (
            get_ppo_ray_runtime_env,
        )

        self._setup_environment()

        if not ray.is_initialized():
            runtime_env = get_ppo_ray_runtime_env()
            if self._training_gpu_ids:
                runtime_env.setdefault("env_vars", {})[
                    "CUDA_VISIBLE_DEVICES"
                ] = self._training_gpu_ids
            ray.init(runtime_env=runtime_env, namespace="OnlineRL")

        logger.info(
            "Online RL environment initialized (GPUs: %s)", self._training_gpu_ids
        )

    def start_training(self) -> None:
        if not self._redis_url:
            raise ValueError(
                "Redis URL not configured. Call setup_redis() first."
            )

        from openjiuwen.agent_evolving.agent_rl.online.scheduler.online_training_scheduler import (
            OnlineTrainingScheduler,
        )
        from openjiuwen.agent_evolving.agent_rl.storage.lora_repo import LoRARepository
        from openjiuwen.agent_evolving.agent_rl.online.inference.notifier import InferenceNotifier

        lora_repo = (
            LoRARepository(self._lora_repo_root) if self._lora_repo_root else None
        )
        notifier = InferenceNotifier(self._vllm_url) if self._vllm_url else None

        self._scheduler = OnlineTrainingScheduler(
            redis_url=self._redis_url,
            poll_interval=self._poll_interval,
            min_samples_for_training=self._min_samples,
            base_model_path=self.config.training.model_path,
            lora_repo=lora_repo,
            notifier=notifier,
            nproc_per_node=self._nproc_per_node,
            training_gpu_ids=self._training_gpu_ids,
            ppo_config_path=self._ppo_config_path,
        )
        self._scheduler.start()
        logger.info(
            "OnlineTrainingScheduler started: redis=%s min_samples=%d poll=%.0fs",
            self._redis_url,
            self._min_samples,
            self._poll_interval,
        )

    def stop(self) -> None:
        import ray_adapter as ray

        if self._scheduler:
            self._scheduler.stop()
            self._scheduler = None
        if ray.is_initialized():
            ray.shutdown()
        logger.info("Online RL stopped")

    def train_on_batch(self, samples: list[dict]) -> dict:
        import ray_adapter as ray
        from openjiuwen.agent_evolving.agent_rl.online.scheduler.ppo_config import compose_online_ppo_config
        from openjiuwen.agent_evolving.agent_rl.optimizer.task_runner import OnlineTaskRunner
        from openjiuwen.agent_evolving.agent_rl.rl_trainer.verl_converter import VerlDataProtoConverter

        if self._ppo_runner is None:
            config = compose_online_ppo_config(
                model_path=self.config.training.model_path,
                n_gpus_per_node=self._nproc_per_node,
                config_path=self._ppo_config_path,
            )
            self._ppo_runner = OnlineTaskRunner.options(
                name="online_ppo_runner", lifetime="detached"
            ).remote()
            ray.get(self._ppo_runner.init_trainer.remote(config))

        pad_token_id = 0
        try:
            from transformers import AutoTokenizer

            tokenizer = AutoTokenizer.from_pretrained(
                self.config.training.model_path, trust_remote_code=True
            )
            pad_token_id = tokenizer.pad_token_id or 0
        except Exception as e:
            logger.warning(
                "Failed to load tokenizer for pad_token_id, defaulting to 0. model_path=%s err=%s",
                self.config.training.model_path,
                e,
            )

        converter = VerlDataProtoConverter(pad_token_id=pad_token_id)
        data_proto = converter.convert_samples(samples)

        metrics = ray.get(self._ppo_runner.train_on_batch.remote(data_proto))
        return metrics

    def export_lora(self, output_dir: str) -> str:
        import ray_adapter as ray

        if self._ppo_runner is None:
            raise RuntimeError(
                "No PPO runner available. Call train_on_batch() first."
            )
        return ray.get(
            self._ppo_runner.export_lora.remote(
                output_dir, self.config.training.model_path
            )
        )
