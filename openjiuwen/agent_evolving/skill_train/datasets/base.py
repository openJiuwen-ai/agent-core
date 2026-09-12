# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Batch planning utilities for skill_train dataset environments.

Trainers consume :class:`BatchSpec` objects. Loaders describe which task
items to run each step and may carry a concrete payload for dataset-backed
benchmarks.

``SplitDataLoader`` supports:

* ``split_mode="split_dir"`` ? read ``train/`` ``val/`` ``test/`` under ``split_dir``
* ``split_mode="ratio"`` ? shuffle ``data_path`` and materialize a split tree
"""

from __future__ import annotations

import json
import os
import random
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

from openjiuwen.core.common.logging import logger

PARTITION_LABELS: tuple[str, str, str] = ("train", "val", "test")

_ALIAS_TO_PARTITION: dict[str, str] = {
    "train": "train",
    "valid_seen": "val",
    "selection": "val",
    "val": "val",
    "valid_unseen": "test",
    "test": "test",
}

_RATIO_TOKEN = re.compile(r"^\d+$")


class RatioWeights(NamedTuple):
    """Positive integer weights for train / val / test."""

    train: int
    val: int
    test: int


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def _records_from_json_text(text: str) -> list[dict] | None:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        nested = parsed.get("data")
        return nested if isinstance(nested, list) else list(parsed.values())
    return None


def _records_from_jsonl_text(text: str) -> list[dict]:
    rows: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def load_item_records(path: str | Path) -> list[dict]:
    """Load task dicts from a JSON array file or JSONL stream."""
    text = _read_text(Path(path))
    if not text:
        return []
    whole = _records_from_json_text(text)
    return whole if whole is not None else _records_from_jsonl_text(text)


def parse_ratio_weights(ratio_text: str) -> tuple[int, int, int]:
    """Parse ``train:val:test`` into three positive integers."""
    cleaned = str(ratio_text or "").replace(" ", "")
    chunks = cleaned.split(":")
    if len(chunks) != 3 or not all(_RATIO_TOKEN.match(chunk) for chunk in chunks):
        raise ValueError(
            f"expected three colon-separated positive integers for split_ratio, got {ratio_text!r}"
        )
    weights = RatioWeights(*(int(chunk) for chunk in chunks))
    if min(weights) < 1:
        raise ValueError(f"each split_ratio weight must be >= 1, got {ratio_text!r}")
    return weights.train, weights.val, weights.test


def partition_counts(total: int, weights: tuple[int, int, int]) -> tuple[int, int, int]:
    """Largest-remainder allocation of *total* across *weights*."""
    denom = sum(weights)
    quotas = [total * weight / denom for weight in weights]
    assigned = [int(quota) for quota in quotas]
    leftover = total - sum(assigned)
    if leftover:
        ranked = sorted(
            range(len(weights)),
            key=lambda i: (quotas[i] - assigned[i], weights[i]),
            reverse=True,
        )
        for i in ranked[:leftover]:
            assigned[i] += 1
    return assigned[0], assigned[1], assigned[2]


@dataclass(slots=True)
class BatchSpec:
    """One training or evaluation step as seen by the skill_train loop."""

    phase: str
    split: str
    seed: int
    batch_size: int
    payload: object | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def _seed_ladder(start: int, count: int) -> list[int]:
    """Return ``count`` consecutive seeds beginning at ``start + 1``."""
    if count <= 0:
        return []
    origin = int(start)
    return [origin + step for step in range(1, count + 1)]


def _permute_seeds(seeds: list[int], epoch: int, seed: int) -> list[int]:
    rng = random.Random(int(seed) + 1000 * int(epoch))
    ordered = list(seeds)
    rng.shuffle(ordered)
    return ordered


def _make_batch_spec(
    *,
    phase: str,
    split: str,
    seed: int,
    rows: list[dict],
) -> BatchSpec:
    return BatchSpec(
        phase=phase,
        split=split,
        seed=seed,
        batch_size=len(rows),
        payload=rows,
    )


class BaseDataLoader(ABC):
    """Turn trainer hyper-parameters into :class:`BatchSpec` sequences."""

    def setup(self, cfg: dict) -> None:
        """One-time initialization from trainer configuration."""

    def set_out_root(self, out_root: str) -> None:
        """Optional hook for loaders that write under the run output tree."""

    def state_dict(self) -> dict[str, Any]:
        """Checkpoint hook; default loaders are stateless."""
        return {}

    def load_state_dict(self, state: dict[str, Any]) -> None:
        """Restore from :meth:`state_dict` (no-op by default)."""
        del state

    def get_train_size(self) -> int | None:
        """Size of the train pool when the loader knows it."""
        return None

    @abstractmethod
    def build_train_batch(self, batch_size: int, seed: int, **kwargs) -> BatchSpec:
        """Construct one training :class:`BatchSpec`."""

    @abstractmethod
    def build_eval_batch(self, env_num: int, split: str, seed: int, **kwargs) -> BatchSpec:
        """Construct one evaluation :class:`BatchSpec`."""

    @staticmethod
    def make_base_seeds(steps_per_epoch: int, accumulation: int, seed: int) -> list[int]:
        """Deterministic seed pool covering one epoch of micro-batches."""
        return _seed_ladder(seed, max(0, int(steps_per_epoch) * int(accumulation)))

    @staticmethod
    def shuffle_epoch_seeds(base_seeds: list[int], epoch: int, seed: int) -> list[int]:
        """Epoch-scoped permutation of *base_seeds*."""
        return _permute_seeds(base_seeds, epoch=epoch, seed=seed)

    def plan_train_epoch(
        self,
        *,
        epoch: int,
        steps_per_epoch: int,
        accumulation: int,
        batch_size: int,
        seed: int,
        **kwargs,
    ) -> list[BatchSpec]:
        """Plan one epoch by building a batch per shuffled seed."""
        ladder = type(self).make_base_seeds(steps_per_epoch, accumulation, seed)
        order = type(self).shuffle_epoch_seeds(ladder, epoch=epoch, seed=seed)
        return [
            self.build_train_batch(batch_size=batch_size, seed=item, **kwargs) for item in order
        ]


@dataclass
class _LoaderPaths:
    """Mutable path / split knobs shared by :class:`SplitDataLoader`."""

    split_dir: str = ""
    data_path: str = ""
    split_mode: str = "ratio"
    split_ratio: str = "2:1:7"
    split_seed: int = 42
    split_output_dir: str = ""
    seed: int = 42
    limit: int = 0


class _RatioSplitMaterializer:
    """Build a train/val/test directory tree from a shuffled source corpus."""

    def __init__(self, loader: SplitDataLoader) -> None:
        self._loader = loader

    def destination(self, cfg: dict) -> Path:
        paths = self._loader.paths
        if paths.split_output_dir:
            return Path(paths.split_output_dir).resolve()
        run_root = Path(str(cfg.get("out_root") or os.getcwd())).resolve()
        stem = type(self._loader).__name__.replace("DataLoader", "").lower()
        env_tag = str(cfg.get("env") or stem)
        ratio_label = str(paths.split_ratio or "2:1:7").replace(":", "-")
        return run_root / "_generated_splits" / f"{env_tag}_{ratio_label}_seed{paths.split_seed}"

    def materialize(self, cfg: dict) -> str:
        loader = self._loader
        paths = loader.paths
        raw_source = str(paths.data_path or "").strip()
        if not raw_source:
            raise ValueError(f"{type(loader).__name__} requires data_path when split_mode=ratio.")
        source = Path(raw_source).resolve()

        weights = parse_ratio_weights(paths.split_ratio)
        records = loader.load_raw_items(str(source))
        if not records:
            raise ValueError(f"No raw items available for ratio split from {source}")

        shuffled = list(records)
        random.Random(paths.split_seed).shuffle(shuffled)
        n_train, n_val, n_test = partition_counts(len(shuffled), weights)
        val_start = n_train
        test_start = n_train + n_val
        test_end = test_start + n_test
        slices = {
            "train": shuffled[:n_train],
            "val": shuffled[val_start:test_start],
            "test": shuffled[test_start:test_end],
        }

        root = self.destination(cfg)
        root.mkdir(parents=True, exist_ok=True)
        for name, items in slices.items():
            loader.write_split_items(str(root / name), items)

        manifest = {
            "source_data_path": str(source),
            "split_mode": "ratio",
            "split_ratio": paths.split_ratio,
            "split_seed": paths.split_seed,
            "counts": {name: len(items) for name, items in slices.items()},
        }
        (root / "split_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(
            "[%s] generated ratio split %s at %s from %s",
            type(loader).__name__,
            paths.split_ratio,
            root,
            source,
        )
        return str(root)


class SplitDataLoader(BaseDataLoader):
    """Loader for benchmarks stored as train/val/test item collections."""

    def __init__(
        self,
        *,
        split_dir: str = "",
        data_path: str = "",
        split_mode: str = "ratio",
        split_ratio: str = "2:1:7",
        split_seed: int = 42,
        split_output_dir: str = "",
        seed: int = 42,
        limit: int = 0,
        **_unused: Any,
    ) -> None:
        del _unused
        self.paths = _LoaderPaths(
            split_dir=str(split_dir or ""),
            data_path=str(data_path or ""),
            split_mode=str(split_mode or "ratio"),
            split_ratio=str(split_ratio or "2:1:7"),
            split_seed=int(split_seed),
            split_output_dir=str(split_output_dir or ""),
            seed=int(seed),
            limit=int(limit or 0),
        )
        self._cache: dict[str, list[dict]] = {}

    # Attribute mirrors kept for callers that read ``loader.split_dir`` etc.
    @property
    def split_dir(self) -> str:
        return self.paths.split_dir

    @split_dir.setter
    def split_dir(self, value: str) -> None:
        self.paths.split_dir = value

    @property
    def data_path(self) -> str:
        return self.paths.data_path

    @data_path.setter
    def data_path(self, value: str) -> None:
        self.paths.data_path = value

    @property
    def split_mode(self) -> str:
        return self.paths.split_mode

    @split_mode.setter
    def split_mode(self, value: str) -> None:
        self.paths.split_mode = value

    @property
    def split_ratio(self) -> str:
        return self.paths.split_ratio

    @split_ratio.setter
    def split_ratio(self, value: str) -> None:
        self.paths.split_ratio = value

    @property
    def split_seed(self) -> int:
        return self.paths.split_seed

    @split_seed.setter
    def split_seed(self, value: int) -> None:
        self.paths.split_seed = int(value)

    @property
    def split_output_dir(self) -> str:
        return self.paths.split_output_dir

    @split_output_dir.setter
    def split_output_dir(self, value: str) -> None:
        self.paths.split_output_dir = value

    @property
    def seed(self) -> int:
        return self.paths.seed

    @seed.setter
    def seed(self, value: int) -> None:
        self.paths.seed = int(value)

    @property
    def limit(self) -> int:
        return self.paths.limit

    @limit.setter
    def limit(self, value: int) -> None:
        self.paths.limit = int(value)

    def _bucket(self, name: str) -> list[dict]:
        return self._cache.get(name, [])

    def __getattr__(self, name: str) -> Any:
        # Expose train/val/test pools without a contiguous property block.
        mapping = {"train_items": "train", "val_items": "val", "test_items": "test"}
        key = mapping.get(name)
        if key is not None:
            return self._bucket(key)
        raise AttributeError(f"{type(self).__name__!r} has no attribute {name!r}")

    def get_split_items(self, split: str) -> list[dict]:
        canonical = _ALIAS_TO_PARTITION.get(split, split)
        fallback = self._bucket("val")
        return list(self._cache.get(canonical, fallback))

    def get_train_size(self) -> int:
        return len(self._bucket("train"))

    def setup(self, cfg: dict) -> None:
        self._absorb_cfg(cfg)
        mode = str(self.paths.split_mode or "ratio").strip().lower()
        allowed = {"ratio", "split_dir"}
        if mode not in allowed:
            raise ValueError(
                f"{type(self).__name__} split_mode must be one of {sorted(allowed)}, "
                f"got {self.paths.split_mode!r}"
            )
        self.paths.split_mode = mode
        if mode == "ratio":
            self.paths.split_dir = _RatioSplitMaterializer(self).materialize(cfg)
        elif not self.paths.split_dir:
            expected = "/".join(PARTITION_LABELS)
            raise ValueError(
                f"{type(self).__name__} requires `data_path` for ratio mode, "
                f"or `split_dir` with {expected}/ for split_dir mode."
            )
        if not self.paths.split_dir:
            raise ValueError(f"{type(self).__name__} resolved an empty split_dir.")
        self._reload_cache()

    def _absorb_cfg(self, cfg: dict) -> None:
        paths = self.paths
        if not paths.split_mode:
            paths.split_mode = str(cfg.get("split_mode", "ratio") or "ratio")
        if not paths.split_dir:
            paths.split_dir = str(cfg.get("split_dir", "") or "")
        if not paths.data_path:
            paths.data_path = str(cfg.get("data_path", "") or "")
        if not paths.split_output_dir:
            paths.split_output_dir = str(cfg.get("split_output_dir", "") or "")
        if not paths.split_ratio:
            paths.split_ratio = str(cfg.get("split_ratio", "2:1:7") or "2:1:7")
        if "split_seed" in cfg and not paths.split_seed:
            paths.split_seed = int(cfg.get("split_seed", 0) or 0)
        if not paths.split_seed:
            paths.split_seed = paths.seed

    def load_raw_items(self, data_path: str) -> list[dict]:
        location = Path(data_path)
        if location.is_file():
            return load_item_records(location)
        if not location.is_dir():
            return load_item_records(location)
        has_partition_dirs = any((location / name).is_dir() for name in PARTITION_LABELS)
        if has_partition_dirs:
            raise ValueError(
                f"{type(self).__name__} received a completed split tree as data_path; "
                "set split_mode=split_dir and pass that tree via split_dir."
            )
        json_files = sorted(location.glob("*.json"))
        jsonl_files = sorted(location.glob("*.jsonl"))
        candidates = [*json_files, *jsonl_files]
        if len(candidates) != 1:
            raise ValueError(
                f"{type(self).__name__} needs data_path as a single JSON/JSONL file "
                f"or a directory with exactly one such file; got {data_path!r}"
            )
        return load_item_records(candidates[0])

    def write_split_items(self, split_path: str, items: list[dict]) -> None:
        folder = Path(split_path)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "items.json"
        target.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_split_items(self, split_path: str) -> list[dict]:
        json_paths = sorted(Path(split_path).glob("*.json"))
        if not json_paths:
            raise FileNotFoundError(f"No .json file found in {split_path}")
        payload = json.loads(json_paths[0].read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError(
                f"Expected JSON array in {json_paths[0]}, got {type(payload).__name__}"
            )
        return payload

    def build_train_batch(self, batch_size: int, seed: int, **kwargs) -> BatchSpec:
        del kwargs
        deck = list(self._bucket("train"))
        random.Random(seed).shuffle(deck)
        chosen = deck[: max(0, int(batch_size))]
        return _make_batch_spec(phase="train", split="train", seed=seed, rows=chosen)

    def build_eval_batch(self, env_num: int, split: str, seed: int, **kwargs) -> BatchSpec:
        del kwargs
        items = self.get_split_items(split)
        limit = int(env_num or 0)
        if limit and limit < len(items):
            items = items[:limit]
        return _make_batch_spec(phase="eval", split=split, seed=seed, rows=items)

    def plan_train_epoch(  # pylint: disable=too-many-locals
        self,
        *,
        epoch: int,
        steps_per_epoch: int,
        accumulation: int,
        batch_size: int,
        seed: int,
        **kwargs,
    ) -> list[BatchSpec]:
        del kwargs
        epoch_seed = int(seed) + int(epoch) * 1000
        deck = list(self._bucket("train"))
        random.Random(epoch_seed).shuffle(deck)

        slots = int(steps_per_epoch) * int(accumulation)
        if slots <= 0:
            return []

        specs: list[BatchSpec] = []
        cursor = 0
        size = max(0, int(batch_size))
        for slot in range(slots):
            end = cursor + size
            chunk = deck[cursor:end]
            cursor = end
            if not chunk and deck:
                refill = list(deck)
                random.Random(epoch_seed + slot + 1).shuffle(refill)
                chunk = refill[:size]
            specs.append(
                _make_batch_spec(
                    phase="train",
                    split="train",
                    seed=epoch_seed + slot + 1,
                    rows=chunk,
                )
            )
        return specs

    def _reload_cache(self) -> None:
        root = Path(self.paths.split_dir)
        for name in PARTITION_LABELS:
            folder = root / name
            if not folder.is_dir():
                raise ValueError(
                    f"Missing '{name}/' subdirectory in split_dir: {self.paths.split_dir}"
                )
            loaded = self.load_split_items(str(folder))
            if self.paths.limit:
                loaded = loaded[: self.paths.limit]
            self._cache[name] = loaded
        summary = " ".join(f"{name}={len(items)}" for name, items in self._cache.items())
        logger.info("[%s] %s  (from %s)", type(self).__name__, summary, self.paths.split_dir)


# Back-compat aliases used by older call sites / docs.
SPLIT_NAMES = PARTITION_LABELS
_CANONICAL_SPLIT = _ALIAS_TO_PARTITION
_decode_json_payload = load_item_records
_coerce_ratio_triplet = parse_ratio_weights
_allocate_partitions_by_ratio = partition_counts
