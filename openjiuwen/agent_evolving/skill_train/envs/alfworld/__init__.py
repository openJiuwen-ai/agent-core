# coding: utf-8
"""alfworld environment package."""

__all__ = ["ALFWorldAdapter"]


def __getattr__(name: str):
    if name == "ALFWorldAdapter":
        from openjiuwen.agent_evolving.skill_train.envs.alfworld.adapter import ALFWorldAdapter

        return ALFWorldAdapter
    raise AttributeError(name)
