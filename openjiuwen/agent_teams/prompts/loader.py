# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Markdown template loader for agent-team prompts.

Kept in its own module so ``sections`` can depend on the loader without
forcing the package ``__init__`` to be ready first.

``make_template_loader(ws_cache)`` is the factory that
binds a per-team ``WorkspaceCache`` into a loader closure with the **same
signature as ``load_template``** — consumers that know their team bind once
at construction and their rendering chain never threads a cache argument.
``ws_cache=None`` (unit tests / ``evolution_enabled=false`` / single agent)
returns the framework read-only loader itself, so every existing call site
and UT keeps working unchanged. The two handlers that resolve the cache
lazily from a ``TeamInfra`` (``MessageHandler`` / ``TeamScheduler``) share
``bind_template_loader(infra)`` so the pattern is written once.
"""

from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING, Callable

from openjiuwen.agent_teams.team_workspace.layout import WorkspaceLayout
from openjiuwen.core.foundation.prompt import PromptTemplate

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.infra import TeamInfra
    from openjiuwen.agent_teams.team_workspace.workspace_cache import WorkspaceCache

_DEFAULT_LANGUAGE = "cn"

#: ``(name, language) -> PromptTemplate`` — the shared loader contract.
#: ``load_template`` and every ``make_template_loader`` closure satisfy it.
TemplateLoader = Callable[[str, str], PromptTemplate]


@cache
def _load(name: str, language: str) -> PromptTemplate:
    """Load a markdown template from the ``<lang>/<name>.md`` file."""
    path = WorkspaceLayout.framework_prompt_file(name, language)
    return PromptTemplate(name=name, content=path.read_text(encoding="utf-8"))


def load_template(name: str, language: str = _DEFAULT_LANGUAGE) -> PromptTemplate:
    """Load a language-specific template from ``<lang>/<name>.md``.

    Signature is the contract every team loader closure matches; without a
    team workspace this is the framework read-only loader (all existing
    callers / UTs stay unchanged).
    """
    return _load(name, language)


def make_template_loader(ws_cache: WorkspaceCache | None = None) -> TemplateLoader:
    """Bind a per-team ``WorkspaceCache`` into a ``load_template``-signed loader.

    The closure checks the workspace's evolved A-class values first and falls
    back to the framework ``@cache`` layer. The cache instance is captured —
    per-team isolation with no ContextVar and no global registry.

    Args:
        ws_cache: The team's resident ``WorkspaceCache`` (built at assembly,
            A/B/C classes). ``None`` → the framework read-only loader
            (equivalent to ``load_template`` itself).
    """
    if ws_cache is None:
        return load_template

    def load(name: str, language: str = _DEFAULT_LANGUAGE) -> PromptTemplate:
        evolved = ws_cache.get_template(name)
        if evolved is not None:
            return evolved
        return _load(name, language)

    return load


def bind_template_loader(infra: "TeamInfra") -> TemplateLoader:
    """Bind a per-team A-class loader from a ``TeamInfra`` (lazy: caller caches).

    The one place the "read the team's resident cache off the backend" pattern
    is expressed for the two handlers that resolve it lazily (``MessageHandler``,
    ``TeamScheduler``). ``infra.team_backend.workspace_cache`` delegates to the
    workspace manager the way every other consumer does; a ``None``
    backend (no team / pre-assembly) yields the framework read-only loader.

    Memoization is left to the caller (a ``@property`` + instance field) so
    the loader is bound once per member life, not once per property access.
    """
    backend = infra.team_backend
    ws_cache = backend.workspace_cache if backend is not None else None
    return make_template_loader(ws_cache)


__all__ = ["TemplateLoader", "bind_template_loader", "load_template", "make_template_loader"]
