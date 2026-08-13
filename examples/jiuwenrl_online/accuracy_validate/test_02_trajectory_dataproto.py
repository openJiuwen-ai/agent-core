# coding: utf-8

from __future__ import annotations

import json
import importlib.util
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from st_utils import FIXTURE_DIR, load_json, stable_json_digest

torch = pytest.importorskip("torch")


pytestmark = [pytest.mark.a5_precision]

AGENT_CORE_ROOT = Path(__file__).resolve().parents[3]


def _load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _load_verl_converter_cls():
    module = _load_module(
        "st_verl_converter",
        AGENT_CORE_ROOT / "openjiuwen" / "agent_evolving" / "agent_rl" / "rl_trainer" / "verl_converter.py",
    )
    return module.VerlDataProtoConverter


def _load_ppo_executor_cls():
    package_names = [
        "st_agent_rl",
        "st_agent_rl.online",
        "st_agent_rl.online.scheduler",
        "st_agent_rl.online.inference",
        "st_agent_rl.storage",
    ]
    for name in package_names:
        pkg = sys.modules.setdefault(name, types.ModuleType(name))
        pkg.__path__ = []

    notifier_mod = types.ModuleType("st_agent_rl.online.inference.notifier")
    notifier_mod.InferenceNotifier = object
    sys.modules[notifier_mod.__name__] = notifier_mod

    lora_mod = types.ModuleType("st_agent_rl.storage.lora_repo")
    lora_mod.LoRARepository = object
    sys.modules[lora_mod.__name__] = lora_mod

    module = _load_module(
        "st_agent_rl.online.scheduler.ppo_executor",
        AGENT_CORE_ROOT / "openjiuwen" / "agent_evolving" / "agent_rl" / "online" / "scheduler" / "ppo_executor.py",
    )
    return module.PPOTrainingExecutor


VerlDataProtoConverter = _load_verl_converter_cls()
PPOTrainingExecutor = _load_ppo_executor_cls()


@dataclass
class _FakeDataProto:
    batch: dict[str, Any]
    non_tensors: dict[str, Any]
    meta_info: dict[str, Any]

    @classmethod
    def from_dict(cls, *, tensors: dict[str, Any], non_tensors: dict[str, Any], meta_info: dict[str, Any]):
        return cls(batch=tensors, non_tensors=non_tensors, meta_info=meta_info)

    def digest(self) -> str:
        payload = {
            "batch": {
                key: {
                    "shape": list(value.shape),
                    "dtype": str(value.dtype),
                    "values": value.cpu().tolist(),
                }
                for key, value in sorted(self.batch.items())
            },
            "non_tensors": self.non_tensors,
            "meta_info": self.meta_info,
        }
        return stable_json_digest(payload)


def _converter(**kwargs) -> VerlDataProtoConverter:
    return VerlDataProtoConverter(dataproto_cls=_FakeDataProto, pad_token_id=0, **kwargs)


def test_fixed_trajectory_to_dataproto_is_repeatable():
    samples = load_json(FIXTURE_DIR / "a5_training_trajectories.json")
    first = _converter().convert_samples(samples)
    second = _converter().convert_samples(samples)

    assert first.digest() == second.digest()
    assert first.batch["input_ids"].shape[0] == 4
    assert first.batch["response_mask"].sum().item() == sum(
        len(item["trajectory"]["response_ids"]) for item in samples
    )
    assert first.meta_info["dropped_samples"] == 0


def test_dataproto_truncation_boundaries_are_explicit():
    samples = load_json(FIXTURE_DIR / "a5_training_trajectories.json")
    data = _converter(max_prompt_length=6, max_response_length=5).convert_samples(samples)

    assert tuple(data.batch["prompts"].shape) == (4, 6)
    assert tuple(data.batch["responses"].shape) == (4, 5)
    assert tuple(data.batch["input_ids"].shape) == (4, 11)
    assert data.meta_info["prompt_truncated_samples"] == 4
    assert data.meta_info["response_truncated_samples"] == 4
    assert data.batch["response_mask"].sum().item() == 20


def test_ppo_sample_chunking_keeps_batch_boundaries():
    samples = load_json(FIXTURE_DIR / "a5_training_trajectories.json")
    expanded = samples + samples + [samples[0]]
    executor = PPOTrainingExecutor(
        base_model_path="/tmp/not-used",
        lora_repo=None,
        notifier=None,
        nproc_per_node=4,
        training_gpu_ids="4,5,6,7",
        ppo_config_path=None,
        ppo_samples_per_step=4,
    )

    chunks = executor._sample_chunks(expanded)
    assert [len(chunk) for chunk in chunks] == [4, 4, 1]
    assert [chunk[0]["sample_id"] for chunk in chunks] == [
        "st-a5-train-001",
        "st-a5-train-001",
        "st-a5-train-001",
    ]
