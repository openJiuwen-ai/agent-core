# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""OfficeQA rollout orchestration: corpus prep, scoring, batch fan-out."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from openjiuwen.agent_evolving.skill_train.envs.io_helpers import write_prediction_artifacts
from openjiuwen.agent_evolving.skill_train.envs.officeqa.dialogue_modes import (
    SEARCH_CUSTOM,
    SEARCH_OFFLINE,
    _DialogueOutcome,
    build_system_prompt,
    build_user_prompt,
    dispatch_dialogue,
    normalize_search_mode,
)
from openjiuwen.agent_evolving.skill_train.envs.officeqa.evaluator import evaluate
from openjiuwen.agent_evolving.skill_train.envs.officeqa.prompt_builders import extract_answer
from openjiuwen.agent_evolving.skill_train.envs.officeqa.tool_runtime import (
    build_oracle_parsed_pages_context,
    resolve_candidate_files,
    resolve_docs_roots,
)
from openjiuwen.agent_evolving.skill_train.envs.rollout_batch import run_parallel_rollout
from openjiuwen.agent_evolving.skill_train.jiuwenswarm_exec import (
    default_exec_prompt,
    exec_work_dir_for,
    prepare_workspace,
    render_skill_md,
    run_jiuwenswarm_cli_exec,
)
from openjiuwen.agent_evolving.skill_train.model_compat import get_target_backend, is_target_exec_backend

__all__ = ["OfficeQABatchKnobs", "OfficeQAItemKnobs", "_DialogueOutcome", "process_one", "run_batch"]

_PROCESS_ONE_OPTION_DEFAULTS: dict[str, Any] = {
    "max_tool_turns": 12,
    "max_completion_tokens": 16384,
    "exec_timeout": 600,
    "search_mode": SEARCH_OFFLINE,
    "max_queries_per_turn": 4,
    "search_api_url": "",
    "search_auth_env": "OFFICEQA_CUSTOM_SEARCH_AUTH",
    "search_provider": "duckduckgo",
    "search_max_num_results": 4,
    "search_timeout_seconds": 20,
    "use_local_tools": True,
    "data_dirs": None,
    "diagnostic_mode": False,
    "diagnostic_instruction": "",
}

_BATCH_OPTION_DEFAULTS: dict[str, Any] = {
    **_PROCESS_ONE_OPTION_DEFAULTS,
    "workers": 8,
    "task_timeout": 1800,
}

_OFFLINE_TOOLS_NOTE = (
    "Treat the excerpts below as primary document evidence; layer in local "
    "document-tool hits when they sharpen the answer."
)
_OFFLINE_PLAIN_NOTE = (
    "Treat the excerpts below as the authoritative document signal for the answer."
)


@dataclass(frozen=True)
class _RemoteLookupOptions:
    endpoint_url: str = ""
    credential_env: str = "OFFICEQA_CUSTOM_SEARCH_AUTH"
    backend: str = "duckduckgo"
    result_cap: int = 4
    deadline_seconds: int = 20


@dataclass(frozen=True)
class _DialogueLimits:
    tool_turn_cap: int = 12
    completion_token_cap: int = 16384
    queries_per_turn_cap: int = 4


@dataclass(frozen=True)
class _DiagnosticProbe:
    enabled: bool = False
    instruction: str = ""


@dataclass(frozen=True)
class OfficeQAItemKnobs:
    """Per-item rollout settings."""

    out_root: str
    skill_content: str
    limits: _DialogueLimits = field(default_factory=_DialogueLimits)
    lookup: _RemoteLookupOptions = field(default_factory=_RemoteLookupOptions)
    search_mode: str = SEARCH_OFFLINE
    use_local_tools: bool = True
    data_dirs: list[str] | str | None = None
    probe: _DiagnosticProbe = field(default_factory=_DiagnosticProbe)
    exec_timeout: int = 600

    @classmethod
    def from_flat(
        cls,
        out_root: str,
        skill_content: str,
        /,
        **flat: Any,
    ) -> OfficeQAItemKnobs:
        limits = _DialogueLimits(
            tool_turn_cap=int(flat.get("max_tool_turns", 12) or 12),
            completion_token_cap=int(flat.get("max_completion_tokens", 16384) or 16384),
            queries_per_turn_cap=int(flat.get("max_queries_per_turn", 4) or 4),
        )
        lookup = _RemoteLookupOptions(
            endpoint_url=str(flat.get("search_api_url") or ""),
            credential_env=str(flat.get("search_auth_env") or "OFFICEQA_CUSTOM_SEARCH_AUTH").strip(),
            backend=str(flat.get("search_provider") or "duckduckgo").strip(),
            result_cap=int(flat.get("search_max_num_results", 4) or 4),
            deadline_seconds=int(flat.get("search_timeout_seconds", 20) or 20),
        )
        probe = _DiagnosticProbe(
            enabled=bool(flat.get("diagnostic_mode", False)),
            instruction=str(flat.get("diagnostic_instruction") or ""),
        )
        return cls(
            out_root=out_root,
            skill_content=skill_content,
            limits=limits,
            lookup=lookup,
            search_mode=str(flat.get("search_mode") or SEARCH_OFFLINE),
            use_local_tools=bool(flat.get("use_local_tools", True)),
            data_dirs=flat.get("data_dirs"),
            probe=probe,
            exec_timeout=int(flat.get("exec_timeout", 600) or 600),
        )

    @property
    def max_tool_turns(self) -> int:
        return self.limits.tool_turn_cap

    @property
    def max_completion_tokens(self) -> int:
        return self.limits.completion_token_cap

    @property
    def max_queries_per_turn(self) -> int:
        return self.limits.queries_per_turn_cap

    @property
    def search_api_url(self) -> str:
        return self.lookup.endpoint_url

    @property
    def search_auth_env(self) -> str:
        return self.lookup.credential_env

    @property
    def search_provider(self) -> str:
        return self.lookup.backend

    @property
    def search_max_num_results(self) -> int:
        return self.lookup.result_cap

    @property
    def search_timeout_seconds(self) -> int:
        return self.lookup.deadline_seconds

    @property
    def diagnostic_mode(self) -> bool:
        return self.probe.enabled

    @property
    def diagnostic_instruction(self) -> str:
        return self.probe.instruction


@dataclass(frozen=True)
class OfficeQABatchKnobs:
    """Batch rollout settings (includes worker count)."""

    item: OfficeQAItemKnobs
    workers: int = 8
    task_timeout: int = 1800

    @classmethod
    def from_flat(
        cls,
        out_root: str,
        skill_content: str,
        /,
        *,
        workers: int = 8,
        task_timeout: int = 1800,
        **flat: Any,
    ) -> OfficeQABatchKnobs:
        return cls(
            item=OfficeQAItemKnobs.from_flat(out_root, skill_content, **flat),
            workers=int(workers or 8),
            task_timeout=int(task_timeout or 1800),
        )

    @property
    def out_root(self) -> str:
        return self.item.out_root

    @property
    def skill_content(self) -> str:
        return self.item.skill_content

    @property
    def max_tool_turns(self) -> int:
        return self.item.max_tool_turns

    @property
    def max_completion_tokens(self) -> int:
        return self.item.max_completion_tokens

    @property
    def search_mode(self) -> str:
        return self.item.search_mode

    @property
    def max_queries_per_turn(self) -> int:
        return self.item.max_queries_per_turn

    @property
    def search_api_url(self) -> str:
        return self.item.search_api_url

    @property
    def search_auth_env(self) -> str:
        return self.item.search_auth_env

    @property
    def search_provider(self) -> str:
        return self.item.search_provider

    @property
    def search_max_num_results(self) -> int:
        return self.item.search_max_num_results

    @property
    def search_timeout_seconds(self) -> int:
        return self.item.search_timeout_seconds

    @property
    def use_local_tools(self) -> bool:
        return self.item.use_local_tools

    @property
    def data_dirs(self) -> list[str] | str | None:
        return self.item.data_dirs

    @property
    def diagnostic_mode(self) -> bool:
        return self.item.diagnostic_mode

    @property
    def diagnostic_instruction(self) -> str:
        return self.item.diagnostic_instruction

    def as_item_knobs(self) -> OfficeQAItemKnobs:
        return self.item


@dataclass(frozen=True)
class _PreparedItem:
    mode: str
    docs_roots: list[str]
    candidate_files: list[str]
    oracle_context: str
    fallback_system: str
    fallback_user: str


def _offline_note(use_local_tools: bool) -> str:
    return _OFFLINE_TOOLS_NOTE if use_local_tools else _OFFLINE_PLAIN_NOTE


def _materialize_oracle(
    source_files: object,
    source_docs: object,
    docs_roots: list[str],
    *,
    evidence_note: str | None = None,
) -> str:
    if evidence_note is None:
        return build_oracle_parsed_pages_context(source_files, source_docs, docs_roots)
    return build_oracle_parsed_pages_context(
        source_files,
        source_docs,
        docs_roots,
        evidence_note=evidence_note,
    )


def _prepare_item(item: dict, knobs: OfficeQAItemKnobs) -> _PreparedItem:
    mode = normalize_search_mode(knobs.search_mode)
    docs_roots = resolve_docs_roots(knobs.data_dirs)
    source_files = item.get("source_files", [])
    source_docs = item.get("source_docs", [])

    candidates: list[str] = []
    oracle = ""
    if mode == SEARCH_OFFLINE:
        candidates = resolve_candidate_files(source_files, docs_roots)
        oracle = _materialize_oracle(
            source_files,
            source_docs,
            docs_roots,
            evidence_note=_offline_note(knobs.use_local_tools),
        )
    elif mode == SEARCH_CUSTOM:
        if source_files:
            candidates = resolve_candidate_files(source_files, docs_roots)
        oracle = _materialize_oracle(source_files, source_docs, docs_roots)

    fallback_system = build_system_prompt(
        knobs.skill_content,
        search_mode=mode,
        use_local_tools=knobs.use_local_tools,
        max_tool_turns=knobs.max_tool_turns,
        max_queries_per_turn=knobs.max_queries_per_turn,
    )
    offline_files = candidates if mode == SEARCH_OFFLINE else None
    fallback_user = build_user_prompt(
        item,
        offline_files,
        diagnostic_mode=knobs.diagnostic_mode,
        diagnostic_instruction=knobs.diagnostic_instruction,
        search_mode=mode,
        max_tool_turns=knobs.max_tool_turns,
        max_queries_per_turn=knobs.max_queries_per_turn,
        oracle_context=oracle,
    )
    return _PreparedItem(
        mode=mode,
        docs_roots=docs_roots,
        candidate_files=candidates,
        oracle_context=oracle,
        fallback_system=fallback_system,
        fallback_user=fallback_user,
    )


def _invoke_dialogue(
    item: dict,
    knobs: OfficeQAItemKnobs,
    prepared: _PreparedItem,
) -> _DialogueOutcome:
    try:
        return dispatch_dialogue(
            item,
            knobs.skill_content,
            mode=prepared.mode,
            max_tool_turns=knobs.max_tool_turns,
            max_completion_tokens=knobs.max_completion_tokens,
            max_queries_per_turn=knobs.max_queries_per_turn,
            search_api_url=knobs.search_api_url,
            search_auth_env=knobs.search_auth_env,
            search_provider=knobs.search_provider,
            search_max_num_results=knobs.search_max_num_results,
            search_timeout_seconds=knobs.search_timeout_seconds,
            use_local_tools=knobs.use_local_tools,
            diagnostic_mode=knobs.diagnostic_mode,
            diagnostic_instruction=knobs.diagnostic_instruction,
            candidate_files=prepared.candidate_files,
            docs_roots=prepared.docs_roots,
            oracle_context=prepared.oracle_context,
            exec_timeout=knobs.exec_timeout,
        )
    except Exception as exc:  # noqa: BLE001
        return _DialogueOutcome(
            system=prepared.fallback_system,
            user=prepared.fallback_user,
            response="",
            answer="",
            conversation=[{"role": "user", "content": prepared.fallback_user}],
            fail_reason=f"error: {exc}",
            response_metadata={},
        )


def _score_row(
    item: dict,
    outcome: _DialogueOutcome,
    prepared: _PreparedItem,
    *,
    use_local_tools: bool,
) -> dict:
    gold = item.get("ground_truth", "")
    if outcome.answer:
        scored = evaluate(outcome.answer, gold)
    else:
        scored = {"em": 0.0, "f1": 0.0, "predicted_answer": "", "gold_answer": gold}

    mismatch = ""
    if not scored["em"]:
        mismatch = (
            f"predicted '{scored['predicted_answer']}' but expected "
            f"'{item.get('ground_truth', '')}'"
        )
    oracle_text = prepared.oracle_context
    return {
        "id": str(item["id"]),
        "question": item.get("question", ""),
        "task_type": item.get("task_type", "officeqa"),
        "task_description": item.get("question", ""),
        "predicted_answer": scored["predicted_answer"],
        "response": outcome.response,
        "ground_truth": gold,
        "source_files": item.get("source_files", []),
        "resolved_source_paths": prepared.candidate_files,
        "oracle_parsed_pages_included": bool(oracle_text),
        "oracle_parsed_pages_chars": len(oracle_text),
        "use_local_tools": bool(use_local_tools),
        "hard": int(scored["em"]),
        "soft": scored["f1"],
        "fail_reason": outcome.fail_reason or mismatch,
        "agent_ok": not outcome.fail_reason,
        "n_turns": len(outcome.conversation),
        "last_finish_reason": outcome.response_metadata.get("finish_reason", ""),
        "target_system_prompt": outcome.system,
        "target_user_prompt": outcome.user,
    }


_EXEC_DOCS_DIR = "docs"


def _docs_root_aliases(docs_roots: list[str]) -> list[tuple[str, str]]:
    """Unique ``docs/<basename>`` aliases for each corpus root."""
    aliases: list[tuple[str, str]] = []
    used: set[str] = set()
    for root in docs_roots:
        base = os.path.basename(os.path.normpath(root)) or "corpus"
        name = base
        suffix = 2
        while name in used:
            name = f"{base}_{suffix}"
            suffix += 1
        used.add(name)
        aliases.append((os.path.abspath(root), f"{_EXEC_DOCS_DIR}/{name}"))
    return aliases


def _workspace_rel_for(path: str, aliases: list[tuple[str, str]]) -> str:
    """Map an absolute corpus file to ``docs/<root_alias>/...`` (or ``docs/<name>``)."""
    abs_path = os.path.abspath(path)
    for root_abs, alias in aliases:
        try:
            rel = os.path.relpath(abs_path, root_abs)
        except ValueError:  # different drive on Windows
            continue
        if not rel.startswith("..") and not os.path.isabs(rel):
            return f"{alias}/{rel}".replace("\\", "/")
    return f"{_EXEC_DOCS_DIR}/{os.path.basename(abs_path)}"


def _exec_doc_links(candidates: list[str], docs_roots: list[str]) -> list[tuple[str, str]]:
    """Link only *candidate* files into the workspace (never the full corpus root)."""
    aliases = _docs_root_aliases(docs_roots)
    links: list[tuple[str, str]] = []
    seen_dst: set[str] = set()
    for path in candidates:
        if not path or not os.path.isfile(path):
            continue
        abs_path = os.path.abspath(path)
        rel_dst = _workspace_rel_for(abs_path, aliases)
        if rel_dst in seen_dst:
            continue
        seen_dst.add(rel_dst)
        links.append((abs_path, rel_dst))
    return links


def _rebase_candidates(candidates: list[str], docs_roots: list[str]) -> list[str]:
    """Rewrite absolute corpus paths to their workspace-relative ``docs/...`` form."""
    aliases = _docs_root_aliases(docs_roots)
    return [_workspace_rel_for(path, aliases) for path in candidates]


def _named_source_files(item: dict) -> list[str]:
    raw = item.get("source_files") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(name).strip() for name in raw if str(name).strip()]


def _exec_task_text(item: dict, knobs: OfficeQAItemKnobs, prepared: _PreparedItem, links: list[tuple[str, str]]) -> str:
    display = [rel_dst for _src, rel_dst in links] or (
        _rebase_candidates(prepared.candidate_files, prepared.docs_roots)
        if _named_source_files(item)
        else []
    )
    user = build_user_prompt(
        item,
        display,
        diagnostic_mode=knobs.diagnostic_mode,
        diagnostic_instruction=knobs.diagnostic_instruction,
        search_mode=prepared.mode,
        max_tool_turns=knobs.max_tool_turns,
        max_queries_per_turn=knobs.max_queries_per_turn,
        oracle_context=prepared.oracle_context,
    )
    if not links:
        return user
    lines = [f"- `{rel_dst}`" for _src, rel_dst in links]
    return (
        f"{user}\n\n## Local Documents\n"
        "Only these candidate document files are available in this workspace; "
        "use file tools (read / grep) on them instead of web search:\n" + "\n".join(lines)
    )


def _execute_single_item_exec(item: dict, knobs: OfficeQAItemKnobs) -> dict:
    """Run one OfficeQA item through the jiuwenswarm CLI harness (offline corpus only)."""
    prepared = _prepare_item(item, knobs)
    if prepared.mode != SEARCH_OFFLINE:
        raise ValueError(
            f"search_mode={prepared.mode!r} requires a chat target backend; "
            f"target backend {get_target_backend()!r} only supports offline mode"
        )
    # Mount only files named by source_files — never the whole docs root.
    # (When source_files is empty, resolve_candidate_files returns the full corpus.)
    mount_files = prepared.candidate_files if _named_source_files(item) else []
    links = _exec_doc_links(mount_files, prepared.docs_roots)
    skill_md = render_skill_md(knobs.skill_content)
    task_text = _exec_task_text(item, knobs, prepared, links)
    work_dir = exec_work_dir_for(knobs.out_root, str(item["id"]))
    try:
        prepare_workspace(work_dir=work_dir, skill_md=skill_md, task_text=task_text, link_dirs=links)
        response, raw = run_jiuwenswarm_cli_exec(
            work_dir=work_dir,
            prompt=default_exec_prompt(
                f"answer the OfficeQA question using the documents under `{_EXEC_DOCS_DIR}/` in this workspace."
            ),
            timeout=knobs.exec_timeout,
        )
    except (AttributeError, KeyError, NameError, TypeError, AssertionError):
        raise
    except Exception as exc:  # noqa: BLE001 — expected harness/runtime failures
        outcome = _DialogueOutcome(
            system=skill_md,
            user=task_text,
            response="",
            answer="",
            conversation=[{"role": "user", "content": task_text}],
            fail_reason=f"error: {exc}",
            response_metadata={},
        )
    else:
        text = response or raw
        tagged = "<answer>" in text.lower()
        outcome = _DialogueOutcome(
            system=skill_md,
            user=task_text,
            response=text,
            answer=extract_answer(text) if tagged else "",
            conversation=[{"type": "message", "turn": 1, "content": text}],
            fail_reason="" if tagged else "Model reply lacked a final <answer> tag",
            response_metadata={"backend": get_target_backend()},
        )
    write_prediction_artifacts(
        knobs.out_root,
        str(item["id"]),
        system_prompt=outcome.system,
        user_prompt=outcome.user,
        conversation=outcome.conversation,
    )
    return _score_row(item, outcome, prepared, use_local_tools=knobs.use_local_tools)


def _execute_single_item(item: dict, knobs: OfficeQAItemKnobs) -> dict:
    if is_target_exec_backend():
        return _execute_single_item_exec(item, knobs)
    prepared = _prepare_item(item, knobs)
    outcome = _invoke_dialogue(item, knobs, prepared)
    write_prediction_artifacts(
        knobs.out_root,
        str(item["id"]),
        system_prompt=outcome.system,
        user_prompt=outcome.user,
        conversation=outcome.conversation,
    )
    return _score_row(item, outcome, prepared, use_local_tools=knobs.use_local_tools)


def _merge_rollout_options(defaults: dict[str, Any], **options: Any) -> dict[str, Any]:
    merged = dict(defaults)
    merged.update(options)
    return merged


def process_one(
    item: dict,
    out_root: str,
    skill_content: str,
    **options: Any,
) -> dict:
    """Run one OfficeQA item and persist prediction artifacts.

    Keyword options mirror :class:`OfficeQAItemKnobs` fields, e.g.
    ``max_tool_turns``, ``search_mode``, ``search_api_url``.
    """
    merged = _merge_rollout_options(_PROCESS_ONE_OPTION_DEFAULTS, **options)
    knobs = OfficeQAItemKnobs.from_flat(out_root, skill_content, **merged)
    return _execute_single_item(item, knobs)


def run_batch(
    items: list[dict],
    out_root: str | OfficeQABatchKnobs,
    skill_content: str = "",
    **options: Any,
) -> list[dict]:
    """Parallel OfficeQA rollout over ``items``.

    Accepts either an :class:`OfficeQABatchKnobs` as the second argument or the
    legacy positional ``out_root`` + keyword knobs used by older callers.
    """
    batch = (
        out_root
        if isinstance(out_root, OfficeQABatchKnobs)
        else OfficeQABatchKnobs.from_flat(
            out_root,
            skill_content,
            **_merge_rollout_options(_BATCH_OPTION_DEFAULTS, **options),
        )
    )
    per_item = batch.as_item_knobs()

    def _worker(entry: dict) -> dict:
        return _execute_single_item(entry, per_item)

    soft_floor = int(batch.item.exec_timeout) + 60
    deadline = max(int(batch.task_timeout), soft_floor)

    return run_parallel_rollout(
        items,
        batch.out_root,
        process_one=_worker,
        workers=batch.workers,
        task_timeout=deadline,
    )
