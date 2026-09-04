# coding: utf-8
"""docvqa environment package."""

__all__ = ["DocVQAAdapter"]


def __getattr__(name: str):
    if name == "DocVQAAdapter":
        from openjiuwen.agent_evolving.skill_train.envs.docvqa.adapter import DocVQAAdapter

        return DocVQAAdapter
    raise AttributeError(name)
