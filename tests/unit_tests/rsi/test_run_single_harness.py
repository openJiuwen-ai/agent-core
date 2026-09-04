"""Tests for the unified RSI dataset launcher."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from examples.rsi import run_single_harness


def test_unified_launcher_forwards_adapter_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[str] = []

    def target(argv: list[str] | None) -> int:
        received.extend(argv or [])
        return 7

    monkeypatch.setattr(
        run_single_harness.importlib,
        "import_module",
        lambda name: SimpleNamespace(main=target),
    )
    registry = {
        "sample": {
            "optimize": run_single_harness.DatasetAction("sample.module"),
        }
    }

    result = run_single_harness.main(
        ["sample", "optimize", "--run-name", "trial"],
        registry=registry,
    )

    assert result == 7
    assert received == ["--run-name", "trial"]


def test_unified_launcher_rejects_unknown_dataset() -> None:
    with pytest.raises(SystemExit, match="unknown dataset"):
        run_single_harness.main(["missing", "optimize"], registry={})


def test_unified_launcher_rejects_unknown_action() -> None:
    registry = {"sample": {"optimize": run_single_harness.DatasetAction("sample.module")}}
    with pytest.raises(SystemExit, match="unknown action"):
        run_single_harness.main(["sample", "evaluate"], registry=registry)
