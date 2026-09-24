# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Induce FACT/TIP rules from a task trajectory.

Ports the reference ``induce.py`` (and, in later slices, ``blame.py`` /
``synthesize.py``). The parse logic and prompt assembly are verbatim; only the
LLM call swaps ``glm_chat`` (sync HTTP) for jiuwen's async
:func:`invoke_text_with_retry` (built-in retry + budget).
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from openjiuwen.agent_evolving.optimizer.llm_resilience import (
    LLMInvokePolicy,
    invoke_text_with_retry,
)
from openjiuwen.core.foundation.llm.model import Model

from .induce_context import BANK_SECTION_MAX_CHARS, format_bank_section
from .prompts import (
    BLAME_SYSTEM,
    SYNTH_SYSTEM,
    _OUTCOME_LBL,
    ExistingBank,
    InduceTaskEvidence,
    blame_prompt,
    induce_batch_prompt,
    induce_prompt,
    synthesize_prompt,
)


def parse_rules(text: str) -> Tuple[List[str], List[str]]:
    """Parse [FACT]/[TIP] lines from LLM output. Returns (facts, tips)."""
    facts: List[str] = []
    tips: List[str] = []
    if not text or text.strip().upper() == "NONE":
        return facts, tips
    for line in str(text).splitlines():
        s = line.strip()
        if not s:
            continue
        up = s.upper()
        if up.startswith("[FACT]"):
            body = s[6:].strip(" :")
            if body and body.upper() != "NONE":
                facts.append(body)
        elif up.startswith("[TIP]"):
            body = s[5:].strip(" :")
            if body and body.upper() != "NONE":
                tips.append(body)
    return facts, tips


async def induce(
    *,
    llm: Model,
    model: str,
    policy: LLMInvokePolicy,
    task_prompt: str,
    conversation_snippet: str,
    tool_call_chain: str,
    capabilities: str,
    existing_facts: List[str],
    existing_tips: List[str],
    outcome: str = "success",
    grader_note: str = "",
    max_tokens: Optional[int] = None,
) -> Tuple[List[str], List[str]]:
    """Extract new FACT/TIP rules from structured task evidence. Returns (facts, tips)."""
    bank = ExistingBank(
        facts=format_bank_section(existing_facts, max_chars=BANK_SECTION_MAX_CHARS),
        tips=format_bank_section(existing_tips, max_chars=BANK_SECTION_MAX_CHARS),
    )
    evidence = InduceTaskEvidence(
        task_query=task_prompt or "",
        conversation_snippet=conversation_snippet or "",
        tool_call_chain=tool_call_chain or "",
        grader_note=grader_note or "",
    )
    user = induce_prompt(evidence, capabilities, bank, outcome)
    kwargs = {}
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    out = await invoke_text_with_retry(llm, model, user, policy=policy, temperature=0.3, **kwargs)
    return parse_rules(out)


async def induce_batch(
    *,
    llm: Model,
    model: str,
    policy: LLMInvokePolicy,
    group: List[dict],
    capabilities: str,
    existing_facts: List[str],
    existing_tips: List[str],
    max_tokens: Optional[int] = None,
) -> Tuple[List[str], List[str]]:
    """One LLM call to extract rules from a WHOLE batch of task trajectories.

    Cost amortization: N tasks induce via a single LLM call instead of N.
    ``group`` is a list of per-task observations, each a dict with keys
    ``task_id`` / ``task_prompt`` / ``conversation_snippet`` / ``tool_call_chain``
    / ``outcome`` (optional ``grader_note``). Existing-bank text is capped at
    :data:`BANK_SECTION_MAX_CHARS` per track. Returns (facts, tips).
    """
    prepared = []
    for item in group:
        outcome = item.get("outcome", "success")
        lbl = _OUTCOME_LBL.get(outcome, _OUTCOME_LBL["success"])
        evidence = InduceTaskEvidence(
            task_query=item.get("task_prompt", "") or "",
            conversation_snippet=item.get("conversation_snippet", "") or "",
            tool_call_chain=item.get("tool_call_chain", "") or "",
            grader_note=item.get("grader_note", "") or "",
        )
        prepared.append((item.get("task_id", ""), evidence, lbl))
    user = induce_batch_prompt(
        prepared,
        capabilities,
        format_bank_section(existing_facts, max_chars=BANK_SECTION_MAX_CHARS),
        format_bank_section(existing_tips, max_chars=BANK_SECTION_MAX_CHARS),
    )
    out = await invoke_text_with_retry(llm, model, user, policy=policy, temperature=0.3, max_tokens=max_tokens)
    return parse_rules(out)


# ----------------------------------------------------------------------
# Blame (attribute a failure to one rule) + Synthesize (resolve a
# contradiction). Parse logic is verbatim from the reference blame.py /
# synthesize.py; only glm_chat -> invoke_text_with_retry changes.
# ----------------------------------------------------------------------


def parse_verdict(text: str, n: int) -> Optional[int]:
    """Parse a blame verdict line ``VERDICT: <num>|NONE`` -> 1-based index | None."""
    if not text:
        return None
    for line in str(text).splitlines():
        s = line.strip()
        if s.upper().startswith("VERDICT"):
            payload = s.split(":", 1)[-1].strip() if ":" in s else s
            if payload.upper() in ("NONE", ""):
                return None
            for tok in payload.replace(",", " ").split():
                if tok.isdigit():
                    v = int(tok)
                    return v if 1 <= v <= n else None
            return None
    return None


def parse_reason(text: str) -> str:
    if not text:
        return ""
    for line in str(text).splitlines():
        if line.strip().upper().startswith("REASON"):
            return line.split(":", 1)[-1].strip() if ":" in line else line.strip()
    return ""


def parse_synthesis(text: str) -> Optional[str]:
    """Parse a synthesized ``[TIP] ...`` line (or NONE) -> tip text | None."""
    if not text or text.strip().upper() == "NONE":
        return None
    for line in str(text).splitlines():
        s = line.strip()
        if s.upper().startswith("[TIP]"):
            body = s[5:].strip(" :")
            if body and body.upper() != "NONE":
                return body
    return None


async def blame(
    *,
    llm: Model,
    model: str,
    policy: LLMInvokePolicy,
    task_prompt: str,
    traj_text: str,
    rules_numbered: str,
    n_rules: int,
) -> Tuple[Optional[int], str]:
    """Attribute a failed task to at most one rule.

    ``rules_numbered`` is the pre-rendered numbered snapshot and ``n_rules``
    its length (for verdict validation). Returns (1-based index | None, reason).
    """
    if not rules_numbered:
        return None, "no active rules during this task"
    prompt = f"{BLAME_SYSTEM}\n\n{blame_prompt(task_prompt, traj_text, rules_numbered)}"
    out = await invoke_text_with_retry(llm, model, prompt, policy=policy, temperature=0.2)
    return parse_verdict(out, n_rules), (parse_reason(out) or "no reason given")


async def synthesize(
    *,
    llm: Model,
    model: str,
    policy: LLMInvokePolicy,
    rules_numbered: str,
    capabilities: str,
) -> Optional[str]:
    """Propose at most one resolving TIP for a contradiction/duplicate.

    Returns the synthesized TIP text, or None when there is nothing to resolve
    (or fewer than two rules are present). The caller is responsible for the
    ``len(flat) < 2`` short-circuit per the reference.
    """
    if not rules_numbered:
        return None
    prompt = f"{SYNTH_SYSTEM}\n\n{synthesize_prompt(rules_numbered, capabilities)}"
    out = await invoke_text_with_retry(llm, model, prompt, policy=policy, temperature=0.3)
    return parse_synthesis(out)


__all__ = [
    "parse_rules",
    "induce",
    "induce_batch",
    "blame",
    "synthesize",
    "parse_verdict",
    "parse_reason",
    "parse_synthesis",
]
