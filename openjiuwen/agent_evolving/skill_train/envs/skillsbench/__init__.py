# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SkillsBench environment package."""

__all__ = ["SkillsBenchAdapter"]


def __getattr__(name: str):
    if name == "SkillsBenchAdapter":
        from openjiuwen.agent_evolving.skill_train.envs.skillsbench.adapter import SkillsBenchAdapter

        return SkillsBenchAdapter
    raise AttributeError(name)
