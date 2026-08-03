# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for TaskTool subagent thinking hook registry."""

from __future__ import annotations

from types import SimpleNamespace

from openjiuwen.harness.tools.subagent import thinking_hook as th


def test_apply_subagent_thinking_noop_without_hook():
    th.register_subagent_thinking_hook(None)
    th.apply_subagent_thinking(SimpleNamespace(), thinking="off", model=None)


def test_apply_subagent_thinking_invokes_hook():
    calls: list[tuple] = []

    def _hook(subagent, *, thinking, model):
        calls.append((subagent, thinking, model))

    th.register_subagent_thinking_hook(_hook)
    try:
        agent = SimpleNamespace(name="child")
        th.apply_subagent_thinking(agent, thinking="on", model="m1")
        assert calls == [(agent, "on", "m1")]
    finally:
        th.register_subagent_thinking_hook(None)


def test_apply_subagent_thinking_swallows_hook_errors():
    def _boom(subagent, *, thinking, model):
        raise RuntimeError("boom")

    th.register_subagent_thinking_hook(_boom)
    try:
        th.apply_subagent_thinking(SimpleNamespace(), thinking="off")
    finally:
        th.register_subagent_thinking_hook(None)
