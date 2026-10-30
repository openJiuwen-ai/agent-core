# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The attribution tools bound to one run's runtime.

Search/render logic lives in core pure functions; these tools only route to
the host runtime and keep the reporting contracts (atomicity, overwrite
semantics, closure check).
"""

from __future__ import annotations

import json
from typing import Any

from openjiuwen.agent_evolving.attribution.agent import AttributionAgentRuntime
from openjiuwen.agent_evolving.attribution.closure import completion_state
from openjiuwen.agent_evolving.attribution.model import AttributionEntry, DiagnosticSignal, SignalSource
from openjiuwen.agent_evolving.attribution.nodes import (
    FIELD_WINDOW_LIMIT,
    TrajectoryNode,
    render_overview,
    render_summary,
)
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput

_TOOL_ID_PREFIX = "attribution"


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required}


def _text_prop(cn: str, en: str) -> dict[str, Any]:
    return {"type": "string", "description": f"{cn} / {en}"}


def split_node_id(node_id: str) -> tuple[str, str] | None:
    """Split ``<subagent_key>:<seq>`` from the right (keys may contain colons)."""
    text = str(node_id or "").strip()
    if not text or ":" not in text:
        return None
    key, _, seq = text.rpartition(":")
    if not key or not seq.isdigit():
        return None
    return key, seq


def same_subagent(from_id: str, to_id: str) -> bool:
    """Whether two node ids belong to the same subagent."""
    left, right = split_node_id(from_id), split_node_id(to_id)
    if left is None or right is None:
        return False
    return left[0] == right[0]


class _AttributionTool(Tool):
    tool_name = ""
    tool_id = ""
    description = ""
    input_schema: dict[str, Any] = {}

    def __init__(self, runtime: AttributionAgentRuntime, signals: list[DiagnosticSignal]) -> None:
        self._runtime = runtime
        self._signals = {signal.signal_id: signal for signal in signals}
        self._signal_ids = set(self._signals)
        super().__init__(
            ToolCard(
                id=f"{_TOOL_ID_PREFIX}.{self.tool_name}",
                name=self.tool_name,
                description=self.description,
                input_params=self.input_schema,
                stateless=False,
                idempotent=True,
            )
        )

    async def stream(self, inputs: dict[str, Any], **kwargs: Any) -> Any:  # type: ignore[override]
        del inputs, kwargs
        if False:  # pragma: no cover
            yield ToolOutput(success=False)

    @staticmethod
    def ok(payload: dict[str, Any]) -> ToolOutput:
        return ToolOutput(success=True, data={"content": json.dumps(payload, ensure_ascii=False)})

    @staticmethod
    def fail(error: str) -> ToolOutput:
        return ToolOutput(success=False, error=error)

    def _node_missing(self, node_id: str) -> ToolOutput:
        return self.ok(
            {
                "error": (
                    f"节点不存在：{node_id}。请核对 node_id（照抄工具返回）。/ Node not found: "
                    f"{node_id}. Verify the node id (copy verbatim from tool output)."
                )
            }
        )


class KeywordRegTool(_AttributionTool):
    """正则检索选中范围内全部 subagent 的轨迹节点（re.search、忽略大小写）。

    / Regex search (re.search, case-insensitive) over all subjects' nodes in
    the selected range, grouped per subject.
    """

    tool_name = "keyword_reg"
    tool_id = "KeywordRegTool"
    description = (
        "在选中范围内全部 subagent 的轨迹节点中做正则检索（re.search 语义、忽略大小写），"
        "按 subagent 分组返回；全局命中 <30 返回命中节点（node_id、内容片段、上下文），≥30 仅返回"
        "各 subagent 命中数量并提示收紧正则。/ Regex search (re.search, case-insensitive) over "
        "all subjects' nodes in the selected range, grouped per subject; <30 global hits return "
        "matched nodes (node_id, snippet, context), >=30 returns per-subject counts only — "
        "tighten the regex."
    )
    input_schema = _schema(
        {"keyword": _text_prop("正则表达式（Python re 语法）", "Regex (Python re syntax)")}, ["keyword"]
    )

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:  # type: ignore[override]
        del kwargs
        keyword = str(inputs.get("keyword") or "")
        if not keyword:
            return self.fail("keyword must be a non-empty string")
        return self.ok(self._runtime.search_keyword(keyword))


class ViewTrajectoryTool(_AttributionTool):
    """查看某个 subagent 选中范围内的全部轨迹节点。"""

    tool_name = "view_trajectory"
    tool_id = "ViewTrajectoryTool"
    description = (
        "按顺序查看某个 subagent 选中范围内的全部轨迹节点；>500 节点返回整体级（时间坐标 + "
        "user/error/markers 规则锚点），≤500 返回摘要级（逐行截断内容）。/ View all nodes of one "
        "subject's selected range in order; >500 nodes returns the overview level (coordinates + "
        "rule anchors), <=500 the summary level (per-line truncated content)."
    )
    input_schema = _schema(
        {
            "subagent_id": _text_prop(
                "目标 subagent 的 id，取自 subagent 列表", "Target subagent id, from the subagent list"
            )
        },
        ["subagent_id"],
    )

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:  # type: ignore[override]
        del kwargs
        subagent_id = str(inputs.get("subagent_id") or "")
        if not subagent_id:
            return self.fail("subagent_id must be a non-empty string")
        return self.ok(self._runtime.render_trajectory(subagent_id))


class ViewPartialTrajectoryTool(_AttributionTool):
    """查看同一 subagent 内两端节点之间的轨迹片段。"""

    tool_name = "view_partial_trajectory"
    tool_id = "ViewPartialTrajectoryTool"
    description = (
        "查看同一 subagent 内 [node_id_from, node_id_end] 之间（含两端）的轨迹节点，按节点顺序返回；"
        "两端入参顺序不限；跨 subagent 返回错误。/ View nodes between node_id_from and node_id_end "
        "(both inclusive, either order) within one subject, in node order; cross-subject input "
        "is an error."
    )
    input_schema = _schema(
        {
            "node_id_from": _text_prop("片段一端的节点 ID（照抄工具返回）", "Node id of one endpoint (copy verbatim)"),
            "node_id_end": _text_prop(
                "片段另一端的节点 ID（照抄工具返回）", "Node id of the other endpoint (copy verbatim)"
            ),
        },
        ["node_id_from", "node_id_end"],
    )

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:  # type: ignore[override]
        del kwargs
        from_id = str(inputs.get("node_id_from") or "")
        to_id = str(inputs.get("node_id_end") or "")
        if split_node_id(from_id) is None or split_node_id(to_id) is None:
            return self.fail("node_id_from/node_id_end must be valid node ids")
        if not same_subagent(from_id, to_id):
            return self.ok(
                {
                    "error": (
                        f"start_node_id 与 end_node_id 属于不同 subagent（{from_id} vs {to_id}），"
                        f"不可成对定位；请分别成条提交。/ start_node_id and end_node_id belong to "
                        f"different subagents ({from_id} vs {to_id}); paired location is not "
                        "allowed — submit separate entries."
                    )
                }
            )
        return self.ok(self._runtime.render_partial_trajectory(from_id, to_id))


class ViewNodeDetailTool(_AttributionTool):
    """查看单个节点的完整详情。"""

    tool_name = "view_node_detail"
    tool_id = "ViewNodeDetailTool"
    description = (
        "查看单个节点的完整详情（各字段最多 1000 字符，超长标记 [truncated]，含时间戳与来源 ID）。/ "
        "View the full detail of one node (fields capped at 1000 chars with [truncated] marks, "
        "incl. timestamps and source ids)."
    )
    input_schema = _schema(
        {"node_id": _text_prop("目标节点 ID（照抄工具返回）", "Target node id (copy verbatim from tool output)")},
        ["node_id"],
    )

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:  # type: ignore[override]
        del kwargs
        node_id = str(inputs.get("node_id") or "")
        if split_node_id(node_id) is None:
            return self.fail("node_id must be a valid node id")
        result = self._runtime.render_node_detail(node_id)
        if "error" in result:
            return self._node_missing(node_id)
        return self.ok(result)


class ViewNodeFieldTool(_AttributionTool):
    """按字符窗口续读某节点某字段（游标式）。"""

    tool_name = "view_node_field"
    tool_id = "ViewNodeFieldTool"
    description = (
        "按字符窗口读取某节点某字段的一段内容（游标式）：field 仅限 content 或 tool_calls"
        "（tool_calls 为 JSON 文本，切片处可能不完整，不影响阅读）；start 为字符起点（缺省 0）；"
        "limit 为窗口上限（缺省 50000、最大 50000）；返回 {text, start, end, total_chars, "
        "has_more}——续读时把上次的 end 作为下次的 start 照抄，直到 has_more=false；start 超出"
        "字段总长时返回空 text 且 has_more=false（非错误）。仅当该节点内容对归因关键、且"
        "view_node_detail 的 [truncated]/field_lengths 表明仍有剩余时才调用，避免浪费预算。/ "
        "Read one character window of one node field (cursor-style): field is only content or "
        "tool_calls (tool_calls is JSON text; the slice may be structurally incomplete, which is "
        "fine for reading); start is the character offset (default 0); limit is the window cap "
        "(default 50000, max 50000); returns {text, start, end, total_chars, has_more} — to "
        "continue, copy the previous end as the next start until has_more=false; a start at or "
        "past the field length returns empty text with has_more=false (not an error). Call only "
        "when the node matters for attribution and view_node_detail's [truncated]/field_lengths "
        "show remaining content; don't waste budget."
    )
    input_schema = _schema(
        {
            "node_id": _text_prop("目标节点 ID（照抄工具返回）", "Target node id (copy verbatim from tool output)"),
            "field": _text_prop("字段名：content 或 tool_calls", "Field name: content or tool_calls"),
            "start": {
                "type": "integer",
                "description": (
                    "字符起点（0-based，缺省 0；续读照抄上次返回的 end）/ character offset "
                    "(0-based, default 0; copy the previous end to continue)"
                ),
            },
            "limit": {
                "type": "integer",
                "description": "窗口上限，缺省 50000、最大 50000 / window cap, default 50000, max 50000",
            },
        },
        ["node_id", "field"],
    )

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:  # type: ignore[override]
        del kwargs
        node_id = str(inputs.get("node_id") or "")
        field = str(inputs.get("field") or "")
        if split_node_id(node_id) is None:
            return self.fail("node_id must be a valid node id")
        if field not in ("content", "tool_calls"):
            return self.fail("field must be one of: content, tool_calls")
        start = inputs.get("start")
        if start is not None and (not isinstance(start, int) or start < 0):
            return self.fail("start must be a non-negative integer")
        limit = inputs.get("limit")
        if limit is not None and (not isinstance(limit, int) or limit < 1):
            return self.fail("limit must be a positive integer")
        effective_start = int(start) if start is not None else 0
        effective_limit = int(limit) if limit is not None else FIELD_WINDOW_LIMIT
        if effective_limit > FIELD_WINDOW_LIMIT:
            return self.fail(f"limit must not exceed {FIELD_WINDOW_LIMIT}")
        result = self._runtime.render_node_field(node_id, field, effective_start, effective_limit)
        if "error" in result:
            return self._node_missing(node_id)
        return self.ok(result)


class _SubmitCauseTool(_AttributionTool):
    """Base for the three directed cause tools: one signal, atomic entries."""

    record_method = ""
    error_source = ""

    def _check_signal(self, signal_id: str) -> str | None:
        """Return an error text when the signal does not fit this tool."""
        return None

    def _record(self, signal_id: str, entries: list[dict[str, Any]]) -> str:
        return getattr(self._runtime, self.record_method)(signal_id, entries)

    def _extra_entry_error(self, entry: dict[str, Any]) -> str | None:
        """Return an error text for tool-specific entry fields."""
        return None

    def _extra_entry_fields(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Extra fields this tool stores on an entry."""
        return {}

    def _entry_error(self, signal_id: str, results: Any) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
        if not isinstance(results, list) or not results:
            return None, {"status": "error", "signal_id": signal_id, "error": "results must be a non-empty list"}
        entries: list[dict[str, Any]] = []
        for entry in results:
            if not isinstance(entry, dict):
                return None, {"status": "error", "signal_id": signal_id, "error": "every entry must be an object"}
            start_node_id = str(entry.get("start_node_id") or "")
            end_node_id = str(entry.get("end_node_id") or "") or None
            if split_node_id(start_node_id) is None:
                return None, {
                    "status": "error",
                    "signal_id": signal_id,
                    "error": f"invalid start_node_id: {start_node_id}",
                }
            if end_node_id is not None:
                if split_node_id(end_node_id) is None:
                    return None, {
                        "status": "error",
                        "signal_id": signal_id,
                        "error": f"invalid end_node_id: {end_node_id}",
                    }
                if not same_subagent(start_node_id, end_node_id):
                    return None, {
                        "status": "error",
                        "signal_id": signal_id,
                        "error": (
                            f"start_node_id 与 end_node_id 属于不同 subagent（{start_node_id} vs "
                            f"{end_node_id}），不可成对定位；请分别成条提交。/ start_node_id and "
                            f"end_node_id belong to different subagents ({start_node_id} vs "
                            f"{end_node_id}); paired location is not allowed — submit separate entries."
                        ),
                    }
            if "error" in self._runtime.render_node_detail(start_node_id):
                return None, {
                    "status": "error",
                    "signal_id": signal_id,
                    "error": (
                        f"节点不存在：{start_node_id}。/ Node not found: {start_node_id}. "
                        "Any invalid entry fails the whole call; fix it and resubmit the complete list."
                    ),
                }
            if end_node_id is not None and "error" in self._runtime.render_node_detail(end_node_id):
                return None, {
                    "status": "error",
                    "signal_id": signal_id,
                    "error": (
                        f"节点不存在：{end_node_id}。/ Node not found: {end_node_id}. "
                        "Any invalid entry fails the whole call; fix it and resubmit the complete list."
                    ),
                }
            extra_error = self._extra_entry_error(entry)
            if extra_error is not None:
                return None, {"status": "error", "signal_id": signal_id, "error": extra_error}
            stored = AttributionEntry(
                text_md=str(entry.get("text_md") or ""),
                start_node_id=start_node_id,
                end_node_id=end_node_id,
            ).to_dict()
            stored.update(self._extra_entry_fields(entry))
            entries.append(stored)
        return entries, None

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:  # type: ignore[override]
        del kwargs
        signal_id = str(inputs.get("diag_signal_id") or "")
        if signal_id not in self._signal_ids:
            return self.ok({"status": "error", "signal_id": signal_id, "error": "signal_id is not part of this run"})
        if signal_id in self._runtime.invalid_signals:
            return self.ok({"status": "error", "signal_id": signal_id, "error": "signal is marked invalid"})
        mismatch = self._check_signal(signal_id)
        if mismatch:
            return self.ok({"status": "error", "signal_id": signal_id, "error": mismatch})
        entries, error = self._entry_error(signal_id, inputs.get("results"))
        if error is not None:
            return self.ok(error)
        status = self._record(signal_id, entries or [])
        self._runtime.on_progress()
        return self.ok({"status": status, "signal_id": signal_id, "accepted_count": len(entries or [])})


_ENTRY_SCHEMA = {
    "type": "array",
    "description": (
        "定位条目列表 [{text_md, start_node_id, end_node_id?}]；end_node_id 缺省 = 节点级，成对 = "
        "片段级（须同 subagent）。text_md 一句话讲清问题，尽量简单精炼，不写长篇分析。/ "
        "Entries [{text_md, start_node_id, end_node_id?}]; omitted end_node_id = node-level, a "
        "pair = span-level (same subject). Keep text_md to one concise sentence; no long analysis."
    ),
    "items": {
        "type": "object",
        "properties": {
            "text_md": {"type": "string", "description": "归因说明（简明）/ concise attribution markdown"},
            "start_node_id": {"type": "string", "description": "起始节点 ID / start node id"},
            "end_node_id": {"type": "string", "description": "结束节点 ID（可选）/ end node id (optional)"},
        },
        "required": ["text_md", "start_node_id"],
    },
}


_REWARD_ENTRY_SCHEMA = {
    **_ENTRY_SCHEMA,
    "description": (
        "条目列表 [{text_md, start_node_id, end_node_id?, suggestion}]；end_node_id 缺省 = 节点级，"
        "成对 = 片段级（须同 subagent）。text_md 只写问题，一句话讲清、尽量简单精炼；suggestion 写一条"
        "可执行的改进建议（应该怎么改）。/ Entries [{text_md, start_node_id, end_node_id?, "
        "suggestion}]; text_md states the problem in one concise sentence; suggestion is one "
        "actionable improvement (what to change)."
    ),
    "items": {
        **_ENTRY_SCHEMA["items"],
        "properties": {
            **_ENTRY_SCHEMA["items"]["properties"],
            "suggestion": {
                "type": "string",
                "description": (
                    "可执行的改进建议：应该怎么改 / actionable improvement: what to change"
                ),
            },
        },
        "required": [*_ENTRY_SCHEMA["items"]["required"], "suggestion"],
    },
}


def _cause_schema(entry_schema: dict[str, Any] | None = None) -> dict[str, Any]:
    return _schema(
        {
            "diag_signal_id": _text_prop(
                "诊断信号 id（照抄信号列表）", "Diagnostic signal id (copy verbatim from the signal list)"
            ),
            "results": entry_schema if entry_schema is not None else _ENTRY_SCHEMA,
        },
        ["diag_signal_id", "results"],
    )


class SubmitRewardFailureTool(_SubmitCauseTool):
    """提交一条产物评价信号（reward 源）的失败归因。"""

    tool_name = "submit_reward_failure"
    tool_id = "SubmitRewardFailureTool"
    description = (
        "提交一条产物评价信号（reward 源）的失败归因：一次调用提交该信号的全部条目，重复调用整条"
        "覆盖；每条目为 {text_md, start_node_id, end_node_id?, suggestion}，end 缺省为节点级、成对"
        "为片段级（须同 subagent）；任一条目非法则整条报错、不部分记录。text_md 只写问题，一句话"
        "讲清、尽量简单精炼；suggestion 写一条可执行的改进建议（应该怎么改）。/ Submit the located "
        "cause of one reward-source signal: all entries in one call; re-calls overwrite the whole "
        "set; each entry is {text_md, start_node_id, end_node_id?, suggestion}; any invalid entry "
        "fails the whole call with no partial record. text_md states the problem in one concise "
        "sentence; suggestion is one actionable improvement (what to change)."
    )
    input_schema = _cause_schema(_REWARD_ENTRY_SCHEMA)
    record_method = "record_reward_failure"

    def _check_signal(self, signal_id: str) -> str | None:
        signal = self._signals.get(signal_id)
        if signal is None or signal.source != SignalSource.REWARD:
            return "submit_reward_failure only accepts reward-source signals"
        return None

    def _extra_entry_error(self, entry: dict[str, Any]) -> str | None:
        if not str(entry.get("suggestion") or "").strip():
            return "every reward entry needs a non-empty suggestion (one actionable improvement)"
        return None

    def _extra_entry_fields(self, entry: dict[str, Any]) -> dict[str, Any]:
        return {"suggestion": str(entry.get("suggestion") or "").strip()}


class SubmitSkillEvolutionCauseTool(_SubmitCauseTool):
    """提交一条已标 skill 信号的归因。"""

    tool_name = "submit_skill_evolution_cause"
    tool_id = "SubmitSkillEvolutionCauseTool"
    description = (
        "提交一条**已用 mark_signal_topic(category=\"skill\") 标记**的信号的定位归因；未标记或已"
        "标无效的信号会被拒绝。一次调用提交该信号的全部条目，重复调用整条覆盖；任一条目非法则整条"
        "报错、不部分记录。text_md 写问题与 skill 执行依据的关系，尽量简单精炼。/ Submit the "
        "located cause of one signal already marked with mark_signal_topic(category=\"skill\"); "
        "unmarked or invalidated signals are rejected. All entries in one call; re-calls overwrite "
        "the whole set. text_md states how the problem relates to the skill's execution; keep it "
        "concise."
    )
    input_schema = _cause_schema()
    record_method = "record_skill_cause"

    def _check_signal(self, signal_id: str) -> str | None:
        categories = (getattr(self._runtime, "marks", {}) or {}).get(signal_id) or {}
        if "skill" not in categories:
            return "signal is not marked skill; call mark_signal_topic(category=\"skill\") first"
        return None


class SubmitExperienceCauseTool(_SubmitCauseTool):
    """提交一条已标 experience 信号的归因。"""

    tool_name = "submit_experience_cause"
    tool_id = "SubmitExperienceCauseTool"
    description = (
        "提交一条**已用 mark_signal_topic(category=\"experience\") 标记**的信号的定位归因；未标记"
        "或已标无效的信号会被拒绝。一次调用提交该信号的全部条目，重复调用整条覆盖；任一条目非法则"
        "整条报错、不部分记录。text_md 写经验相关问题依据，尽量简单精炼。/ Submit the located "
        "cause of one signal already marked with mark_signal_topic(category=\"experience\"); "
        "unmarked or invalidated signals are rejected. All entries in one call; re-calls overwrite "
        "the whole set. text_md states the evidence for the experience issue; keep it concise."
    )
    input_schema = _cause_schema()
    record_method = "record_experience_cause"

    def _check_signal(self, signal_id: str) -> str | None:
        categories = (getattr(self._runtime, "marks", {}) or {}).get(signal_id) or {}
        if "experience" not in categories:
            return "signal is not marked experience; call mark_signal_topic(category=\"experience\") first"
        return None


class MarkSignalInvalidTool(_AttributionTool):
    """将诊断信号标为无效/无需归因。"""

    tool_name = "mark_signal_invalid"
    tool_id = "MarkSignalInvalidTool"
    description = (
        "将诊断信号标为无效/无需归因（误判、不重要、或选中范围内找不到相关节点）；标记后无需再为它"
        "提交结果，也解除它的 skill/experience 义务。已标 skill/experience 的信号如要撤销标记，"
        "唯一方式也是调用本工具。/ Mark a diagnostic signal as invalid/not worth attributing "
        "(false positive, unimportant, or no relevant nodes in range); no further submission is "
        "needed for it and its skill/experience obligations are released. This is also the only "
        "way to undo a topic mark."
    )
    input_schema = _schema(
        {
            "diag_signal_id": _text_prop(
                "诊断信号 id（照抄信号列表）", "Diagnostic signal id (copy verbatim from the signal list)"
            )
        },
        ["diag_signal_id"],
    )

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:  # type: ignore[override]
        del kwargs
        signal_id = str(inputs.get("diag_signal_id") or "")
        if signal_id not in self._signal_ids:
            return self.ok({"status": "error", "signal_id": signal_id, "error": "signal_id is not part of this run"})
        status = self._runtime.mark_signal_invalid(signal_id)
        self._runtime.on_progress()
        return self.ok({"status": status, "signal_id": signal_id})


class MarkSignalTopicTool(_AttributionTool):
    """标记一条信号属于 skill 或 experience 话题。"""

    tool_name = "mark_signal_topic"
    tool_id = "MarkSignalTopicTool"
    description = (
        "标记一条诊断信号的演进话题：category=\"skill\" 时必须给 skill_key（取自本次轨迹的 Skill "
        "清单，每次调用只标一个 skill），category=\"experience\" 时不给 skill_key。同一信号可分别"
        "标记 skill 与 experience；同一 category 重复调用覆盖。标记是提交 skill/experience 归因与"
        "全局演进建议的前提；只判定会沉淀经验时才标 experience。撤销标记只能调用 "
        "mark_signal_invalid。/ Mark one diagnostic signal's evolution topic: category=\"skill\" "
        "requires skill_key (from the Skill list of this trajectory, one skill per call); "
        "category=\"experience\" leaves it empty. A signal may be marked skill and experience "
        "separately; re-marking the same category overwrites. Marking is the prerequisite of the "
        "skill/experience cause submission and global advice; mark experience only when you decide "
        "to distill one. Undo a mark only via mark_signal_invalid."
    )
    input_schema = _schema(
        {
            "diag_signal_id": _text_prop(
                "诊断信号 id（照抄信号列表）", "Diagnostic signal id (copy verbatim from the signal list)"
            ),
            "category": {
                "type": "string",
                "enum": ["skill", "experience"],
                "description": "话题类别 / topic category",
            },
            "skill_key": _text_prop(
                "skill 名称或 skill.md 绝对路径（category=skill 时必填）",
                "Skill name or absolute skill.md path (required when category=skill)",
            ),
            "relation": {
                "type": "string",
                "enum": ["uses_skill", "caused_by_skill"],
                "description": "可选：skill 参与方式，仅供判断记录 / optional skill relation note",
            },
        },
        ["diag_signal_id", "category"],
    )

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:  # type: ignore[override]
        del kwargs
        signal_id = str(inputs.get("diag_signal_id") or "")
        category = str(inputs.get("category") or "")
        skill_key = str(inputs.get("skill_key") or "").strip() or None
        relation = str(inputs.get("relation") or "").strip() or None
        if signal_id not in self._signal_ids:
            return self.ok({"status": "error", "signal_id": signal_id, "error": "signal_id is not part of this run"})
        if signal_id in self._runtime.invalid_signals:
            return self.ok({"status": "error", "signal_id": signal_id, "error": "signal is marked invalid"})
        if category not in ("skill", "experience"):
            return self.ok({"status": "error", "signal_id": signal_id, "error": "category must be skill or experience"})
        if category == "skill":
            if not bool(getattr(self._runtime, "skill_evolution_enabled", True)):
                return self.ok({"status": "error", "signal_id": signal_id, "error": "skill evolution is disabled"})
            if skill_key is None:
                return self.ok(
                    {"status": "error", "signal_id": signal_id, "error": "skill_key is required for category=skill"}
                )
            if skill_key not in self._runtime.skill_keys():
                return self.ok(
                    {
                        "status": "error",
                        "signal_id": signal_id,
                        "error": (
                            f"skill_key not in this trajectory's Skill list: {skill_key}. Copy it verbatim from the "
                            "Skill list / skill_key 不在本次轨迹的 Skill 清单中，请照抄清单。"
                        ),
                    }
                )
        else:
            if not bool(getattr(self._runtime, "ttse_advice", True)):
                return self.ok({"status": "error", "signal_id": signal_id, "error": "experience advice is disabled"})
            skill_key = None
        if relation is not None and relation not in ("uses_skill", "caused_by_skill"):
            return self.ok(
                {"status": "error", "signal_id": signal_id, "error": "relation must be uses_skill or caused_by_skill"}
            )
        status = self._runtime.mark_signal_topic(signal_id, category, skill_key, relation)
        self._runtime.on_progress()
        return self.ok({"status": status, "signal_id": signal_id, "category": category})


def artifact_improvement_required(runtime: Any) -> bool:
    """Whether this run must submit an artifact improvement: only when a deliverable was reviewed."""
    return bool(getattr(runtime, "artifact_improvement_required", True))


class SubmitArtifactImprovementTool(_AttributionTool):
    """提交产物/任务执行改进意见（实验保留，不参与收口）。"""

    tool_name = "submit_artifact_improvement"
    tool_id = "SubmitArtifactImprovementTool"
    description = (
        "提交产物/任务执行改进意见（markdown，宽泛信息，每个改进点 ≤200 token）；重复调用整条覆盖。"
        "本工具是实验对照路径，可选、不参与收口。/ Submit artifact/execution improvement feedback "
        "(markdown, broad; <=200 tokens per point); re-calls overwrite the whole text. Optional "
        "experimental path; it does not take part in closure."
    )
    input_schema = _schema(
        {
            "md_string": _text_prop(
                "markdown 改进意见；每个改进点 ≤200 token", "Markdown improvement notes; each point <=200 tokens"
            )
        },
        ["md_string"],
    )

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:  # type: ignore[override]
        del kwargs
        md_string = str(inputs.get("md_string") or "")
        if not md_string.strip():
            return self.fail("md_string must be a non-empty string")
        if not artifact_improvement_required(self._runtime):
            return self.ok(
                {
                    "error": (
                        "本次没有评估交付物（框选中没有交付物的最终版本），不提交产物改进意见；把建议写进"
                        "归因（如调用了 skill，也可写进技能改进）。/ No deliverable was reviewed this run (the "
                        "selection holds no final version of one): submit no artifact improvement; put advice in "
                        "attributions (or skill improvements if a skill was used)."
                    )
                }
            )
        status = self._runtime.record_artifact_improvement(md_string)
        return self.ok({"status": status})


class SubmitSkillEvolutionTool(_AttributionTool):
    """提交某个 skill 的演进建议（每个 skill 至多一份）。"""

    tool_name = "submit_skill_evolution"
    tool_id = "SubmitSkillEvolutionTool"
    description = (
        "提交某个 skill 的演进建议：skill_name_or_filename 必须从本次轨迹的 Skill 清单选择，且该 "
        "skill 至少有一条已被标记的归因信号（先 mark_signal_topic 再提交）；每个 skill 至多一份，"
        "重复调用整条覆盖。md_string 是整段改进建议 markdown；confidence 是你对该 skill 需要改进的"
        "总体置信度（0=没把握，1=非常确信）。/ Submit one skill's evolution advice: "
        "skill_name_or_filename must come from this trajectory's Skill list and the skill needs at "
        "least one marked signal (mark_signal_topic first). At most one per skill; re-calls "
        "overwrite. md_string is the whole markdown advice; confidence (0..1) is your overall "
        "confidence that the skill needs improvement."
    )
    input_schema = _schema(
        {
            "skill_name_or_filename": _text_prop("Skill 清单里的名称或路径", "Name or path from the Skill list"),
            "md_string": _text_prop(
                "markdown 改进意见；每个改进点 ≤200 token", "Markdown improvement notes; each point <=200 tokens"
            ),
            "confidence": {
                "type": "number",
                "description": "0-1, default 0.5 / 置信度，缺省 0.5",
            },
        },
        ["skill_name_or_filename", "md_string"],
    )

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:  # type: ignore[override]
        del kwargs
        key = str(inputs.get("skill_name_or_filename") or "").strip()
        md_string = str(inputs.get("md_string") or "")
        confidence = inputs.get("confidence")
        if not key:
            return self.fail("skill_name_or_filename must be a non-empty string")
        if confidence is None:
            confidence = 0.5
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= float(confidence) <= 1:
            return self.fail("confidence must be a number within [0, 1]")
        if key not in self._runtime.skill_keys():
            return self.ok(
                {
                    "error": (
                        f"skill not in this trajectory's Skill list: {key}. Copy it verbatim from the list, or leave "
                        "skill evolution out / skill 不在本次轨迹的 Skill 清单中，请照抄清单或不提交。"
                    )
                }
            )
        marked = any(
            signal_id not in self._runtime.invalid_signals
            and "skill" in categories
            and categories["skill"].skill_key == key
            for signal_id, categories in (getattr(self._runtime, "marks", {}) or {}).items()
        )
        if not marked:
            return self.ok(
                {
                    "error": (
                        "no signal is marked for this skill; call mark_signal_topic(category=\"skill\", "
                        "skill_key=...) and submit its cause first / 先标记该 skill 的信号并提交归因。"
                    )
                }
            )
        status = self._runtime.record_skill_evolution(key, md_string, float(confidence))
        return self.ok({"status": status, "key": key})


class SubmitExperienceEvolutionTool(_AttributionTool):
    """提交本次诊断的经验沉淀判定（至多一份）。"""

    tool_name = "submit_experience_evolution"
    tool_id = "SubmitExperienceEvolutionTool"
    description = (
        "提交本次诊断的经验沉淀判定：outcome 必填（success / partial / fail），suggestions 是 1-3 "
        "条 fact/tip 预览（可选）；每次诊断至多一份，重复调用整条覆盖。提交前必须已用 "
        "mark_signal_topic(category=\"experience\") 标记至少一条信号。/ Submit this diagnosis's "
        "experience verdict: outcome is required (success / partial / fail); suggestions are 1-3 "
        "fact/tip previews (optional). At most one per diagnosis; re-calls overwrite. At least one "
        "signal must already be marked with mark_signal_topic(category=\"experience\")."
    )
    input_schema = _schema(
        {
            "outcome": {
                "type": "string",
                "enum": ["success", "partial", "fail"],
                "description": "经验判定结果 / experience outcome",
            },
            "suggestions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "1-3 条 fact/tip 预览（可选）/ 1-3 fact/tip previews (optional)",
            },
        },
        ["outcome"],
    )

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:  # type: ignore[override]
        del kwargs
        outcome = str(inputs.get("outcome") or "")
        raw_suggestions = inputs.get("suggestions")
        if outcome not in ("success", "partial", "fail"):
            return self.fail("outcome must be one of: success, partial, fail")
        if raw_suggestions is not None and not isinstance(raw_suggestions, list):
            return self.fail("suggestions must be a list of strings")
        suggestions = [
            str(item).strip() for item in (raw_suggestions or []) if str(item).strip()
        ]
        marked = any(
            signal_id not in self._runtime.invalid_signals and "experience" in categories
            for signal_id, categories in (getattr(self._runtime, "marks", {}) or {}).items()
        )
        if not marked:
            return self.ok(
                {
                    "error": (
                        "no signal is marked experience; call mark_signal_topic(category=\"experience\") first / "
                        "先标记经验信号。"
                    )
                }
            )
        status = self._runtime.record_experience_evolution(outcome, suggestions)
        return self.ok({"status": status})


class SubmitTrajectorySummaryTool(_AttributionTool):
    """提交这段轨迹的叙事摘要（收口必交）。"""

    tool_name = "submit_trajectory_summary"
    tool_id = "SubmitTrajectorySummaryTool"
    description = (
        "提交这段轨迹的叙事摘要：发生了什么、存在哪些问题（含未标记规则信号的归因结论）。一段话即可，"
        "控制在 300 字以内，不要包含具体节点 id（如 main:0）；覆盖式；收口必交。/ Submit this "
        "trajectory's narrative summary: what happened and what problems exist (including conclusions "
        "for unmarked rule signals). One paragraph; keep it within 300 characters; do not include "
        "concrete node ids (e.g. main:0); overwrites; required before finishing."
    )
    input_schema = _schema(
        {
            "summary": _text_prop(
                "轨迹摘要（一段话，≤300 字，不含节点 id）",
                "Trajectory summary (one paragraph, ≤300 chars, no node ids)",
            )
        },
        ["summary"],
    )

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:  # type: ignore[override]
        del kwargs
        summary = str(inputs.get("summary") or "").strip()
        if not summary:
            return self.fail("summary must be a non-empty string")
        status = self._runtime.record_trajectory_summary(summary)
        return self.ok({"status": status})


class CheckAttributionCompleteTool(_AttributionTool):
    """校验归因是否收口；结束前必须调用并收到 all_done=true。"""

    tool_name = "check_attribution_complete"
    tool_id = "CheckAttributionCompleteTool"
    description = (
        "校验归因是否收口：reward 信号已归因或标无效；被标 skill/experience 的信号已提交对应归因；"
        "每个被标 skill 有全局建议、被标 experience 有经验判定；轨迹摘要已提交。返回 all_done，false "
        "时附未完成分组；宣布结束前必须调用并收到 all_done=true。/ Verify attribution closure: "
        "reward signals attributed or invalidated; marked skill/experience signals have their cause; "
        "every marked skill has global advice and marked experience has a verdict; the trajectory "
        "summary is submitted. Returns all_done, with pending groups when false; must be called and "
        "return all_done=true before finishing."
    )
    input_schema = _schema({}, [])

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:  # type: ignore[override]
        del inputs, kwargs
        state = completion_state(
            self._runtime,
            list(self._signals.values()),
            skill_evolution=bool(getattr(self._runtime, "skill_evolution_enabled", True)),
            ttse_advice=bool(getattr(self._runtime, "ttse_advice", True)),
        )
        return self.ok(state)


_TOOLS: list[type[_AttributionTool]] = [
    KeywordRegTool,
    ViewTrajectoryTool,
    ViewPartialTrajectoryTool,
    ViewNodeDetailTool,
    ViewNodeFieldTool,
    SubmitRewardFailureTool,
    SubmitSkillEvolutionCauseTool,
    SubmitExperienceCauseTool,
    MarkSignalTopicTool,
    MarkSignalInvalidTool,
    SubmitSkillEvolutionTool,
    SubmitExperienceEvolutionTool,
    SubmitArtifactImprovementTool,
    SubmitTrajectorySummaryTool,
    CheckAttributionCompleteTool,
]


def build_attribution_tools(runtime: AttributionAgentRuntime, signals: list[DiagnosticSignal]) -> list[Tool]:
    """Instantiate the attribution tools bound to one run's runtime."""
    return [tool_cls(runtime, list(signals)) for tool_cls in _TOOLS]


_SUMMARY_TOOLS: list[type[_AttributionTool]] = [
    KeywordRegTool,
    ViewTrajectoryTool,
    ViewPartialTrajectoryTool,
    ViewNodeDetailTool,
    ViewNodeFieldTool,
    SubmitTrajectorySummaryTool,
]


def build_summary_tools(runtime: AttributionAgentRuntime) -> list[Tool]:
    """Instantiate the read + summary tools for a summary-only run."""
    return [tool_cls(runtime, []) for tool_cls in _SUMMARY_TOOLS]


def render_trajectory_view(nodes: list[TrajectoryNode], language: str) -> dict[str, Any]:
    """Route a subagent's nodes to the overview or summary tier."""
    if len(nodes) > 500:
        return render_overview(nodes)
    return render_summary(nodes, language)


def render_partial_view(
    nodes: list[TrajectoryNode],
    from_id: str,
    to_id: str,
    language: str,
) -> dict[str, Any]:
    """Render the summary tier for the inclusive range between two node ids."""
    positions = {node.node_id: index for index, node in enumerate(nodes)}
    start = positions.get(from_id)
    end = positions.get(to_id)
    if start is None or end is None:
        return {"error": f"node not found in range: {from_id if start is None else to_id}"}
    low, high = sorted((start, end))
    stop = high + 1
    selected = nodes[low:stop]
    if len(selected) > 500:
        return render_overview(selected)
    return render_summary(selected, language)


__all__ = [
    "build_attribution_tools",
    "build_summary_tools",
    "render_partial_view",
    "render_trajectory_view",
    "same_subagent",
    "split_node_id",
]
