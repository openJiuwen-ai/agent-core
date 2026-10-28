# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Regression tests for fields shared by BuildContext consumers."""

from openjiuwen.harness.schema.build_context import BuildContext


def test_build_context_keeps_moa_and_invocation_owner_together():
    moa = object()
    context = BuildContext(moa=moa, tool_owner_id="invocation-owner")

    assert context.moa is moa
    assert context.tool_owner_id == "invocation-owner"

    derived = context.derive(member_name="worker")
    assert derived.moa is moa
    assert derived.tool_owner_id == "invocation-owner"
