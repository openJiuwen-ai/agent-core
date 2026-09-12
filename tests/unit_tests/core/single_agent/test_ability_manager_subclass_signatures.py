# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Signature compatibility between AbilityManager and its subclasses.

AbilityManager invokes its own methods on ``self``, so an override whose
parameter list has drifted from the base turns every such call into a
``TypeError`` at run time rather than at import time.  Nothing in the type
system or the test suite catches that today, and it has now happened twice in
the same subclass: once for ``execute`` and once for
``_execute_single_tool_call``.

These tests pin the invariant for every subclass and every overridden method,
so the next override that drifts fails here instead of in production.
"""
from __future__ import annotations

import importlib
import inspect

import pytest

from openjiuwen.core.single_agent.ability_manager import AbilityManager

# A subclass is only reachable through ``__subclasses__()`` once the module
# defining it has been imported, so every module holding one is imported here
# for that side effect. A subclass missing from this list is a subclass this
# guard cannot see, which is what ``test_known_subclasses_are_discovered``
# exists to catch.
_SUBCLASS_MODULES = (
    "openjiuwen.core.context_engine.processor.forked.compressor.session_memory_agent",
    "openjiuwen.core.multi_agent.teams.hierarchical_msgbus.p2p_ability_manager",
)
for _module_name in _SUBCLASS_MODULES:
    importlib.import_module(_module_name)


def _all_subclasses(cls) -> list[type]:
    """Every subclass of ``cls``, transitively."""
    found: list[type] = []
    for sub in cls.__subclasses__():
        found.append(sub)
        found.extend(_all_subclasses(sub))
    return found


def _overridden_methods(sub: type):
    """Yield ``(name, base_function, override_function)`` for each override."""
    # Dunder methods are excluded: __init__ in particular is not called
    # polymorphically by the base class, and subclasses legitimately take
    # different construction arguments (P2PAbilityManager requires a
    # supervisor). Every other method may be reached through ``self`` from
    # base-class code, so its signature has to stay call-compatible.
    for name, member in vars(sub).items():
        if name.startswith("__") or not inspect.isfunction(member):
            continue
        base_member = getattr(AbilityManager, name, None)
        if inspect.isfunction(base_member):
            yield name, base_member, member


def _override_cases():
    cases = []
    for sub in _all_subclasses(AbilityManager):
        for name, base_member, override in _overridden_methods(sub):
            cases.append(
                pytest.param(
                    base_member, override, id=f"{sub.__name__}.{name}"
                )
            )
    return cases


class TestAbilityManagerSubclassSignatures:
    """Every AbilityManager override stays callable the way the base calls it."""

    @staticmethod
    def test_known_subclasses_are_discovered():
        """The guard is only meaningful if it actually sees the subclasses."""
        names = {sub.__name__ for sub in _all_subclasses(AbilityManager)}
        assert {"P2PAbilityManager", "SessionMemoryAbilityManager"} <= names

    @staticmethod
    @pytest.mark.parametrize("base_member,override", _override_cases())
    def test_override_accepts_every_base_parameter(base_member, override):
        """An override must accept every parameter name the base declares."""
        over_sig = inspect.signature(override)
        if any(
            param.kind is inspect.Parameter.VAR_KEYWORD
            for param in over_sig.parameters.values()
        ):
            pytest.skip("override accepts **kwargs, so every keyword binds")
        base_sig = inspect.signature(base_member)
        missing = [
            name for name in base_sig.parameters if name not in over_sig.parameters
        ]
        assert not missing, (
            f"{override.__qualname__} does not accept {missing}, "
            f"which {base_member.__qualname__} declares. "
            f"base={base_sig} override={over_sig}"
        )

    @staticmethod
    @pytest.mark.parametrize("base_member,override", _override_cases())
    def test_override_takes_no_positional_only_parameters(base_member, override):
        """Base-class code calls these by keyword, so none may be positional-only."""
        positional_only = [
            name
            for name, param in inspect.signature(override).parameters.items()
            if param.kind is inspect.Parameter.POSITIONAL_ONLY
        ]
        assert not positional_only, (
            f"{override.__qualname__} makes {positional_only} positional-only; "
            f"{base_member.__qualname__} is called with keyword arguments"
        )

    @staticmethod
    def test_railed_call_binds_on_every_single_tool_call_override():
        """Pin the exact keyword set _railed_execute_single_tool_call passes."""
        # This is the call that broke: the base passes ``callback_context=``
        # and an override predating that parameter cannot bind it.
        for sub in _all_subclasses(AbilityManager):
            override = vars(sub).get("_execute_single_tool_call")
            if override is None:
                continue
            inspect.signature(override).bind(
                None,  # self
                tool_call=None,
                session=None,
                tag=None,
                callback_context=None,
            )
