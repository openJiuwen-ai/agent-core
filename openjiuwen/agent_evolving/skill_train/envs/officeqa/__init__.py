# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""officeqa environment package."""

__all__ = ["OfficeQAAdapter"]


def __getattr__(name: str):
    if name == "OfficeQAAdapter":
        from openjiuwen.agent_evolving.skill_train.envs.officeqa.adapter import OfficeQAAdapter

        return OfficeQAAdapter
    raise AttributeError(name)
