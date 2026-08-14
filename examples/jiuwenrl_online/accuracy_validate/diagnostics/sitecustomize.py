# coding: utf-8

"""Optional online-RL determinism diagnostics loaded via PYTHONPATH.

This module is intentionally inert unless ``ONLINE_RL_DETERMINISM_DEBUG=1``.
It is imported by Python automatically as ``sitecustomize`` before normal
application imports, which lets us wrap ``peft.get_peft_model`` before verl
imports it into ``fsdp_workers.py``.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
from typing import Any


def _enabled() -> bool:
    return os.getenv("ONLINE_RL_DETERMINISM_DEBUG", "0") == "1"


def _safe_seed() -> int | None:
    raw = os.getenv("ONLINE_RL_DETERMINISTIC_SEED", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _apply_seed() -> None:
    seed = _safe_seed()
    if seed is None:
        return
    random.seed(seed)
    try:
        import numpy as np
        np.random.seed(seed)
    except Exception:
        pass
    try:
        import torch
        torch.random.default_generator.manual_seed(seed)
    except Exception:
        pass


def _seed_summary() -> dict[str, Any]:
    out: dict[str, Any] = {
        "pid": os.getpid(),
        "rank": os.getenv("RANK"),
        "local_rank": os.getenv("LOCAL_RANK"),
        "cuda_visible_devices": os.getenv("CUDA_VISIBLE_DEVICES"),
        "online_rl_seed": os.getenv("ONLINE_RL_DETERMINISTIC_SEED"),
        "python_random_head": list(random.getstate()[1][:5]),
    }
    try:
        import numpy as np
        out["numpy_random_head"] = [int(x) for x in np.random.get_state()[1][:5]]
    except Exception as exc:
        out["numpy_error"] = repr(exc)
    try:
        import torch
        out["torch_initial_seed"] = int(torch.initial_seed())
    except Exception as exc:
        out["torch_error"] = repr(exc)
    return out


def _lora_digest(model: Any) -> dict[str, Any]:
    h = hashlib.sha256()
    count = 0
    numel = 0
    samples: list[dict[str, Any]] = []
    for name, param in model.named_parameters():
        if "lora_" not in name:
            continue
        tensor = param.detach().cpu().contiguous()
        h.update(name.encode("utf-8"))
        h.update(str(tuple(tensor.shape)).encode("utf-8"))
        h.update(str(tensor.dtype).encode("utf-8"))
        h.update(tensor.numpy().tobytes())
        count += 1
        numel += int(tensor.numel())
        if len(samples) < 6:
            tf = tensor.float()
            samples.append({
                "name": name,
                "shape": list(tensor.shape),
                "dtype": str(tensor.dtype),
                "mean": float(tf.mean().item()) if tf.numel() else 0.0,
                "std": float(tf.std().item()) if tf.numel() > 1 else 0.0,
                "sum": float(tf.sum().item()) if tf.numel() else 0.0,
            })
    return {
        "lora_tensor_count": count,
        "lora_numel": numel,
        "lora_sha256": h.hexdigest() if count else "",
        "lora_samples": samples,
    }


def _emit(event: str, **payload: Any) -> None:
    payload = {"event": event, **payload}
    print("[ONLINE_RL_DETERMINISM] " + json.dumps(payload, sort_keys=True), flush=True)


def _patch_peft() -> None:
    try:
        import peft
    except Exception as exc:
        _emit("peft_import_failed", error=repr(exc), seed_summary=_seed_summary())
        return

    if getattr(peft, "_online_rl_determinism_patched", False):
        return
    original_get_peft_model = peft.get_peft_model

    def wrapped_get_peft_model(*args: Any, **kwargs: Any) -> Any:
        _emit("before_get_peft_model", seed_summary=_seed_summary())
        model = original_get_peft_model(*args, **kwargs)
        _emit("after_get_peft_model", seed_summary=_seed_summary(), lora=_lora_digest(model))
        return model

    peft.get_peft_model = wrapped_get_peft_model
    peft._online_rl_determinism_patched = True
    _emit("peft_patched", seed_summary=_seed_summary())


if _enabled():
    _apply_seed()
    _emit("sitecustomize_loaded", seed_summary=_seed_summary())
    _patch_peft()
