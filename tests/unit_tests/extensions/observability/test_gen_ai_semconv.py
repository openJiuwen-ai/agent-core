# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Contracts for the replaceable GenAI semantic-convention module."""

from __future__ import annotations

import ast
import inspect

from openjiuwen.extensions.observability import gen_ai_semconv, semconv


def _standard_attributes(module: object) -> dict[str, str]:
    return {
        name: value
        for name, value in vars(module).items()
        if name.startswith("GEN_AI_") and isinstance(value, str) and value.startswith("gen_ai.")
    }


def test_generated_gen_ai_attributes_are_unique_and_reexported() -> None:
    generated = _standard_attributes(gen_ai_semconv)

    assert len(generated) == gen_ai_semconv.GEN_AI_SEMCONV_ATTRIBUTE_COUNT
    assert len(set(generated.values())) == len(generated)
    assert all(getattr(semconv, name) == value for name, value in generated.items())


def test_project_semconv_does_not_redefine_standard_gen_ai_attributes() -> None:
    tree = ast.parse(inspect.getsource(semconv))
    assigned_strings = {
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }

    assert not {value for value in assigned_strings if value.startswith("gen_ai.")}
