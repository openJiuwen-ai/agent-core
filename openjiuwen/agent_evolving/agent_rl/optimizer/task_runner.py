# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import json
import os
from abc import abstractmethod
from typing import Optional

from omegaconf import OmegaConf
try:
    import ray_adapter as ray
    from ray_adapter._private.runtime_env.constants import RAY_JOB_CONFIG_JSON_ENV_VAR
except ModuleNotFoundError:
    import ray
    from ray._private.runtime_env.constants import RAY_JOB_CONFIG_JSON_ENV_VAR
from verl.single_controller.ray import RayWorkerGroup
from verl.trainer.ppo.ray_trainer import ResourcePoolManager, Role
from verl.trainer.ppo.reward import load_reward_manager
from verl.utils import hf_processor, hf_tokenizer
from verl.utils.fs import copy_to_local

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.agent_evolving.agent_rl.rl_trainer.verl_executor import VerlTrainingExecutor


_AGENT_CORE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)

_PPO_RAY_RUNTIME_ENV = {
    "env_vars": {
        "TOKENIZERS_PARALLELISM": "true",
        "NCCL_DEBUG": "WARN",
        "VLLM_LOGGING_LEVEL": "WARN",
        "VLLM_ALLOW_RUNTIME_LORA_UPDATING": "true",
        "CUDA_DEVICE_MAX_CONNECTIONS": "1",
        "NCCL_CUMEM_ENABLE": "0",
        "VLLM_ASCEND_ENABLE_NZ": "0",
    },
}


def get_ppo_ray_runtime_env() -> dict:
    working_dir = (
        json.loads(os.environ.get(RAY_JOB_CONFIG_JSON_ENV_VAR, "{}"))
        .get("runtime_env", {})
        .get("working_dir", None)
    )
    env_vars = _PPO_RAY_RUNTIME_ENV["env_vars"].copy()

    existing_pp = os.environ.get("PYTHONPATH", "")
    path_parts: list[str] = [_AGENT_CORE_DIR]
    if existing_pp:
        for raw_entry in existing_pp.split(":"):
            entry = raw_entry.strip()
            if not entry:
                continue
            normalized = os.path.abspath(entry)
            # Never prepend the package subdir itself. Doing so exposes
            # openjiuwen/agent_evolving/signal as a top-level "signal" module
            # and breaks Ray worker startup when stdlib signal is imported.
            if normalized.endswith(os.path.join("openjiuwen", "agent_evolving")):
                continue
            if normalized not in path_parts:
                path_parts.append(normalized)
    env_vars["PYTHONPATH"] = ":".join(path_parts)

    runtime_env: dict = {
        "env_vars": env_vars,
        **({"working_dir": None} if working_dir is None else {}),
    }
    for key in list(runtime_env["env_vars"].keys()):
        if key == "PYTHONPATH":
            continue
        if os.environ.get(key) is not None:
            runtime_env["env_vars"].pop(key, None)
    return runtime_env


class BaseTaskRunner:
    def __init__(self):
        self.verl_trainer: Optional[VerlTrainingExecutor] = None
        self.tokenizer = None
        self._config = None
        self._initialized = False

    @classmethod
    def _init_model_components(cls, config):
        model_dir = copy_to_local(config.actor_rollout_ref.model.path)
        allow_remote_code = bool(config.data.get("trust_remote_code", False))

        tok = hf_tokenizer(model_dir, trust_remote_code=allow_remote_code)
        proc = hf_processor(model_dir, use_fast=True)
        return tok, proc

    @classmethod
    def _init_resource_pools(cls, config, role_mapping: dict) -> ResourcePoolManager:
        pool_name = "global_pool"
        per_node = int(config.trainer.n_gpus_per_node)
        node_count = int(config.trainer.nnodes)
        spec = {pool_name: [per_node] * node_count}
        return ResourcePoolManager(resource_pool_spec=spec, mapping=role_mapping)

    @classmethod
    def _init_reward_functions(cls, config, tokenizer):
        import inspect

        reward_kwargs = config.reward_model.get("reward_kwargs", {})
        sig = inspect.signature(load_reward_manager)
        if "num_examine" in sig.parameters:
            reward_fn = load_reward_manager(
                config, tokenizer, num_examine=0, **reward_kwargs
            )
            val_reward_fn = load_reward_manager(
                config, tokenizer, num_examine=1, **reward_kwargs
            )
        else:
            reward_fn = load_reward_manager(config, tokenizer, **reward_kwargs)
            val_reward_fn = load_reward_manager(config, tokenizer, **reward_kwargs)
        return reward_fn, val_reward_fn

    @classmethod
    @abstractmethod
    def _init_datasets(cls, config, tokenizer, processor):
        pass

    def is_ready(self) -> bool:
        return self._initialized

    def get_global_steps(self) -> int:
        if self.verl_trainer is None:
            return 0
        return self.verl_trainer.global_steps


@ray.remote(num_cpus=1)
class OfflineTaskRunner(BaseTaskRunner):
    def __init__(self):
        super().__init__()
        self.main_trainer = None

    @classmethod
    def _init_worker_mapping(cls, config):
        from verl.workers.fsdp_workers import (
            ActorRolloutRefWorker,
            AsyncActorRolloutRefWorker,
            CriticWorker,
        )

        strategy = config.actor_rollout_ref.actor.strategy
        if strategy not in {"fsdp", "fsdp2"}:
            raise build_error(
                StatusCode.AGENT_RL_STRATEGY_NOT_SUPPORTED,
                strategy=strategy,
            )
        is_async = config.actor_rollout_ref.rollout.mode == "async"
        actor_rollout_cls = (
            AsyncActorRolloutRefWorker if is_async else ActorRolloutRefWorker
        )
        worker_pairs = [
            (Role.ActorRollout, ray.remote(actor_rollout_cls)),
            (Role.Critic, ray.remote(CriticWorker)),
            (Role.RefPolicy, ray.remote(actor_rollout_cls)),
        ]
        return dict(worker_pairs), RayWorkerGroup

    @classmethod
    def _init_datasets(cls, config, tokenizer, processor):
        from openjiuwen.agent_evolving.agent_rl.dataset import create_offline_datasets

        return create_offline_datasets(config, tokenizer, processor)

    def init_trainer(
        self,
        config,
        *,
        task_runner=None,
        agent_factory=None,
        task_data_fn=None,
        reward_fn=None,
        metrics_tracker=None,
        persistence=None,
    ):
        from openjiuwen.agent_evolving.agent_rl.offline.main_trainer import MainTrainer

        logger.info(OmegaConf.to_container(config, resolve=True))
        OmegaConf.resolve(config)

        tokenizer, processor = self._init_model_components(config)
        role_worker_mapping, ray_worker_group_cls = self._init_worker_mapping(config)

        pool = "global_pool"
        role_pool_mapping = dict.fromkeys(
            (Role.ActorRollout, Role.Critic, Role.RefPolicy),
            pool,
        )
        resource_pool_manager = self._init_resource_pools(config, role_pool_mapping)

        verl_reward_fn, val_reward_fn = self._init_reward_functions(config, tokenizer)
        bundle = self._init_datasets(config, tokenizer, processor)

        trainer_kwargs = {
            "config": config,
            "tokenizer": tokenizer,
            "processor": processor,
            "role_worker_mapping": role_worker_mapping,
            "resource_pool_manager": resource_pool_manager,
            "ray_worker_group_cls": ray_worker_group_cls,
            "reward_fn": verl_reward_fn,
            "val_reward_fn": val_reward_fn,
            "train_dataset": bundle.train_dataset,
            "val_dataset": bundle.val_dataset,
            "collate_fn": bundle.collate_fn,
            "train_sampler": bundle.train_sampler,
        }
        self.verl_trainer = VerlTrainingExecutor(**trainer_kwargs)
        self.verl_trainer.init_workers()

        self.main_trainer = MainTrainer(
            rl_trainer=self.verl_trainer,
            config=config,
            collate_fn=bundle.collate_fn,
            train_sampler=bundle.train_sampler,
            agent_factory=agent_factory,
            task_data_fn=task_data_fn,
            reward_fn=reward_fn,
            metrics_tracker=metrics_tracker,
            persistence=persistence,
        )

        self.tokenizer = tokenizer
        self._config = config
        self._initialized = True

    def start_trainer(self):
        if self.main_trainer is None:
            raise build_error(
                StatusCode.AGENT_RL_TRAINER_NOT_INITIALIZED,
                error_msg="call init_trainer() before start_trainer",
            )
        self.main_trainer.fit()


@ray.remote(num_cpus=1)
class OnlineTaskRunner(BaseTaskRunner):
    @classmethod
    def _init_worker_mapping(cls, config):
        from verl.workers.fsdp_workers import ActorRolloutRefWorker

        actor_remote = ray.remote(ActorRolloutRefWorker)
        worker_pairs = [
            (Role.ActorRollout, actor_remote),
            (Role.RefPolicy, actor_remote),
        ]
        return dict(worker_pairs), RayWorkerGroup

    @classmethod
    def _init_datasets(cls, config, tokenizer, processor):
        from openjiuwen.agent_evolving.agent_rl.dataset import create_online_datasets

        return create_online_datasets(config, tokenizer, processor)

    @staticmethod
    def _init_workers_no_rollout(trainer, resource_pool_manager, ray_worker_group_cls):
        from verl.single_controller.ray.base import create_colocated_worker_cls, RayClassWithInitArgs

        resource_pool_manager.create_resource_pool()

        trainer.resource_pool_to_cls = {
            pool: {} for pool in resource_pool_manager.resource_pool_dict.values()
        }

        actor_role = Role.ActorRollout
        actor_rollout_resource_pool = resource_pool_manager.get_resource_pool(actor_role)
        actor_rollout_cls = RayClassWithInitArgs(
            cls=trainer.role_worker_mapping[actor_role],
            config=trainer.config.actor_rollout_ref,
            role="actor",
        )
        trainer.resource_pool_to_cls[actor_rollout_resource_pool][str(actor_role)] = actor_rollout_cls

        if trainer.use_reference_policy:
            ref_pool = resource_pool_manager.get_resource_pool(Role.RefPolicy)
            ref_cls = RayClassWithInitArgs(
                trainer.role_worker_mapping[Role.RefPolicy],
                config=trainer.config.actor_rollout_ref,
                role="ref",
            )
            trainer.resource_pool_to_cls[ref_pool][str(Role.RefPolicy)] = ref_cls

        wg_kwargs = {"device_name": trainer.device_name}
        all_wg = {}
        for resource_pool, class_dict in trainer.resource_pool_to_cls.items():
            if not class_dict:
                continue
            worker_dict_cls = create_colocated_worker_cls(class_dict=class_dict)
            wg_dict = ray_worker_group_cls(
                resource_pool=resource_pool,
                ray_cls_with_init=worker_dict_cls,
                **wg_kwargs,
            )
            spawn_wg = wg_dict.spawn(prefix_set=class_dict.keys())
            all_wg.update(spawn_wg)

        if trainer.use_reference_policy and str(Role.RefPolicy) in all_wg:
            trainer.ref_policy_wg = all_wg[str(Role.RefPolicy)]
            trainer.ref_policy_wg.init_model()

        actor_key = str(actor_role)
        actor_wg = all_wg.get(actor_key)
        if actor_wg is None:
            raise build_error(
                StatusCode.AGENT_RL_DEPENDENCY_INIT_FAILED,
                error_msg=f"missing worker group for role={actor_key}",
            )
        trainer.actor_rollout_wg = actor_wg
        trainer.actor_rollout_wg.init_model()

        if getattr(trainer, "ref_in_actor", False):
            trainer.ref_policy_wg = trainer.actor_rollout_wg

        trainer.async_rollout_manager = None
        trainer.checkpoint_manager = None

    def init_trainer(self, config) -> None:
        logger.info("OnlineTaskRunner init_trainer: %s", OmegaConf.to_container(config, resolve=True))
        OmegaConf.resolve(config)
        self._config = config

        tokenizer, processor = self._init_model_components(config)
        self.tokenizer = tokenizer

        role_worker_mapping, ray_worker_group_cls = self._init_worker_mapping(config)

        pool = "global_pool"
        role_pool_mapping = dict.fromkeys((Role.ActorRollout, Role.RefPolicy), pool)
        resource_pool_manager = self._init_resource_pools(config, role_pool_mapping)

        verl_reward_fn, val_reward_fn = self._init_reward_functions(config, tokenizer)

        bundle = self._init_datasets(config, tokenizer, processor)

        trainer_kwargs = {
            "config": config,
            "tokenizer": tokenizer,
            "processor": processor,
            "role_worker_mapping": role_worker_mapping,
            "resource_pool_manager": resource_pool_manager,
            "ray_worker_group_cls": ray_worker_group_cls,
            "reward_fn": verl_reward_fn,
            "val_reward_fn": val_reward_fn,
            "train_dataset": bundle.train_dataset,
            "val_dataset": bundle.val_dataset,
            "collate_fn": bundle.collate_fn,
            "train_sampler": bundle.train_sampler,
        }
        self.verl_trainer = VerlTrainingExecutor(**trainer_kwargs)

        if bundle.cleanup_fn:
            bundle.cleanup_fn()

        self._init_workers_no_rollout(self.verl_trainer, resource_pool_manager, RayWorkerGroup)
        self.verl_trainer.global_steps = 0
        self._initialized = True
        logger.info("OnlineTaskRunner initialized (actor + ref, no critic)")

    def train_on_batch(self, data_proto) -> dict:
        if not self._initialized:
            raise RuntimeError("Call init_trainer() before train_on_batch()")

        self.verl_trainer.global_steps += 1
        logger.info(
            "OnlineTaskRunner train_on_batch step=%d batch_size=%d",
            self.verl_trainer.global_steps, len(data_proto),
        )

        if "old_log_probs" in data_proto.batch.keys():
            data_proto.batch.pop("old_log_probs")

        metrics = self.verl_trainer.train_step(data_proto, data_proto)
        logger.info("train_step completed: %s", {k: v for k, v in metrics.items() if isinstance(v, (int, float))})
        return metrics

    def export_lora(self, output_dir: str, base_model_path: str) -> str:
        import shutil
        from pathlib import Path

        if not self._initialized:
            raise RuntimeError("Call init_trainer() before export_lora()")

        ckpt_dir = Path(output_dir) / "fsdp_ckpt"
        ckpt_dir.mkdir(parents=True, exist_ok=True)

        old_default_local_dir = self._config.trainer.default_local_dir
        try:
            OmegaConf.set_struct(self._config, False)
            self._config.trainer.default_local_dir = str(ckpt_dir)
            self.verl_trainer.save_checkpoint()
        finally:
            self._config.trainer.default_local_dir = old_default_local_dir

        step_dir = self._find_latest_checkpoint(str(ckpt_dir))

        verl_lora_dir = step_dir / "actor" / "lora_adapter"
        if verl_lora_dir.exists() and (verl_lora_dir / "adapter_model.safetensors").exists():
            peft_dir = str(Path(output_dir) / "peft_adapter")
            shutil.copytree(str(verl_lora_dir), peft_dir, dirs_exist_ok=True)
        else:
            actor_ckpt = step_dir / "actor"
            peft_dir = str(Path(output_dir) / "peft_adapter")
            self._convert_fsdp_to_peft(actor_ckpt, base_model_path, peft_dir)

        shutil.rmtree(str(ckpt_dir), ignore_errors=True)

        logger.info("Exported LoRA adapter to %s", peft_dir)
        return peft_dir

    @staticmethod
    def _find_latest_checkpoint(output_dir: str):
        from pathlib import Path

        output_path = Path(output_dir)
        tracker = output_path / "latest_checkpointed_iteration.txt"
        if tracker.exists():
            step = tracker.read_text().strip()
            ckpt = output_path / f"global_step_{step}"
            if ckpt.exists():
                return ckpt
        candidates = sorted(output_path.glob("global_step_*"), key=lambda p: int(p.name.split("_")[-1]))
        if not candidates:
            raise FileNotFoundError(f"No checkpoint found in {output_dir}")
        return candidates[-1]

    @staticmethod
    def _convert_fsdp_to_peft(ckpt_dir, base_model: str, lora_output_dir: str) -> str:
        import torch
        from pathlib import Path
        from safetensors.torch import save_file

        ckpt_dir = Path(ckpt_dir)
        fsdp_config_path = ckpt_dir / "fsdp_config.json"
        if not fsdp_config_path.exists():
            raise FileNotFoundError(f"fsdp_config.json not found in {ckpt_dir}")

        with open(fsdp_config_path) as f:
            fsdp_cfg = json.load(f)
        world_size = fsdp_cfg["world_size"]

        lora_meta_path = ckpt_dir / "lora_train_meta.json"
        if lora_meta_path.exists():
            with open(lora_meta_path) as f:
                lora_meta = json.load(f)
        else:
            lora_meta = {"r": 16, "lora_alpha": 32, "task_type": "CAUSAL_LM"}

        logger.info("Loading and merging %d FSDP shards from %s ...", world_size, ckpt_dir)
        state_dicts = []
        for rank in range(world_size):
            shard_path = ckpt_dir / f"model_world_size_{world_size}_rank_{rank}.pt"
            sd = torch.load(shard_path, map_location="cpu", weights_only=False)
            state_dicts.append(sd)

        merged: dict[str, torch.Tensor] = {}
        for key in state_dicts[0]:
            tensors = []
            for sd in state_dicts:
                t = sd[key]
                try:
                    from torch.distributed._tensor import DTensor
                except ImportError:
                    DTensor = None
                if DTensor is not None and isinstance(t, DTensor):
                    to_local = getattr(t, "to_local", None)
                    if not callable(to_local):
                        raise RuntimeError(
                            "DTensor detected but missing public to_local(); "
                            "please upgrade PyTorch to a version that supports DTensor.to_local()."
                        )
                    t = to_local()
                tensors.append(t)
            if tensors[0].shape == tensors[-1].shape and torch.equal(tensors[0], tensors[-1]):
                merged[key] = tensors[0]
            else:
                try:
                    merged[key] = torch.cat(tensors, dim=0)
                except RuntimeError:
                    merged[key] = tensors[0]

        lora_state_dict = {}
        for key, value in merged.items():
            if "lora_" not in key:
                continue
            clean_key = key.replace(".default.weight", ".weight")
            lora_state_dict[clean_key] = value

        if not lora_state_dict:
            logger.warning("No LoRA parameters found in checkpoint; publishing raw FSDP checkpoint")
            return str(ckpt_dir)

        out = Path(lora_output_dir)
        out.mkdir(parents=True, exist_ok=True)

        save_file(lora_state_dict, str(out / "adapter_model.safetensors"))

        adapter_config = {
            "base_model_name_or_path": base_model.rstrip("/"),
            "bias": "none",
            "fan_in_fan_out": False,
            "inference_mode": True,
            "init_lora_weights": True,
            "lora_alpha": lora_meta.get("lora_alpha", 32),
            "lora_dropout": 0.0,
            "modules_to_save": None,
            "peft_type": "LORA",
            "r": lora_meta.get("r", 16),
            "revision": None,
            "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj",
                               "gate_proj", "up_proj", "down_proj"],
            "task_type": lora_meta.get("task_type", "CAUSAL_LM"),
        }
        with open(out / "adapter_config.json", "w") as f:
            json.dump(adapter_config, f, indent=2)

        logger.info("Converted FSDP checkpoint to PEFT LoRA adapter at %s (%d params)",
                     out, len(lora_state_dict))
        return str(out)
