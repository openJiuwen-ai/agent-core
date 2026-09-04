# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Codex exec stubs — not supported in Phase 1."""

from __future__ import annotations


def is_target_exec_backend() -> bool:
    return False


def prepare_workspace(**kwargs):  # noqa: ANN003
    del kwargs
    raise NotImplementedError("Codex exec backend is not supported in agent-core skill_train Phase 1")


def render_skill_md(skill_content: str, **kwargs) -> str:  # noqa: ANN003
    del kwargs
    return skill_content


def run_target_exec(**kwargs):  # noqa: ANN003
    del kwargs
    raise NotImplementedError("Codex exec backend is not supported in agent-core skill_train Phase 1")
