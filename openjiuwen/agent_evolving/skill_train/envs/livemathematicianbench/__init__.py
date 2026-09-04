# coding: utf-8
"""livemathematicianbench environment package."""

__all__ = ["LiveMathematicianBenchAdapter"]


def __getattr__(name: str):
    if name == "LiveMathematicianBenchAdapter":
        from openjiuwen.agent_evolving.skill_train.envs.livemathematicianbench.adapter import LiveMathematicianBenchAdapter

        return LiveMathematicianBenchAdapter
    raise AttributeError(name)
