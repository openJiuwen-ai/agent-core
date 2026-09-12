# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SearchQA environment package."""

__all__ = ["SearchQAAdapter"]


def __getattr__(name: str):
    if name == "SearchQAAdapter":
        from openjiuwen.agent_evolving.skill_train.envs.searchqa.adapter import SearchQAAdapter

        return SearchQAAdapter
    raise AttributeError(name)
