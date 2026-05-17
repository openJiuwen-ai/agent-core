# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for evolution signal base helpers."""

from openjiuwen.agent_evolving.signal.base import (
    EvolutionSignal,
    make_evolution_signal,
    make_signal_fingerprint,
)


def test_to_dict_uses_only_stable_top_level_fields() -> None:
    signal = EvolutionSignal(
        signal_type="execution_failure",
        section="Troubleshooting",
        excerpt="tool timeout",
        skill_name="skill-a",
        context={"source": "passive_conversation", "tool_name": "bash"},
    )

    assert signal.to_dict() == {
        "type": "execution_failure",
        "section": "Troubleshooting",
        "excerpt": "tool timeout",
        "skill_name": "skill-a",
        "context": {"source": "passive_conversation", "tool_name": "bash"},
    }


def test_make_evolution_signal_moves_tool_name_into_context() -> None:
    signal = make_evolution_signal(
        signal_type="execution_failure",
        section="Troubleshooting",
        excerpt="tool timeout",
        skill_name="skill-a",
        tool_name="bash",
        source="passive_conversation",
    )

    assert signal.context == {
        "source": "passive_conversation",
        "tool_name": "bash",
    }


def test_make_signal_fingerprint_reads_tool_name_from_context() -> None:
    signal = EvolutionSignal(
        signal_type="execution_failure",
        section="Troubleshooting",
        excerpt="tool timeout",
        skill_name="skill-a",
        context={"tool_name": "bash"},
    )

    assert make_signal_fingerprint(signal) == (
        "execution_failure",
        "bash",
        "skill-a",
        "tool timeout",
    )
