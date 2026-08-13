#!/usr/bin/env python3
# coding: utf-8

"""Launch verl SFT with optional full-parameter layer filtering.

This module is loaded by torchrun instead of ``verl.trainer.sft_trainer`` when
full-parameter SFT should update only selected transformer layers. It keeps the
installed verl package untouched and patches the FSDP language-model engine
registration before hydra builds the trainer.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Iterable

import torch


@dataclass(frozen=True)
class LayerSelection:
    mode: str
    indices: frozenset[int] = frozenset()
    count: int | None = None

    @property
    def enabled(self) -> bool:
        return self.mode != "all"


def _parse_csv_indices(value: str) -> frozenset[int]:
    indices: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start = int(start_s)
            end = int(end_s)
            if end < start:
                raise ValueError(f"invalid layer range {part!r}: end < start")
            indices.update(range(start, end + 1))
        else:
            indices.add(int(part))
    return frozenset(indices)


def parse_layer_selection(spec: str | None) -> LayerSelection:
    value = (spec or "all").strip().lower()
    if not value or value in {"all", "full", "none"}:
        return LayerSelection(mode="all")
    if value.startswith("last:"):
        count = int(value.split(":", 1)[1])
        if count <= 0:
            raise ValueError("last:N requires N > 0")
        return LayerSelection(mode="last", count=count)
    if value.startswith("first:"):
        count = int(value.split(":", 1)[1])
        if count <= 0:
            raise ValueError("first:N requires N > 0")
        return LayerSelection(mode="first", count=count)
    if value.startswith("layers:"):
        indices = _parse_csv_indices(value.split(":", 1)[1])
        if not indices:
            raise ValueError("layers: requires at least one layer index")
        return LayerSelection(mode="layers", indices=indices)
    raise ValueError(
        "SFT_FULL_TRAIN_LAYER_SPEC must be one of: all, last:N, first:N, layers:i,j,k or layers:start-end"
    )


def _iter_layer_modules(module) -> list[tuple[int, torch.nn.Module]]:
    candidates = (
        "model.layers",
        "base_model.model.model.layers",
        "module.model.layers",
        "transformer.h",
        "gpt_neox.layers",
        "model.decoder.layers",
        "language_model.model.layers",
    )
    for path in candidates:
        current = module
        ok = True
        for attr in path.split("."):
            if not hasattr(current, attr):
                ok = False
                break
            current = getattr(current, attr)
        if ok and isinstance(current, torch.nn.ModuleList):
            return list(enumerate(current))

    named = []
    pattern = re.compile(r"(?:^|\.)(?:layers|h)\.(\d+)(?:\.|$)")
    seen = set()
    for name, submodule in module.named_modules():
        match = pattern.search(name)
        if not match:
            continue
        idx = int(match.group(1))
        key = (idx, id(submodule))
        if key in seen:
            continue
        seen.add(key)
        if name.endswith(f".{idx}") or name.endswith(f"layers.{idx}") or name.endswith(f"h.{idx}"):
            named.append((idx, submodule))
    if named:
        return sorted(named, key=lambda item: item[0])
    raise RuntimeError("could not locate transformer layers on the loaded model")


def _selected_indices(selection: LayerSelection, layer_count: int) -> set[int]:
    if selection.mode == "all":
        return set(range(layer_count))
    if selection.mode == "last":
        assert selection.count is not None
        return set(range(max(0, layer_count - selection.count), layer_count))
    if selection.mode == "first":
        assert selection.count is not None
        return set(range(min(selection.count, layer_count)))
    if selection.mode == "layers":
        return {idx for idx in selection.indices if 0 <= idx < layer_count}
    raise ValueError(f"unknown layer selection mode: {selection.mode}")


def _set_requires_grad(parameters: Iterable[torch.nn.Parameter], enabled: bool) -> int:
    count = 0
    for param in parameters:
        param.requires_grad_(enabled)
        count += param.numel()
    return count


def apply_full_sft_layer_filter(module, selection: LayerSelection) -> None:
    if not selection.enabled:
        return

    layers = _iter_layer_modules(module)
    selected = _selected_indices(selection, len(layers))
    if not selected:
        raise ValueError(f"layer selection {selection} matched no layers out of {len(layers)}")

    total_params = _set_requires_grad(module.parameters(), False)
    trainable_params = 0
    selected_names = []
    for idx, layer in layers:
        if idx not in selected:
            continue
        selected_names.append(str(idx))
        trainable_params += _set_requires_grad(layer.parameters(), True)

    train_embeddings = os.getenv("SFT_FULL_TRAIN_EMBEDDINGS", "0") == "1"
    train_lm_head = os.getenv("SFT_FULL_TRAIN_LM_HEAD", "1") != "0"
    for name, submodule in module.named_modules():
        if train_embeddings and isinstance(submodule, torch.nn.Embedding):
            trainable_params += _set_requires_grad(submodule.parameters(), True)
        if train_lm_head and name in {"lm_head", "embed_out"}:
            trainable_params += _set_requires_grad(submodule.parameters(), True)

    if torch.distributed.is_available() and torch.distributed.is_initialized():
        rank = torch.distributed.get_rank()
    else:
        rank = 0
    if rank == 0:
        print(
            "[full-sft] train_layer_spec="
            f"{os.getenv('SFT_FULL_TRAIN_LAYER_SPEC')} selected_layers={','.join(selected_names)} "
            f"train_embeddings={train_embeddings} train_lm_head={train_lm_head} "
            f"trainable_params={trainable_params} total_params={total_params}"
        )


def patch_fsdp_language_model_engine() -> None:
    selection = parse_layer_selection(os.getenv("SFT_FULL_TRAIN_LAYER_SPEC", "all"))
    if not selection.enabled:
        return

    from verl.workers.engine.base import EngineRegistry
    from verl.workers.engine.fsdp.transformer_impl import FSDPEngineWithLMHead
    from verl.utils.debug import log_gpu_memory_usage
    from verl.utils.model import print_model_size

    class LayerFilteredFSDPEngineWithLMHead(FSDPEngineWithLMHead):
        def _build_model_optimizer(self):
            module = self._build_module()
            if self._is_lora:
                raise ValueError("full-parameter SFT layer filtering requires model.lora_rank=0")

            apply_full_sft_layer_filter(module, selection)

            torch.distributed.barrier()
            if self.rank == 0:
                print_model_size(module)
            log_gpu_memory_usage("After init model from HF AutoModel", logger=None)

            log_gpu_memory_usage("Before FSDP", logger=None)
            module = self._build_fsdp_module(module)
            log_gpu_memory_usage("After FSDP", logger=None)

            if not self.engine_config.forward_only:
                optimizer = self._build_optimizer(module)
                lr_scheduler = self._build_lr_scheduler(optimizer)
            else:
                optimizer = None
                lr_scheduler = None

            self.module = module
            self.optimizer = optimizer
            self.lr_scheduler = lr_scheduler

    for backend in ("fsdp", "fsdp2"):
        for device in ("cuda", "npu"):
            EngineRegistry._engines.setdefault("language_model", {}).setdefault(backend, {})[
                device
            ] = LayerFilteredFSDPEngineWithLMHead


def main() -> None:
    patch_fsdp_language_model_engine()
    from verl.trainer.sft_trainer import main as verl_sft_main

    verl_sft_main()


if __name__ == "__main__":
    main()
