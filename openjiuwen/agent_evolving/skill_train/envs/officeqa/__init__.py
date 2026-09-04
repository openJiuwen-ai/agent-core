# coding: utf-8
"""officeqa environment package."""

__all__ = ["OfficeQAAdapter"]


def __getattr__(name: str):
    if name == "OfficeQAAdapter":
        from openjiuwen.agent_evolving.skill_train.envs.officeqa.adapter import OfficeQAAdapter

        return OfficeQAAdapter
    raise AttributeError(name)
