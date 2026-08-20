"""Unified entry point for RSI single-Harness dataset adapters.

Usage::

    python examples/rsi/run_single_harness.py <dataset> <action> [adapter options]

Dataset-specific execution stays in adapter modules. Adding another dataset
requires registering its callable here, not creating another launcher script.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib
from pathlib import Path
import sys
from typing import Callable, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@dataclass(frozen=True, slots=True)
class DatasetAction:
    """Lazy target for one dataset operation."""

    module: str
    function: str = "main"
    description: str = ""

    def load(self) -> Callable[[list[str] | None], int]:
        target = getattr(importlib.import_module(self.module), self.function)
        if not callable(target):
            raise TypeError(f"dataset target is not callable: {self.module}:{self.function}")
        return target


DATASET_ACTIONS: Mapping[str, Mapping[str, DatasetAction]] = {
    "evobench": {
        "optimize": DatasetAction(
            "examples.rsi.run_evobench_single_harness",
            description="Run RSI optimization on a prepared Evo-Bench validation run.",
        ),
        "evaluate": DatasetAction(
            "examples.rsi.evobench.domain_runner",
            description="Prepare or evaluate an exact General/Office release partition.",
        ),
        "official": DatasetAction(
            "examples.rsi.evobench.launcher",
            description="Run the unmodified official Evo-Bench protocol.",
        ),
    },
    "workbuddy-office": {
        "optimize": DatasetAction(
            "examples.rsi.run_workbuddy_office_single_harness",
            description="Run WorkBuddy Office baseline or RSI optimization.",
        ),
    },
}


def main(
    argv: list[str] | None = None,
    *,
    registry: Mapping[str, Mapping[str, DatasetAction]] = DATASET_ACTIONS,
) -> int:
    """Resolve a dataset/action pair and delegate all remaining arguments."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] in {"-h", "--help"}:
        _print_help(registry)
        return 0

    dataset = arguments.pop(0)
    actions = registry.get(dataset)
    if actions is None:
        raise SystemExit(f"unknown dataset {dataset!r}; choose from: {', '.join(sorted(registry))}")
    if not arguments or arguments[0] in {"-h", "--help"}:
        _print_dataset_help(dataset, actions)
        return 0

    action = arguments.pop(0)
    target = actions.get(action)
    if target is None:
        raise SystemExit(f"unknown action {action!r} for {dataset}; choose from: {', '.join(sorted(actions))}")
    return int(target.load()(arguments))


def _print_help(registry: Mapping[str, Mapping[str, DatasetAction]]) -> None:
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.print_help()
    print("\nDatasets and actions:")
    for dataset, actions in sorted(registry.items()):
        print(f"  {dataset}: {', '.join(sorted(actions))}")
    print("\nUse '<dataset> --help' to list its actions.")


def _print_dataset_help(dataset: str, actions: Mapping[str, DatasetAction]) -> None:
    print(f"{dataset} actions:")
    for name, target in sorted(actions.items()):
        print(f"  {name:<10} {target.description}")
    print(f"\nUse '{dataset} <action> --help' for adapter-specific options.")


if __name__ == "__main__":
    raise SystemExit(main())
