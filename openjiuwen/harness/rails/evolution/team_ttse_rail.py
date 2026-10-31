# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Leader-side TTSE for team mode.

Collects the team root trace while the leader invoke is open, then splits
ended spans into one leader window and one window per member. Induction
runs only after the leader's idle poll reports the team completed, and it
runs in the background so pause and stream close are not blocked.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading
from copy import copy, deepcopy
from typing import Any

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.spans import (
    iter_spans,
    merge_trajectories,
    span_attributes,
    trim_trajectory,
)
from openjiuwen.agent_evolving.ttse.capabilities import render_capabilities
from openjiuwen.agent_evolving.ttse.catalog import catalog_dir
from openjiuwen.agent_evolving.ttse.config import TTSEConfig
from openjiuwen.agent_evolving.ttse.subject_log import bind_induce_subject, logger, reset_induce_subject
from openjiuwen.core.common.background_tasks import BackgroundTask
from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.extensions.observability import semconv as observability_semconv
from openjiuwen.extensions.observability.span_context import get_root_span
from openjiuwen.harness.rails.evolution.evolution_rail import EvolutionTriggerPoint
from openjiuwen.harness.rails.evolution.ttse_rail import TTSERail, _TTSE_PROMPT_PRIORITY

_LEADER_INDUCE_NOTE = """\
Role: you are the team leader. You staff the team, write the task, review deliverables, and send files to the user. You do not run the member's collection or charting tools.
A member procedure is in scope only as text you should put into spawn_teammate, create_task, or send_message next time. The TIP capability must be a tool this leader actually called in the tool chain. Do not use python_exec, bash, or curl as that capability.
Do not store this run's cities, numbers, file names, or other one-off data. Store the assignment or review change that data caused.\
"""

_LEADER_CATALOG_GUIDANCE_CN = """\
## 经验目录

末尾附件是组长经验条数，不按业务类目划分。闲聊可忽略。
非闲聊且条数不为 0 时，动手前用 `ttse_consult` 取回：`category` 填 `all`，`query` 用本轮协作关键词，取回后再规划。条数为 0 或附件为 `(empty)` 时直接执行。
经验是历史启发式，与当前工具证据冲突时以当前证据为准。
"""

_LEADER_CATALOG_GUIDANCE_EN = """\
## Experience catalog

The trailing attachment is the leader bank size, not a business-category listing. Ignore it for chitchat.
On a non-trivial task, if the count is not zero, call `ttse_consult` before acting with category `all` and a keyword query, then plan from the returned experience. If the count is zero or the attachment is `(empty)`, proceed.
These are historical heuristics; if they conflict with current tool evidence, trust the current evidence.
"""

_LEADER_CONSULT_DESCRIPTION = (
    "Retrieve FACT and TIP rule bodies from the leader bank. "
    "This bank is not split into business categories. Set category to all. "
    "Do not call this tool to re-list the catalog. "
    "Rewrite the user request into query by joining keywords; do not paste the full message. "
    "Do not use bash or read_file to scan the experience bank."
)

_LEADER_CATEGORY_DESCRIPTION = (
    "Always `all`. The leader bank has no business categories; "
    "`all` reads the whole bank and is not a stored class."
)


def render_leader_catalog_markdown(fact_count: int, tip_count: int) -> str:
    """Leader prompt attachment: a count, not the business-category listing."""
    total = int(fact_count) + int(tip_count)
    lines = [
        "# 经验目录",
        "",
        "组长经验不按业务类目划分。正文用 `ttse_consult` 取回，`category` 填 `all`。",
        "",
    ]
    if total <= 0:
        lines.append("(empty)")
    else:
        lines.append(f"- 共 {total} 条（FACT {fact_count}，TIP {tip_count}）")
    return "\n".join(lines) + "\n"

_LEADER_KIND = "team_leader"
_MEMBER_KIND = "team_member"
_SUBJECT_KIND = observability_semconv.OJ_EXECUTION_SUBJECT_KIND
_SUBJECT_ID = observability_semconv.OJ_EXECUTION_SUBJECT_ID


def _team_trace() -> tuple[str, str] | None:
    """Return the active team root trace id and team name, or None."""

    root = get_root_span()
    if root is None:
        return None
    recording = getattr(root, "is_recording", None)
    try:
        recording = recording() if callable(recording) else recording
    except Exception:
        return None
    if not recording:
        return None
    trace_id = getattr(getattr(root, "context", None), "trace_id", None)
    if not isinstance(trace_id, int) or trace_id <= 0:
        return None
    attributes = getattr(root, "attributes", None) or {}
    try:
        team_name = attributes.get(observability_semconv.AT_TEAM_NAME)
    except AttributeError:
        return None
    team_id = str(team_name).strip() if team_name is not None else ""
    if not team_id:
        return None
    return f"{trace_id:032x}", team_id


def _trajectory_for_subject(increment: Any, key: tuple[str, ...]) -> Trajectory | None:
    """Keep one subject's spans and the resource metadata they were captured with."""

    payload = deepcopy(increment.to_otlp()) if hasattr(increment, "to_otlp") else deepcopy(increment)
    resources: list[dict[str, Any]] = []
    for resource in payload.get("resourceSpans") or []:
        if not isinstance(resource, dict):
            continue
        scopes: list[dict[str, Any]] = []
        for scope in resource.get("scopeSpans") or []:
            if not isinstance(scope, dict):
                continue
            spans = [span for span in scope.get("spans") or [] if _subject_key(span) == key]
            if spans:
                scopes.append({**scope, "spans": spans})
        if scopes:
            resources.append({**resource, "scopeSpans": scopes})
    if not resources:
        return None
    return Trajectory.from_otlp({"resourceSpans": resources})


def _subject_label(kind: str, subject_id: str = "") -> str:
    """Short log id: ``leader`` or ``member:<name>``."""

    if kind == "leader":
        return "leader"
    name = subject_id.rsplit(":", 1)[-1].strip() if subject_id else ""
    return f"member:{name or subject_id or 'unknown'}"


def _called_tool_names(messages: list) -> list[str]:
    """Tool names that actually appear in this window, in first-seen order."""

    seen: set[str] = set()
    names: list[str] = []
    for raw in messages or []:
        if not isinstance(raw, dict):
            continue
        calls = raw.get("tool_calls") or []
        for call in calls:
            if isinstance(call, dict):
                function = call.get("function") if isinstance(call.get("function"), dict) else {}
                name = str(call.get("name") or function.get("name") or "")
            else:
                name = str(getattr(call, "name", "") or "")
            name = name.strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)
        if (raw.get("role") or "") == "tool":
            name = str(raw.get("name") or "").strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)
    return names


def _capabilities_from_called_tools(names: list[str]) -> str:
    lines = ["TOOLS CALLED IN THIS WINDOW (TIPs may only reference these names):"]
    for name in names:
        lines.append(f"- tool `{name}`")
    return "\n".join(lines)


def _subject_key(span: Any) -> tuple[str, ...] | None:
    """Map one span onto the leader window or a member window."""

    attributes = span_attributes(span)
    kind = str(attributes.get(_SUBJECT_KIND) or "")
    subject_id = str(attributes.get(_SUBJECT_ID) or "").strip()
    if kind == _LEADER_KIND:
        return ("leader",)
    if kind == _MEMBER_KIND and subject_id:
        return ("member", subject_id)
    return None


class TeamTTSERail(TTSERail):
    """Leader TTSE rail: split team spans, write two banks, induce on acceptance.

    The mounted instance reads and writes the leader bank and injects from it.
    A second, unmounted ``TTSERail`` writes the shared member bank. Member
    agents mount their own inject-only ``TTSERail`` on that same bank.
    """

    def __init__(
        self,
        *,
        llm: Any,
        model: str,
        member_store_path: str,
        ttse_config: TTSEConfig | None = None,
        embedding: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            llm=llm,
            model=model,
            ttse_config=ttse_config,
            embedding=embedding,
            **kwargs,
        )
        member_cfg = copy(self._ttse_config)
        member_cfg.store_path = member_store_path
        member_cfg.inject_enabled = False
        member_cfg.evolve_enabled = False
        self._member_writer = TTSERail(
            llm=llm,
            model=model,
            ttse_config=member_cfg,
            embedding=self._ttse_config.embedding,
            trajectory_span_processor=self._trajectory_span_processor,
        )
        self._consult_tool_description = _LEADER_CONSULT_DESCRIPTION
        self._consult_category_description = _LEADER_CATEGORY_DESCRIPTION
        self._subject_windows: dict[tuple[str, ...], Any] = {}
        self._subject_lock = threading.Lock()
        self._round_user_query = ""
        self._logged_missing_root = False
        self._release_round_subscription = False

    def _hold_subscription_across_invokes(self) -> bool:
        """One team-trace subscription covers the whole user round, including idle gaps."""

        return True

    def _release_held_subscription(self) -> bool:
        if not self._release_round_subscription:
            return False
        self._release_round_subscription = False
        return True

    def _drain_held_pending(self) -> None:
        """Fold spans that ended while the leader was between invokes into the windows.

        Acceptance runs on the idle poll, after the invoke context has been
        detached, so the held subscription is also taken from the active set.
        """

        captures: list[Any] = []
        current = self._current_capture()
        if current is not None and current.hold_across_invokes:
            captures.append(current)
        with self._subscription_lock:
            for item in self._active_captures.values():
                if item.hold_across_invokes and item not in captures:
                    captures.append(item)
        for capture in captures:
            increment, issues = self._trajectory_span_processor.drain(capture.subscription)
            if increment is None or issues:
                continue
            self._merge_clean_increment(capture, increment)

    @staticmethod
    def _subscription_categories() -> tuple[str, ...]:
        # Induction reads model and tool spans. The processor has no ``event``
        # category; team/plan spans are not part of a FACT/TIP window.
        return ("llm", "tool")

    def _capture_route(self, ctx: AgentCallbackContext) -> tuple[str | None, str | None, str | None]:
        del ctx
        identity = _team_trace()
        if identity is None:
            if not self._logged_missing_root:
                self._logged_missing_root = True
                logger.warning("[TeamTTSERail] no team root span; member spans will not be collected")
            return None, None, None
        trace_id, team_id = identity
        return None, team_id, trace_id

    @staticmethod
    def _scope_key(
        *,
        session_id: str,
        member_id: str | None,
        team_id: str | None,
    ) -> tuple[str, ...]:
        del member_id
        return ("team_ttse", team_id or "local", str(session_id))

    def _allow_evolution_trigger(self, trigger_point: EvolutionTriggerPoint, ctx: AgentCallbackContext) -> bool:
        """Never induce from an invoke. Acceptance owns induction."""

        del trigger_point, ctx
        return False

    async def _on_before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Keep subject windows across invokes and remember this round's user query."""

        if self._round_user_query or self._is_background_run(ctx):
            return
        query = self._extract_query(ctx).strip()
        if query:
            self._round_user_query = query

    def _merge_clean_increment(self, capture: Any, increment: Any, *, project: bool = True) -> Any:
        """Split a drained increment by execution subject. Do not keep one team window."""

        del capture, project
        keys: set[tuple[str, ...]] = set()
        for span in iter_spans(increment):
            key = _subject_key(span)
            if key is not None:
                keys.add(key)
        if not keys:
            return None
        with self._subject_lock:
            for key in keys:
                piece = _trajectory_for_subject(increment, key)
                if piece is None:
                    continue
                current = self._subject_windows.get(key)
                merged = merge_trajectories(current, piece) if current is not None else piece
                self._subject_windows[key] = trim_trajectory(merged, self._max_trajectory_spans)
        return None

    async def _on_after_task_iteration(self, ctx: AgentCallbackContext, trajectory: Any) -> None:
        """One leader iteration advances both banks' dream counters."""

        await super()._on_after_task_iteration(ctx, trajectory)
        await self._member_writer.note_task_iteration(ctx, trajectory)

    async def _classify_added_rules(self, items: list[tuple[str, str]]) -> None:
        """Leader rules stay uncategorized. Members still use the business taxonomy."""
        if items:
            logger.info("[TeamTTSERail] skip category assignment count=%s", len(items))

    def _catalog_guidance_section(self) -> PromptSection:
        return PromptSection(
            name=SectionName.TTSE_FACTS_TIPS,
            content={
                "cn": _LEADER_CATALOG_GUIDANCE_CN,
                "en": _LEADER_CATALOG_GUIDANCE_EN,
            },
            priority=_TTSE_PROMPT_PRIORITY,
        )

    def _catalog_attachment(self) -> tuple[str, dict]:
        facts = len(getattr(self._ttse_store, "facts", []) or [])
        tips = len(getattr(self._ttse_store, "tips", []) or [])
        live = {"facts": facts, "tips": tips}
        return render_leader_catalog_markdown(facts, tips), live

    async def _maybe_project_catalog(self) -> None:
        try:
            await asyncio.to_thread(self._project_leader_catalog)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[TeamTTSERail] catalog projection skipped: %s", exc)

    def _project_leader_catalog(self) -> None:
        """Write a count-only catalog next to the leader bank. Drop category folders."""
        store_path = str(getattr(self._ttse_config, "store_path", "") or "")
        if not store_path:
            return
        root = catalog_dir(store_path)
        os.makedirs(root, exist_ok=True)
        facts = len(getattr(self._ttse_store, "facts", []) or [])
        tips = len(getattr(self._ttse_store, "tips", []) or [])
        with open(os.path.join(root, "CATALOG.md"), "w", encoding="utf-8") as handle:
            handle.write(render_leader_catalog_markdown(facts, tips))
        index = {"uncategorized": True, "facts": facts, "tips": tips}
        with open(os.path.join(root, "index.json"), "w", encoding="utf-8") as handle:
            json.dump(index, handle, ensure_ascii=False, indent=1)
        by_cat = os.path.join(root, "by_cat")
        if os.path.isdir(by_cat):
            shutil.rmtree(by_cat, ignore_errors=True)
        logger.info("[TeamTTSERail] catalog projected path=%s facts=%s tips=%s", root, facts, tips)

    async def notify_team_accepted(self) -> None:
        """Snapshot subject windows and schedule induction without waiting for it."""

        self._drain_held_pending()
        leader, members, query = self._take_round()
        self._release_round_subscription = True
        if not self._ttse_config.evolve_enabled:
            logger.info("[TeamTTSERail] induce skip reason=evolve_disabled")
            return
        if leader is None and not members:
            logger.info("[TeamTTSERail] induce skip reason=no_windows")
            return
        task = asyncio.create_task(
            self._induce_round(leader, members, query),
            name="team-ttse-induce",
        )
        self._bg_tasks.add(BackgroundTask.from_asyncio_task(task, group="evolution"))
        self._bg_tasks = {item for item in self._bg_tasks if not item.done()}

    def _take_round(self) -> tuple[Any, dict[str, Any], str]:
        with self._subject_lock:
            leader = self._subject_windows.get(("leader",))
            members = {
                key[1]: window
                for key, window in self._subject_windows.items()
                if key and key[0] == "member"
            }
            self._subject_windows.clear()
            query = self._round_user_query
            self._round_user_query = ""
        return leader, members, query

    async def _induce_round(self, leader: Any, members: dict[str, Any], query: str) -> None:
        try:
            member_labels = [_subject_label("member", subject_id) for subject_id in sorted(members)]
            logger.info(
                "[TeamTTSERail] induce round leader=%s members=%s",
                "yes" if leader is not None else "no",
                ",".join(member_labels) or "-",
            )
            if leader is None:
                logger.info("[TeamTTSERail] induce skip subject=leader reason=no_window")

            async def _lead() -> None:
                if leader is not None:
                    await self._induce_window(self, leader, query, subject="leader")

            async def _members() -> None:
                for subject_id in sorted(members):
                    await self._induce_window(
                        self._member_writer,
                        members[subject_id],
                        query,
                        subject=_subject_label("member", subject_id),
                    )

            outcomes = await asyncio.gather(_lead(), _members(), return_exceptions=True)
            for outcome in outcomes:
                if isinstance(outcome, Exception):
                    logger.error("[TeamTTSERail] induce window crashed", exc_info=outcome)
        except Exception:
            logger.exception("[TeamTTSERail] induce round failed")

    async def _capabilities_for_window(self, label: str, messages: list) -> str:
        """Leader uses mounted tools. Members use tools this window actually called."""

        if label == "leader" and self._agent is not None:
            try:
                rendered = await render_capabilities(self._agent, fallback=False)
            except Exception:
                logger.exception("[TeamTTSERail] leader capability render failed")
                rendered = ""
            if rendered:
                return rendered
            logger.warning("[TeamTTSERail] leader capabilities empty reason=agent exposed no tools")
        elif label == "leader":
            logger.warning("[TeamTTSERail] leader capabilities unavailable reason=agent is None")

        names = _called_tool_names(messages)
        if names:
            logger.info(
                "[TeamTTSERail] capabilities from tool chain subject=%s tools=%s",
                label,
                ",".join(names),
            )
            return _capabilities_from_called_tools(names)
        logger.warning("[TeamTTSERail] capabilities empty subject=%s reason=no tools in window", label)
        return (
            "TOOLS CALLED IN THIS WINDOW: (none). "
            "Do not invent capability names such as bash, python_exec, or web_search."
        )

    async def _induce_window(self, writer: TTSERail, trajectory: Any, query: str, *, subject: str = "") -> None:
        label = subject or "unknown"
        logger.info("[TeamTTSERail] induce begin subject=%s", label)
        for attempt in (1, 2):
            token = bind_induce_subject(label)
            try:
                messages = writer.messages_from_trajectory(trajectory)
                snapshot: dict[str, Any] = {"messages": messages}
                if query:
                    snapshot["ttse_task_query"] = query
                if label == "leader":
                    snapshot["ttse_role_note"] = _LEADER_INDUCE_NOTE
                snapshot["ttse_capabilities"] = await self._capabilities_for_window(label, messages)
                result = await writer.run_induction(trajectory, None, snapshot=snapshot)
                logger.info("[TeamTTSERail] induce end subject=%s result=%s", label, result)
                return
            except Exception:
                logger.exception(
                    "[TeamTTSERail] induce failed subject=%s attempt=%s",
                    label,
                    attempt,
                )
            finally:
                reset_induce_subject(token)
        logger.warning("[TeamTTSERail] induce drop subject=%s reason=retry_exhausted", label)
