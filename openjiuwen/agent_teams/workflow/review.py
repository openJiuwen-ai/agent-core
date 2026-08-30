# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Business-side builder for SwarmFlow ``verify()`` (SDD-0017).

This is the counterpart to the business-agnostic ``workflow.engine.verify``: it
turns a *role*-level reviewer spec (``type`` / ``instruction``) plus a
deliverable into the engine-neutral ``Reviewer`` objects ``verify()`` runs. It
owns the two things the engine must not: which prompt template a reviewer type
uses, and how a deliverable (inline text or file paths) is presented to the
reviewer.

Mapping (role -> vote kind / tally pool):
    verifier / challenger -> ``verdict`` (binary pass/fail, one-vote veto)
    inspector            -> ``score``  (0-1, threshold on the average)

Reviewer prompts are rendered from the ``swarmflow_reviewer_*`` templates under
``prompts/{cn,en}/``, adapted from the scheduled-dispatch reviewer templates to
instruct the reviewer to submit its vote as **structured output** (rather than
the scheduled ``verify_task`` tool the SwarmFlow worker does not have).
"""
from __future__ import annotations

from typing import Sequence

from openjiuwen.agent_teams.prompts.loader import load_template
from openjiuwen.agent_teams.workflow.engine.errors import EngineError
from openjiuwen.agent_teams.workflow.engine.verify import Reviewer, ReviewerKind

#: reviewer ``type`` -> prompt template basename.
_TYPE_TEMPLATE: dict[str, str] = {
    "verifier": "swarmflow_reviewer_verifier",
    "inspector": "swarmflow_reviewer_inspector",
    "challenger": "swarmflow_reviewer_challenger",
}

#: reviewer ``type`` -> engine vote kind.
_TYPE_KIND: dict[str, ReviewerKind] = {
    "verifier": "verdict",
    "challenger": "verdict",
    "inspector": "score",
}

_DEFAULT_LANGUAGE = "cn"


def _render_deliverable(deliverable: str | Sequence[str]) -> str:
    """Present a deliverable to a reviewer.

    A ``str`` is treated as inline content; a sequence of strings as file paths
    (rendered as a list so the reviewer reads them with its file tools).
    """
    if isinstance(deliverable, str):
        return deliverable
    return "\n".join(f"- {path}" for path in deliverable)


def build_reviewers(
    deliverable: str | Sequence[str],
    specs: Sequence[dict],
    *,
    acceptance: str | None = None,
    language: str = _DEFAULT_LANGUAGE,
) -> list[Reviewer]:
    """Build ``Reviewer`` objects for ``verify()`` from role-level specs.

    Args:
        deliverable: The object to verify — inline text (``str``) or a list of
            file paths (``Sequence[str]``).
        specs: List of ``{"type", "instruction"?, "label"?}``. ``type`` is one of
            ``verifier`` / ``inspector`` / ``challenger``. An ``inspector``
            without ``instruction`` falls back to the default 6-dimension rubric
            (``reviewer_dims_for_inspector``).
        acceptance: Optional acceptance criteria / requirements presented to every
            reviewer.
        language: Prompt language (``cn`` / ``en``).

    Returns:
        The engine-neutral ``Reviewer`` list, ready to pass to ``verify()``.
    """
    deliverable_text = _render_deliverable(deliverable)
    reviewers: list[Reviewer] = []
    for i, spec in enumerate(specs):
        rtype = spec.get("type") or "verifier"
        if rtype not in _TYPE_TEMPLATE:
            raise ValueError(f"unknown reviewer type {rtype!r}; expected one of {sorted(_TYPE_TEMPLATE)}")
        template = _TYPE_TEMPLATE[rtype]
        instruction = spec.get("instruction")
        if rtype == "inspector" and not instruction:
            instruction = load_template("reviewer_dims_for_inspector", language).content
        label = spec.get("label") or f"{rtype}-{i}"
        body = load_template(template, language).content
        if not isinstance(body, str):
            raise EngineError(f"swarmflow reviewer template {template!r} resolved to non-str content")
        prompt = body.format(
            reviewer=label,
            instruction=instruction or "",
            deliverable=deliverable_text,
            acceptance=acceptance or "",
        )
        reviewers.append(Reviewer(kind=_TYPE_KIND[rtype], prompt=prompt, label=label))
    return reviewers


__all__ = ["build_reviewers"]
