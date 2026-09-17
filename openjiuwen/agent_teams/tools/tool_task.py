# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Task management tools: create, view, update, submit, claim, and complete."""

from typing import Any

from openjiuwen.agent_teams.schema.status import TaskStatus
from openjiuwen.agent_teams.schema.task import TaskGraphSpec
from openjiuwen.agent_teams.timefmt import format_time_context
from openjiuwen.agent_teams.tools.database.engine import get_current_time
from openjiuwen.agent_teams.tools.locales import Translator
from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.tool_base import TeamTool
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput


# ========== Task Management ==========


def _base_task_node_properties(t: Translator) -> dict[str, Any]:
    """Build the task fields shared by every ``create_task`` variant."""
    return {
        "task_id": {"type": "string", "description": t("create_task", "task.task_id")},
        "title": {"type": "string", "description": t("create_task", "task.title")},
        "content": {"type": "string", "description": t("create_task", "task.content")},
        "depends_on": {
            "type": "array",
            "items": {"type": "string"},
            "description": t("create_task", "task.depends_on"),
        },
        "depended_by": {
            "type": "array",
            "items": {"type": "string"},
            "description": t("create_task", "task.depended_by"),
        },
    }


def _task_node_schema(
    properties: dict[str, Any],
    *,
    extra_required: list[str] | None = None,
) -> dict:
    """Build a per-task JSON schema from mode-specific properties.

    The mode-specific wrapper decides which properties exist. This keeps the
    schema readable at the call site instead of hiding dispatch semantics behind
    boolean flags.
    """
    return {
        "type": "object",
        "properties": properties,
        "required": ["title", "content", *(extra_required or [])],
    }


def _autonomous_task_node_schema(t: Translator) -> dict:
    """Build the autonomous create_task node schema."""
    properties = _base_task_node_properties(t)
    properties["assignee"] = {"type": "string", "description": t("create_task", "task.assignee")}
    return _task_node_schema(properties)


def _scheduled_task_node_schema(t: Translator) -> dict:
    """Build the scheduled create_task node schema."""
    properties = _base_task_node_properties(t)
    properties.update(
        {
            "assignee": {"type": "string", "description": t("create_task", "task.assignee")},
            # reviewer 结构化对象数组。model 只传 type + description，
            # reviewer_id 由代码按类型自动编号（verifier_1, inspector_1 等）。
            "reviewer": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["verifier", "inspector", "challenger"],
                            "description": t("create_task", "task.reviewer_type"),
                        },
                        "reviewer_id": {"type": "string", "description": t("create_task", "task.reviewer_id")},
                        "instruction": {"type": "string", "description": t("create_task", "task.reviewer_instruction")},
                    },
                    "required": ["type"],
                },
                "description": t("create_task", "task.reviewer"),
            },
            "max_review_rounds": {
                "type": "integer",
                "minimum": 1,
                "description": t("create_task", "task.max_review_rounds"),
            },
        }
    )
    return _task_node_schema(properties, extra_required=["assignee"])


async def _validate_assignees(
    agent_team: TeamBackend,
    tasks: list[dict],
    *,
    required: bool,
) -> str | None:
    """Reject assignees that are missing, unknown, or point to the leader."""
    leader_member_name = await agent_team.resolve_leader_member_name()
    for spec in tasks:
        assignee = (spec.get("assignee") or "").strip()
        if not assignee:
            if required:
                return (
                    f"Task {_spec_label(spec)!r} missing required 'assignee' — "
                    f"assigned tasks must name a non-leader team member"
                )
            continue
        if leader_member_name and assignee == leader_member_name:
            return (
                f"Task {_spec_label(spec)!r}: assignee {assignee!r} is the team leader; "
                f"assign the task to a non-leader member"
            )
        if not await agent_team.member_exists(assignee):
            return f"Task {_spec_label(spec)!r}: member {assignee!r} not found in the team"
    return None


def _spec_label(spec: dict) -> str:
    """Human-readable label for a task spec in error messages."""
    return spec.get("task_id") or spec.get("title") or "<unnamed>"


def _validate_task_batch(tasks: list[dict]) -> str | None:
    """Validate batch-level invariants shared by every ``create_task`` variant.

    Returns an error string, or None when the batch is well-formed. In-batch
    edges have exactly one representation (``depends_on`` on the dependent
    task), so a ``depended_by`` pointing at a task of the same call is
    rejected instead of silently deduplicated — the error teaches the caller
    the canonical form.
    """
    batch_ids: set[str] = set()
    for spec in tasks:
        if not spec.get("title") or not spec.get("content"):
            return f"Task {_spec_label(spec)!r} missing required title/content"
        task_id = spec.get("task_id")
        if task_id:
            if task_id in batch_ids:
                return f"Duplicate task_id {task_id!r} in this call"
            batch_ids.add(task_id)

    for spec in tasks:
        in_batch_targets = [dep for dep in spec.get("depended_by") or () if dep in batch_ids]
        if in_batch_targets:
            return (
                f"Task {_spec_label(spec)!r}: depended_by may only reference "
                f"tasks that already exist on the board, but {in_batch_targets} are created "
                f"in this same call — express in-batch edges with depends_on on the dependent task"
            )
    return None


def _clean_reviewers(spec: dict) -> list[dict]:
    """Extract a spec's reviewer list as structured dicts.

    Handles both old (plain string) and new (object) formats on read.
    Old format entries are upgraded to ``{"type": "verifier", ...}``.

    ``reviewer_id`` is auto-generated from the type with a per-type counter
    (``verifier_1``, ``inspector_1``, ``challenger_2`` …) when not
    already provided by an old-format entry.

    Returns ``list[dict]`` with keys ``type``, ``reviewer_id``, ``description``.
    """
    raw = spec.get("reviewer") or ()
    result: list[dict] = []
    counter: dict[str, int] = {}
    for entry in raw:
        if isinstance(entry, dict):
            rtype = entry.get("type", "verifier")
            counter[rtype] = counter.get(rtype, 0) + 1
            rid = entry.get("reviewer_id") or f"{rtype}_{counter[rtype]}"
            if isinstance(rid, str):
                rid = rid.strip()
            if rid:
                result.append({
                    "type": rtype,
                    "reviewer_id": rid,
                    "instruction": str(entry.get("instruction", "")),
                })
        elif isinstance(entry, str):
            stripped = str(entry).strip()
            if stripped:
                result.append({"type": "verifier", "reviewer_id": stripped, "instruction": ""})
    return result


async def _validate_reviewers(agent_team: TeamBackend, tasks: list[dict]) -> str | None:
    """Reject a batch whose reviewer equals the task's own author.

    Reviewers no longer must be pre-existing team members — the scheduler
    spawns a temporary harness for any reviewer name not found in the roster.
    Only the self-review guard remains.

    reviewer 条目现在是结构化对象，通过 ``reviewer_id`` 字段与 assignee 比较。
    """
    for spec in tasks:
        reviewers = _clean_reviewers(spec)
        if not reviewers:
            continue
        assignee = (spec.get("assignee") or "").strip()
        for reviewer in reviewers:
            # 从结构化对象中取 reviewer_id，而非旧版的裸字符串
            rid = reviewer.get("reviewer_id", "")
            if assignee and rid == assignee:
                return (
                    f"Task {_spec_label(spec)!r}: reviewer {rid!r} cannot review their own task "
                    f"(they are the assignee)"
                )
    return None


class TaskCreateTool(TeamTool):
    """Create autonomous-dispatch team tasks, optionally pre-assigned.

    The whole call is one atomic graph mutation via ``add_graph``: edges
    among tasks of the same call are expressed with ``depends_on`` only
    (forward references allowed), while ``depended_by`` is reserved for
    wiring *existing* tasks to depend on a new task. In-batch ``depended_by``
    targets are rejected at this boundary as redundant. Tasks without an
    ``assignee`` are claimable from the shared board; tasks with an assignee
    are reserved for that non-leader member.
    """

    def __init__(self, agent_team: TeamBackend, t: Translator):
        super().__init__(
            ToolCard(
                id="team.create_task",
                name="create_task",
                description=t("create_task"),
            )
        )
        self.agent_team = agent_team
        self.task_manager = agent_team.task_manager
        self.card.input_params = {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": _autonomous_task_node_schema(t),
                    "description": t("create_task", "tasks"),
                },
            },
            "required": ["tasks"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        tasks = inputs.get("tasks", [])
        if not tasks:
            return ToolOutput(success=False, error="'tasks' is required")

        error = _validate_task_batch(tasks)
        if error:
            return ToolOutput(success=False, error=error)
        error = await _validate_assignees(self.agent_team, tasks, required=False)
        if error:
            return ToolOutput(success=False, error=error)

        # One atomic graph mutation for the whole call: depends_on may
        # forward-reference tasks later in the batch, and either every
        # task lands or none does (with the real failure reason).
        result = await self.task_manager.add_graph(
            [
                TaskGraphSpec(
                    title=spec["title"],
                    content=spec["content"],
                    task_id=spec.get("task_id"),
                    depends_on=tuple(spec.get("depends_on") or ()),
                    depended_by=tuple(spec.get("depended_by") or ()),
                    assignee=(spec.get("assignee") or "").strip() or None,
                )
                for spec in tasks
            ]
        )
        if not result.ok:
            return ToolOutput(success=False, error=result.reason)

        await self._auto_start_members()

        briefs = [{**task.brief(), "assignee": task.assignee} for task in result.tasks]
        if len(briefs) == 1:
            return ToolOutput(success=True, data=briefs[0])
        return ToolOutput(
            success=True,
            data={"tasks": briefs, "count": len(briefs)},
        )

    async def _auto_start_members(self) -> None:
        """Bring unstarted members up now that the board has work on it.

        Under autonomous dispatch, work reaches members two ways: a message
        the leader writes, and a task it puts on the board. Only the first
        used to start anybody, so a leader that created tasks and then never
        broadcast left the whole roster parked at UNSTARTED — with nothing
        subscribed, the TaskCreatedEvent went out to nobody, and the
        leader-side stale-pending sweep could not recover it either (that
        sweep requires at least one READY member). Members are started here
        for the same reason the message path starts them: an unstarted member
        cannot be handed anything.

        Every unstarted member is started, not just the assignees — an
        unassigned task goes to the shared claim pool, where any member may
        turn out to be the claimant. Best-effort: the tasks are already
        committed, so a spawn failure is logged and left to the leader's
        round-idle reconcile rather than turned into a tool failure that
        would invite the model to create the tasks a second time.
        """
        try:
            started = await self.agent_team.autostart_unstarted()
        except Exception as e:
            team_logger.error("create_task failed to auto-start members: {}", e, exc_info=True)
            return
        if started:
            team_logger.info(f"Auto-started members: {started}")

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Operation failed"
        d = output.data
        if "task_id" in d and "title" in d:
            line = f"Task created: task_id={d['task_id']} title={d['title']}"
            if d.get("assignee"):
                line += f" -> {d['assignee']}"
            return line
        lines = []
        for task in d.get("tasks", []):
            line = f"task_id={task['task_id']} title={task['title']}"
            if task.get("assignee"):
                line += f" -> {task['assignee']}"
            lines.append(line)
        lines.append(f"Created {d['count']}")
        return "\n".join(lines)


def _owner_phrase(task: dict) -> str:
    """Render a scheduled task's owner and whether it is ready or waiting."""
    if task.get("status") == TaskStatus.BLOCKED.value:
        return f"-> {task['assignee']} (blocked; starts once its dependencies complete)"
    return f"-> {task['assignee']} (assigned; the scheduler starts it)"


class ScheduledTaskCreateTool(TeamTool):
    """Create team tasks, each naming its owner (scheduled dispatch).

    Same atomic ``add_graph`` and same edge rules as ``TaskCreateTool``, plus
    a required ``assignee`` that rides along in the same mutation: the task
    rests at PENDING (or BLOCKED, if it has dependencies) *with its owner on
    record*, and the scheduler starts it when execution begins. Members never
    claim in this mode, so a task without an assignee would never run — hence
    ``assignee`` is required and the result echoes the owner and landing
    status. ``max_review_rounds`` optionally caps the verify-gate rework loop
    of one task (requires ``reviewer``, F_62); beyond it the scheduler
    escalates to the leader instead of looping.
    """

    def __init__(self, agent_team: TeamBackend, t: Translator):
        super().__init__(
            ToolCard(
                id="team.create_task",
                name="create_task",
                description=t("create_task_scheduled"),
            )
        )
        self.agent_team = agent_team
        self.task_manager = agent_team.task_manager
        self.card.input_params = {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": _scheduled_task_node_schema(t),
                    "description": t("create_task", "tasks"),
                },
            },
            "required": ["tasks"],
        }

    @staticmethod
    def _validate_review_rounds(tasks: list[dict]) -> str | None:
        """Reject a round ceiling on a task that has no reviewers to vote."""
        for spec in tasks:
            rounds = spec.get("max_review_rounds")
            if rounds is None:
                continue
            if not isinstance(rounds, int) or isinstance(rounds, bool) or rounds < 1:
                return f"Task {_spec_label(spec)!r}: 'max_review_rounds' must be an integer >= 1"
            if not _clean_reviewers(spec):
                return (
                    f"Task {_spec_label(spec)!r}: 'max_review_rounds' only applies to reviewed "
                    f"tasks — set 'reviewer' as well, or drop the round ceiling"
                )
        return None

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        tasks = inputs.get("tasks", [])
        if not tasks:
            return ToolOutput(success=False, error="'tasks' is required")

        error = _validate_task_batch(tasks)
        if error:
            return ToolOutput(success=False, error=error)
        error = await _validate_assignees(self.agent_team, tasks, required=True)
        if error:
            return ToolOutput(success=False, error=error)
        error = self._validate_review_rounds(tasks)
        if error:
            return ToolOutput(success=False, error=error)
        error = await _validate_reviewers(self.agent_team, tasks)
        if error:
            return ToolOutput(success=False, error=error)

        verify_disabled = not self.agent_team.task_verification_enabled()

        result = await self.task_manager.add_graph(
            [
                TaskGraphSpec(
                    title=spec["title"],
                    content=spec["content"],
                    task_id=spec.get("task_id"),
                    depends_on=tuple(spec.get("depends_on") or ()),
                    depended_by=tuple(spec.get("depended_by") or ()),
                    assignee=(spec.get("assignee") or "").strip() or None,
                    reviewer=() if verify_disabled else tuple(_clean_reviewers(spec)),
                    max_review_rounds=spec.get("max_review_rounds"),
                )
                for spec in tasks
            ]
        )
        if not result.ok:
            return ToolOutput(success=False, error=result.reason)

        # The owner and landing status are the whole point of the scheduled
        # contract: the leader must tell "starts now" from "waiting on
        # dependencies" without a follow-up view_task. Autonomous-effective
        # batches carry no assignee and render like the claimable variant.
        briefs = [{**task.brief(), "assignee": task.assignee} for task in result.tasks]
        if len(briefs) == 1:
            return ToolOutput(success=True, data=briefs[0])
        return ToolOutput(success=True, data={"tasks": briefs, "count": len(briefs)})

    @staticmethod
    def _task_line(task: dict) -> str:
        line = f"task_id={task['task_id']} title={task['title']}"
        if task.get("assignee"):
            line += f" {_owner_phrase(task)}"
        return line

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Operation failed"
        d = output.data
        if "task_id" in d and "title" in d:
            return f"Task created: {self._task_line(d)}"
        lines = [self._task_line(task) for task in d.get("tasks", [])]
        lines.append(f"Created {d['count']}")
        return "\n".join(lines)


class ViewTaskToolV2(TeamTool):
    """Unified task viewing tool (V2).

    Explicit action enum instead of implicit param-based dispatch.
    """

    def __init__(self, task_manager: TeamTaskManager, t: Translator):
        super().__init__(
            ToolCard(
                id="team.view_task",
                name="view_task",
                description=t("view_task"),
            )
        )
        self.task_manager = task_manager
        self.card.input_params = {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["get", "list", "claimable", "in_review"],
                    "description": t("view_task", "action"),
                },
                "task_id": {"type": "string", "description": t("view_task", "task_id")},
                "status": {
                    "type": "string",
                    "description": t("view_task", "status"),
                },
            },
            "required": [],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        action = inputs.get("action", "list")

        if action == "get":
            task_id = inputs.get("task_id")
            if not task_id:
                return ToolOutput(success=False, error="task_id required for get action")
            detail = await self.task_manager.get_task_detail(task_id=task_id)
            if detail:
                return ToolOutput(success=True, data=detail.model_dump(exclude_none=True))
            return ToolOutput(success=False, error="Task not found")

        if action == "in_review":
            # Tasks this member must verify (it is a reviewer and status=IN_REVIEW).
            result = await self.task_manager.list_review_tasks(self.task_manager.member_name)
        elif action == "claimable":
            result = await self.task_manager.list_tasks_with_deps(
                status=TaskStatus.PENDING.value,
            )
        else:
            result = await self.task_manager.list_tasks_with_deps(
                status=inputs.get("status"),
            )

        return ToolOutput(success=True, data=result.model_dump())

    def map_result(self, output: ToolOutput) -> str:
        """Map view_task result — tiered output by action.

        Both tiers render the task's last-transition time as ``<absolute
        local time> (<relative diff>)`` so the model can tell how long a
        task has been sitting in its current status.
        """
        if not output.success:
            return output.error or "Task not found"
        d = output.data
        now_ms = get_current_time()
        # Detail view (get action) — mirrors TaskGetTool
        if "content" in d:
            lines = [
                f"Task #{d['task_id']}: {d['title']}",
                f"Status: {d['status']}",
                f"Content: {d['content']}",
            ]
            if d.get("assignee"):
                lines.append(f"Assignee: {d['assignee']}")
            if d.get("reviewer"):
                lines.append(f"Reviewers: {', '.join(d['reviewer'])}")
            if d.get("updated_at") is not None:
                lines.append(f"Updated: {format_time_context(d['updated_at'], now_ms)}")
            if d.get("blocked_by"):
                lines.append(f"Blocked by: {', '.join(f'#{tid}' for tid in d['blocked_by'])}")
            if d.get("blocks"):
                lines.append(f"Blocks: {', '.join(f'#{tid}' for tid in d['blocks'])}")
            return "\n".join(lines)
        # List view (list/claimable action) — mirrors TaskListTool
        tasks = d.get("tasks", [])
        if not tasks:
            return "No tasks found"
        lines = []
        for task in tasks:
            parts = [f"#{task['task_id']} [{task['status']}] {task['title']}"]
            if task.get("assignee"):
                parts.append(f"({task['assignee']})")
            if task.get("updated_at") is not None:
                parts.append(f"({format_time_context(task['updated_at'], now_ms)})")
            if task.get("blocked_by"):
                parts.append(f"[blocked by {', '.join(f'#{tid}' for tid in task['blocked_by'])}]")
            lines.append(" ".join(parts))
        return "\n".join(lines)


class UpdateTaskTool(TeamTool):
    """Update task content or cancel tasks (Leader only).

    The verify gate is a gated capability, exactly like ``spawn_teammate``'s
    fork properties: under autonomous dispatch the ``reviewer`` /
    ``max_review_rounds`` properties are absent from the schema *and* the
    verify-gate section is dropped from the description, both off the one
    signal. The gate is not cosmetic — that mode has no scheduling runtime to
    summon reviewers (``TeamScheduler`` is built only for a scheduled-dispatch
    leader), so a task pushed into ``IN_REVIEW`` there would stall forever and
    hold its assignee's only active-task slot.
    """

    #: Verify-gate properties and the description slot documenting them.
    #: Schema and prose are gated together — the model must never read about
    #: an argument it has no way to pass.
    _REVIEW_PARAMS = ("reviewer", "max_review_rounds")
    _REVIEW_SLOT = "update_task_verify_gate"

    def __init__(self, agent_team: TeamBackend, t: Translator, *, dispatch_mode: str = "autonomous"):
        """Build the update_task tool.

        Args:
            agent_team: Backend the tool mutates tasks through.
            t: Locale-bound translator.
            dispatch_mode: How tasks reach members. ``"scheduled"`` wires the
                verify-gate properties and their description section;
                ``"autonomous"`` drops both as one unit.
        """
        review_enabled = dispatch_mode == "scheduled"
        super().__init__(
            ToolCard(
                id="team.update_task",
                name="update_task",
                description=t("update_task", omit=None if review_enabled else frozenset({self._REVIEW_SLOT})),
            )
        )
        self.agent_team = agent_team
        self.task_manager = agent_team.task_manager
        self.t = t
        self._review_enabled = review_enabled
        properties: dict[str, Any] = {
            "task_id": {"type": "string", "description": t("update_task", "task_id")},
            "status": {
                "type": "string",
                "enum": ["cancelled"],
                "description": t("update_task", "status"),
            },
            "title": {"type": "string", "description": t("update_task", "title")},
            "content": {"type": "string", "description": t("update_task", "content")},
            "assignee": {"type": "string", "description": t("update_task", "assignee")},
            "add_blocked_by": {
                "type": "array",
                "items": {"type": "string"},
                "description": t("update_task", "add_blocked_by"),
            },
        }
        if review_enabled:
            properties.update({
                # reviewer 结构化对象数组，与 create_task 一致
                "reviewer": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["verifier", "inspector", "challenger"],
                                "description": t("update_task", "reviewer_type"),
                            },
                            "instruction": {"type": "string", "description": t("update_task", "reviewer_instruction")},
                        },
                        "required": ["type"],
                    },
                    "description": t("update_task", "reviewer"),
                },
                "max_review_rounds": {
                    "type": "integer",
                    "minimum": 1,
                    "description": t("update_task", "max_review_rounds"),
                },
            })
        self.card.input_params = {
            "type": "object",
            "properties": properties,
            "required": ["task_id"],
        }

    async def _is_human_agent_locked(self, task) -> bool:
        """Whether a task is held by a human-agent member still on the team,
        and therefore leader-immutable.

        The leader may not cancel, reassign or edit such tasks — only the
        human collaborator can release them (by completing them). While that
        human is on the team, the leader's only recourse is send_message
        nudges.

        The lock keys on a *live* human, not on the bare role: once the
        leader shuts the member down, the task it still holds becomes an
        ordinary orphan the leader may cancel or reassign, exactly like the
        leftovers of a shut-down teammate. Keying on the role alone would
        strand the task of every human the leader ever removes — nobody left
        to complete it, and the leader forbidden from touching it.
        """
        active = (
            TaskStatus.PLANNING.value,
            TaskStatus.IN_PROGRESS.value,
            TaskStatus.IN_REVIEW.value,
        )
        return await self.agent_team.is_live_human_agent(task.assignee) and task.status in active

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        task_id = inputs.get("task_id")
        if not task_id:
            return ToolOutput(success=False, error="'task_id' is required")

        # Second-layer enforcement of the verify-gate gate, for arguments coming
        # from an MCP client, which calls ``invoke`` directly without validating
        # against ``input_params``. Rejected loudly rather than stripped: a
        # silently dropped reviewer reads as "verification is on" to the leader,
        # and it would go on waiting for a verdict that is never coming.
        if not self._review_enabled:
            passed = [key for key in self._REVIEW_PARAMS if inputs.get(key) is not None]
            if passed:
                return ToolOutput(
                    success=False,
                    error=(
                        f"Cannot use {', '.join(passed)}: the verify gate does not exist under "
                        "autonomous dispatch — there is no scheduling runtime to summon reviewers, "
                        "so the task would stall in 'in_review' forever. Write the acceptance "
                        "criteria into the task content and review the result yourself, or run the "
                        "team in scheduled dispatch mode."
                    ),
                )

        status = inputs.get("status")
        title = inputs.get("title")
        content = inputs.get("content")
        assignee = inputs.get("assignee")
        reviewer = inputs.get("reviewer")
        max_review_rounds = inputs.get("max_review_rounds")
        add_blocked_by = inputs.get("add_blocked_by")

        # cancel_all: task_id="*" + status="cancelled"
        if task_id == "*" and status == "cancelled":
            # Each cancelled task fires a targeted TASK_CANCELLED carrying its
            # assignee, so every affected member is steered off its task via
            # on_task_cancelled — no member-wide cancel needed. Preserve every
            # task claimed by a human still on the team (empty set is treated
            # as None); a departed human's leftovers are cancelled like any
            # other member's.
            skip = set(await self.agent_team.live_human_agent_names())
            count = await self.agent_team.cancel_all_tasks(skip_assignees=skip or None)
            return ToolOutput(success=True, data={"cancelled_count": count})

        task = await self.task_manager.get(task_id)
        if not task:
            return ToolOutput(success=False, error="Task not found")

        if status == "completed":
            return ToolOutput(
                success=False,
                error=(
                    "update_task cannot mark a task completed. Assign or reassign the task to a non-leader member "
                    "with update_task(task_id=..., assignee=...), then that member must complete it with their "
                    "task-completion tool."
                ),
            )
        if status and status != "cancelled":
            return ToolOutput(
                success=False,
                error=(
                    f"Invalid status {status!r}; omit status unless you want to cancel the task "
                    "with status='cancelled'"
                ),
            )

        # Cancel single task
        if status == "cancelled":
            if await self._is_human_agent_locked(task):
                return ToolOutput(
                    success=False,
                    error=self.t(
                        "update_task",
                        "error_human_agent_locked_cancel",
                        task_id=task_id,
                    ),
                )
            success = await self.agent_team.cancel_task(task_id=task_id)
            if not success:
                return ToolOutput(success=False, error="Failed to cancel task")
            return ToolOutput(success=True, data={"task_id": task_id, "status": "cancelled"})

        # Collect all field updates in one pass
        updated: list[str] = []

        # Content update (title and/or content). A human-agent-claimed task is
        # leader-immutable (same rule as cancel / reassign); refuse the edit.
        if title or content:
            if await self._is_human_agent_locked(task):
                return ToolOutput(
                    success=False,
                    error=self.t("update_task", "error_human_agent_locked_edit", task_id=task_id),
                )
            result = await self.task_manager.update_task(task_id, title=title, content=content)
            if not result.ok:
                return ToolOutput(success=False, error=result.reason)
            if title:
                updated.append("title")
            if content:
                updated.append("content")

        # Assign task to member. When the task is already owned by a
        # different member, treat this as a leader-driven reassignment: the
        # assignee is swapped in place and the status is preserved, so an
        # assigned-but-not-yet-started task (the scheduled mode's resting
        # state) stays waiting for its owner rather than being started or
        # released. The former assignee is told via a targeted TASK_REVOKED
        # event (not a member-wide cancel), so only this one task moves — its
        # other claims and in-flight round survive. Same-member is idempotent.
        if assignee:
            # One active task per member: reject before any state change so a
            # rejected assign never disturbs the current owner or this task.
            busy_task_id = await self.task_manager.get_other_active_task_id(assignee, task_id)
            if busy_task_id:
                return ToolOutput(
                    success=False,
                    error=(
                        f"Member '{assignee}' already has an active task #{busy_task_id}; "
                        f"wait for it to complete before assigning another."
                    ),
                )
            if task.assignee and task.assignee != assignee:
                if await self._is_human_agent_locked(task):
                    return ToolOutput(
                        success=False,
                        error=self.t(
                            "update_task",
                            "error_human_agent_locked_reassign",
                            task_id=task_id,
                            new_assignee=assignee,
                        ),
                    )
                assign_result = await self.task_manager.reassign(task_id, assignee)
            else:
                assign_result = await self.task_manager.assign(task_id, assignee)
            if not assign_result.ok:
                return ToolOutput(success=False, error=assign_result.reason)
            updated.append("assignee")

        # Set / clear verify-gate reviewers. A leader may (re)assign reviewers
        # at any status; an empty list clears the gate. Reviewers are structured
        # objects (type + reviewer_id + description); none may be the task's
        # author (no self-verification).
        if reviewer is not None:
            # 用 _clean_reviewers 将输入（可能是对象数组或旧格式字符串数组）统一为结构化 dict 列表
            reviewer_entries = _clean_reviewers({"reviewer": reviewer})
            if not self.agent_team.task_verification_enabled():
                reviewer_entries = []  # silently stripped when verification is disabled
            current_assignee = (assignee or task.assignee or "").strip()
            for entry in reviewer_entries:
                rid = entry.get("reviewer_id", "")
                if current_assignee and rid == current_assignee:
                    return ToolOutput(
                        success=False,
                        error=f"Reviewer '{rid}' cannot review their own task (they are the assignee)",
                    )
            # set_reviewer 现在是 list[dict]，内部 json.dumps 后写入 DB
            reviewer_result = await self.task_manager.set_reviewer(task_id, reviewer_entries)
            if not reviewer_result.ok:
                return ToolOutput(success=False, error=reviewer_result.reason)
            updated.append("reviewer")

        # Cap the verify-gate rework loop (F_62). Meaningful only for a
        # reviewed task — the ceiling counts review rounds, so a task with
        # no reviewers (current or being set in this same call) has nothing
        # to count.
        if max_review_rounds is not None:
            if not isinstance(max_review_rounds, int) or isinstance(max_review_rounds, bool) or max_review_rounds < 1:
                return ToolOutput(success=False, error="'max_review_rounds' must be an integer >= 1")
            # effective_reviewers 检查是否有 reviewer：优先取本次调用的输入，否则取 task 已有的
            # _clean_reviewers 返回 list[dict]，len > 0 表示有 reviewer
            effective_reviewers = (
                _clean_reviewers({"reviewer": reviewer}) if reviewer is not None else task.reviewers()
            )
            if not effective_reviewers:
                return ToolOutput(
                    success=False,
                    error=(
                        "'max_review_rounds' only applies to reviewed tasks — "
                        "set 'reviewer' as well, or drop the round ceiling"
                    ),
                )
            rounds_result = await self.task_manager.set_max_review_rounds(task_id, max_review_rounds)
            if not rounds_result.ok:
                return ToolOutput(success=False, error=rounds_result.reason)
            updated.append("max_review_rounds")

        # Add dependencies (blocked_by edges)
        if add_blocked_by:
            deps_result = await self.task_manager.add_dependencies(task_id, add_blocked_by)
            if not deps_result.ok:
                return ToolOutput(success=False, error=deps_result.reason)
            updated.append("blocked_by")

        if not updated:
            return ToolOutput(
                success=False,
                error=(
                    "No update specified — provide status, title, content, assignee, "
                    "reviewer, max_review_rounds, or add_blocked_by"
                ),
            )

        return ToolOutput(
            success=True,
            data={
                "task_id": task_id,
                "status": "updated",
                "updated_fields": updated,
            },
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Operation failed"
        d = output.data
        if "cancelled_count" in d:
            return f"Cancelled {d['cancelled_count']} tasks"
        return f"Task #{d['task_id']} {d['status']}"


class SubmitPlanTool(TeamTool):
    """Submit an execution plan for a plan-mode task."""

    def __init__(
        self,
        task_manager: TeamTaskManager,
        t: Translator,
        *,
        name: str = "submit_plan",
        tool_id: str = "team.submit_plan",
    ):
        super().__init__(
            ToolCard(
                id=tool_id,
                name=name,
                description=t("submit_plan"),
            )
        )
        self.task_manager = task_manager
        self.card.input_params = {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": t("submit_plan", "task_id")},
                "plan_id": {"type": "string", "description": t("submit_plan", "plan_id")},
                "plan_path": {"type": "string", "description": t("submit_plan", "plan_path")},
            },
            "required": ["task_id", "plan_path"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        result = await self.task_manager.submit_plan(
            task_id=inputs.get("task_id"),
            plan_id=inputs.get("plan_id"),
            plan_path=inputs.get("plan_path") or "",
        )
        return ToolOutput(
            success=bool(result.get("success")),
            data=result,
            error=None if result.get("success") else result.get("message", "Failed to submit member plan"),
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to submit member plan"
        d = output.data
        return (
            f"Member plan submitted: task_id={d.get('task_id')} plan_id={d.get('plan_id')} "
            f"status={d.get('status')} "
            f"member_plan_md={d.get('member_plan_md')}"
        )


class ClaimTaskTool(TeamTool):
    """Claim or complete a task (Teammate only)."""

    def __init__(self, task_manager: TeamTaskManager, t: Translator):
        super().__init__(
            ToolCard(
                id="team.claim_task",
                name="claim_task",
                description=t("claim_task"),
            )
        )
        self.task_manager = task_manager
        self.card.input_params = {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": t("claim_task", "task_id")},
                "status": {
                    "type": "string",
                    "enum": ["claimed", "completed"],
                    "description": t("claim_task", "status"),
                },
            },
            "required": ["task_id", "status"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        task_id = inputs.get("task_id")
        status = inputs.get("status")
        if not task_id:
            return ToolOutput(success=False, error="'task_id' is required")

        task = await self.task_manager.get(task_id)
        if not task:
            return ToolOutput(success=False, error="Task not found")

        if status == "claimed":
            # One active task per member: refuse a second concurrent claim so a
            # teammate finishes its current task before picking up another.
            busy_task_id = await self.task_manager.get_other_active_task_id(self.task_manager.member_name, task_id)
            if busy_task_id:
                return ToolOutput(
                    success=False,
                    error=(
                        f"You already have an active task #{busy_task_id}; "
                        f"complete it before claiming another."
                    ),
                )
            claim_result = await self.task_manager.claim(task_id=task_id)
            if not claim_result.ok:
                return ToolOutput(success=False, error=claim_result.reason)
            status_change = {"from": task.status, "to": TaskStatus.IN_PROGRESS.value}

        elif status == "completed":
            complete_result = await self.task_manager.complete(task_id=task_id)
            if not complete_result.ok:
                return ToolOutput(success=False, error=complete_result.reason)
            # A reviewer-carrying task enters IN_REVIEW instead of completing.
            settled = await self.task_manager.get(task_id)
            to_status = (
                TaskStatus.IN_REVIEW.value
                if settled and settled.status == TaskStatus.IN_REVIEW.value
                else TaskStatus.COMPLETED.value
            )
            status_change = {"from": task.status, "to": to_status}

        else:
            return ToolOutput(success=False, error=f"Invalid status: {status}")

        return ToolOutput(
            success=True,
            data={
                "task_id": task_id,
                "updated_fields": ["status"],
                "status_change": status_change,
            },
        )

    def map_result(self, output: ToolOutput) -> str:
        """Map claim_task result with behavior guidance on completion."""
        if not output.success:
            return output.error or "Task not found"
        d = output.data
        sc = d["status_change"]
        result = f"Task #{d['task_id']} {sc['from']} → {sc['to']}"
        if sc["to"] == TaskStatus.COMPLETED.value:
            result += "\n\nTask completed. Call view_task now to find your next available task."
        return result


class MemberCompleteTaskTool(TeamTool):
    """Complete a task whose ``assignee`` is the calling member.

    Self-only by design: the tool refuses any task whose ``assignee``
    differs from the caller's ``member_name``. Distinct from
    ``ClaimTaskTool`` (which couples claim and complete and is
    teammate-only) and from leader's ``UpdateTaskTool`` (which manages
    the team-wide task graph). Wired into ``HUMAN_AGENT_TOOLS`` so the
    user's avatar can mark its leader-assigned tasks as done without
    inheriting any of leader's coordination authority, and into the
    scheduled-dispatch member set, where no member claims its own work.

    Behaviour is identical either way; only the description differs, so the
    caller picks the ``desc_key`` instead of the class.
    """

    def __init__(self, task_manager: TeamTaskManager, t: Translator, *, desc_key: str = "member_complete_task"):
        super().__init__(
            ToolCard(
                id="team.member_complete_task",
                name="member_complete_task",
                description=t(desc_key),
            )
        )
        self.task_manager = task_manager
        self.card.input_params = {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": t("member_complete_task", "task_id"),
                },
                "note": {
                    "type": "string",
                    "description": t("member_complete_task", "note"),
                },
            },
            "required": ["task_id"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        task_id = (inputs.get("task_id") or "").strip()
        if not task_id:
            return ToolOutput(success=False, error="'task_id' is required")

        try:
            task = await self.task_manager.get(task_id)
        except Exception as e:
            team_logger.error("member_complete_task: get(%s) failed: %s", task_id, e)
            return ToolOutput(success=False, error=f"Internal error: {e}")
        if not task:
            return ToolOutput(success=False, error=f"Task '{task_id}' not found")

        caller = self.task_manager.member_name
        if task.assignee != caller:
            return ToolOutput(
                success=False,
                error=(
                    f"Task '{task_id}' is assigned to "
                    f"'{task.assignee or '<unassigned>'}', not '{caller}'; "
                    "you can only complete tasks assigned to yourself"
                ),
            )

        try:
            result = await self.task_manager.complete(task_id=task_id)
        except Exception as e:
            team_logger.error("member_complete_task: complete(%s) failed: %s", task_id, e)
            return ToolOutput(success=False, error=f"Internal error: {e}")
        if not result.ok:
            return ToolOutput(success=False, error=result.reason)

        # The task carries reviewers -> it entered the verify gate (IN_REVIEW)
        # rather than completing. Report the true outcome so the author knows a
        # reviewer decision is pending.
        settled = await self.task_manager.get(task_id)
        outcome = "in_review" if settled and settled.status == TaskStatus.IN_REVIEW.value else "completed"

        note = (inputs.get("note") or "").strip() or None
        return ToolOutput(
            success=True,
            data={
                "task_id": task_id,
                "status": outcome,
                "note": note,
            },
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to complete task"
        d = output.data
        if d.get("status") == "in_review":
            line = f"Task #{d['task_id']} submitted for review — awaiting a reviewer's verdict"
        else:
            line = f"Task #{d['task_id']} completed"
        if d.get("note"):
            line += f" (note: {d['note']})"
        return line


class VerifyTaskTool(TeamTool):
    """Reviewer verdict on a task in the verify gate (reviewer only).

    A task carrying reviewers enters ``IN_REVIEW`` when its author completes.
    A reviewer named on the task calls this with a ``pass`` / ``fail``
    decision. What the verdict does is the team's dispatch-mode policy
    (selected in the manager, described by the per-mode ``desc_key``):
    autonomous applies the first verdict directly; scheduled records a vote
    and the leader-side scheduler settles the tally (F_62). The manager
    enforces that the caller is a reviewer of the task and not its author.
    """

    def __init__(self, task_manager: TeamTaskManager, t: Translator, *, desc_key: str = "verify_task"):
        super().__init__(
            ToolCard(
                id="team.verify_task",
                name="verify_task",
                description=t(desc_key),
            )
        )
        self.task_manager = task_manager
        self.card.input_params = {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": t("verify_task", "task_id")},
                "decision": {
                    "type": "string",
                    "description": t("verify_task", "decision"),
                },
                "feedback": {"type": "string", "description": t("verify_task", "feedback")},
            },
            "required": ["task_id", "decision"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        task_id = (inputs.get("task_id") or "").strip()
        if not task_id:
            return ToolOutput(success=False, error="'task_id' is required")
        decision = (inputs.get("decision") or "").strip()
        feedback = (inputs.get("feedback") or "").strip()

        result = await self.task_manager.verify_task(task_id, decision, feedback)
        if not result.ok:
            return ToolOutput(success=False, error=result.reason)
        data: dict[str, Any] = {"task_id": task_id, "decision": decision.lower(), "feedback": feedback or None}
        if result.data:
            # Scheduled dispatch (F_62): the call recorded a vote, no verdict
            # yet — surface the tally so the reviewer knows where the round
            # stands without a follow-up view_task.
            data["tally"] = result.data
        return ToolOutput(success=True, data=data)

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to verify task"
        d = output.data
        tally = d.get("tally")
        if tally:
            return (
                f"Vote recorded for task #{d['task_id']}: {d['decision']} "
                f"(round {tally['review_round']}: pass {tally['pass_count']}, fail {tally['fail_count']}, "
                f"{tally['reviewer_count']} reviewer(s)). The verdict settles once the tally decides — "
                f"no further action needed from you."
            )
        if d["decision"] == "pass":
            return f"Task #{d['task_id']} verified and completed."
        line = f"Task #{d['task_id']} sent back for revision"
        if d.get("feedback"):
            line += f" (feedback: {d['feedback']})"
        return line
