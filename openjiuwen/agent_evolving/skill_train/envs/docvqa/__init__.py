# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""docvqa environment package."""

__all__ = ["DocVQAAdapter"]


def __getattr__(name: str):
    if name == "DocVQAAdapter":
        from openjiuwen.agent_evolving.skill_train.envs.docvqa.adapter import DocVQAAdapter

        return DocVQAAdapter
    raise AttributeError(name)
