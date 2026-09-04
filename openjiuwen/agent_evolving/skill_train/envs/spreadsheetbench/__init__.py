# coding: utf-8
"""spreadsheetbench environment package."""

__all__ = ["SpreadsheetBenchAdapter"]


def __getattr__(name: str):
    if name == "SpreadsheetBenchAdapter":
        from openjiuwen.agent_evolving.skill_train.envs.spreadsheetbench.adapter import SpreadsheetBenchAdapter

        return SpreadsheetBenchAdapter
    raise AttributeError(name)
