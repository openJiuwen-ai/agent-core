# coding: utf-8
"""Tests for shared trajectory JSON conversion."""

from dataclasses import dataclass

from openjiuwen.agent_evolving.trajectory.serialization import to_json_compatible


@dataclass
class _Record:
    value: tuple[int, int]


class _Model:
    def model_dump(self):
        return {"record": _Record((1, 2))}


def test_to_json_compatible_normalizes_nested_supported_values() -> None:
    source = {"model": _Model(), "values": (3, 4)}

    converted = to_json_compatible(source)

    assert converted == {"model": {"record": {"value": [1, 2]}}, "values": [3, 4]}
    assert converted is not source


def test_to_json_compatible_logs_and_falls_back_after_failed_conversion_method(caplog) -> None:
    class BrokenModel:
        def model_dump(self):
            raise ValueError("broken")

        def __str__(self):
            return "fallback"

    assert to_json_compatible(BrokenModel()) == "fallback"
    assert "model_dump" in caplog.text
