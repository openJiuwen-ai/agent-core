# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The ten attribution tools bound to one run's runtime.

Search/render logic lives in core pure functions; these tools only route to
the host runtime and keep the reporting contracts (atomicity, overwrite
semantics, closure check).
"""

from __future__ import annotations

import json
from typing import Any

from openjiuwen.agent_evolving.attribution.agent import AttributionAgentRuntime
from openjiuwen.agent_evolving.attribution.model import AttributionEntry
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

    def __init__(self, runtime: AttributionAgentRuntime, signal_ids: list[str]) -> None:
        self._runtime = runtime
        self._signal_ids = set(signal_ids)
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


class SubmitAttributionTool(_AttributionTool):
    """提交一条诊断信号的定位归因（整条原子提交）。"""

    tool_name = "submit_attribution"
    tool_id = "SubmitAttributionTool"
    description = (
        "提交一条诊断信号的定位归因：一次调用提交该信号的全部条目，重复调用整条覆盖；每条目为 "
        "{text_md, start_node_id, end_node_id?}，end 缺省为节点级定位、成对为片段级（须同 subagent）；"
        "任一条目非法则整条报错、不部分记录。/ Submit the located attribution for one diagnostic "
        "signal: all entries in one call; re-calls overwrite the whole set; each entry is "
        "{text_md, start_node_id, end_node_id?} — end omitted means node-level, a pair means "
        "span-level (same subject required); any invalid entry fails the whole call with no "
        "partial record."
    )
    input_schema = _schema(
        {
            "diag_signal_id": _text_prop(
                "诊断信号 id（照抄信号列表）", "Diagnostic signal id (copy verbatim from the signal list)"
            ),
            "results": {
                "type": "array",
                "description": (
                    "归因条目列表 [{text_md, start_node_id, end_node_id?}]；end_node_id 缺省 = "
                    "start_node_id（节点级），成对 = 片段级；text_md 写清问题 / 原因 / 改进建议。/ "
                    "List of entries [{text_md, start_node_id, end_node_id?}]; omitted end_node_id "
                    "= node-level, paired = span-level; text_md states problem / cause / improvement."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "text_md": {"type": "string", "description": "归因说明 markdown / attribution markdown"},
                        "start_node_id": {"type": "string", "description": "起始节点 ID / start node id"},
                        "end_node_id": {"type": "string", "description": "结束节点 ID（可选）/ end node id (optional)"},
                    },
                    "required": ["text_md", "start_node_id"],
                },
            },
        },
        ["diag_signal_id", "results"],
    )

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:  # type: ignore[override]
        del kwargs
        signal_id = str(inputs.get("diag_signal_id") or "")
        results = inputs.get("results")
        if signal_id not in self._signal_ids:
            return self.ok({"status": "error", "signal_id": signal_id, "error": "signal_id is not part of this run"})
        if not isinstance(results, list) or not results:
            return self.ok({"status": "error", "signal_id": signal_id, "error": "results must be a non-empty list"})
        entries: list[dict[str, Any]] = []
        for entry in results:
            if not isinstance(entry, dict):
                return self.ok({"status": "error", "signal_id": signal_id, "error": "every entry must be an object"})
            start_node_id = str(entry.get("start_node_id") or "")
            end_node_id = str(entry.get("end_node_id") or "") or None
            if split_node_id(start_node_id) is None:
                return self.ok(
                    {"status": "error", "signal_id": signal_id, "error": f"invalid start_node_id: {start_node_id}"}
                )
            if end_node_id is not None:
                if split_node_id(end_node_id) is None:
                    return self.ok(
                        {"status": "error", "signal_id": signal_id, "error": f"invalid end_node_id: {end_node_id}"}
                    )
                if not same_subagent(start_node_id, end_node_id):
                    return self.ok(
                        {
                            "status": "error",
                            "signal_id": signal_id,
                            "error": (
                                f"start_node_id 与 end_node_id 属于不同 subagent（{start_node_id} vs "
                                f"{end_node_id}），不可成对定位；请分别成条提交。/ start_node_id and "
                                f"end_node_id belong to different subagents ({start_node_id} vs "
                                f"{end_node_id}); paired location is not allowed — submit separate entries."
                            ),
                        }
                    )
            detail = self._runtime.render_node_detail(start_node_id)
            if "error" in detail:
                return self.ok(
                    {
                        "status": "error",
                        "signal_id": signal_id,
                        "error": (
                            f"节点不存在：{start_node_id}。/ Node not found: {start_node_id}. "
                            "Any invalid entry fails the whole call; fix it and resubmit the complete list."
                        ),
                    }
                )
            if end_node_id is not None:
                detail = self._runtime.render_node_detail(end_node_id)
                if "error" in detail:
                    return self.ok(
                        {
                            "status": "error",
                            "signal_id": signal_id,
                            "error": (
                                f"节点不存在：{end_node_id}。/ Node not found: {end_node_id}. "
                                "Any invalid entry fails the whole call; fix it and resubmit the complete list."
                            ),
                        }
                    )
            entries.append(
                AttributionEntry(
                    text_md=str(entry.get("text_md") or ""),
                    start_node_id=start_node_id,
                    end_node_id=end_node_id,
                ).to_dict()
            )
        status = self._runtime.record_attribution(signal_id, entries)
        self._runtime.on_progress()
        return self.ok({"status": status, "signal_id": signal_id, "accepted_count": len(entries)})


class MarkSignalInvalidTool(_AttributionTool):
    """将诊断信号标为无效/无需归因。"""

    tool_name = "mark_signal_invalid"
    tool_id = "MarkSignalInvalidTool"
    description = (
        "将诊断信号标为无效/无需归因（误判、不重要、或选中范围内找不到相关节点）；标记后无需再为它"
        "提交结果。/ Mark a diagnostic signal as invalid/not worth attributing (false positive, "
        "unimportant, or no relevant nodes in range); no further submission needed for it."
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


def artifact_improvement_required(runtime: Any) -> bool:
    """Whether this run must submit an artifact improvement: only when a deliverable was reviewed."""
    return bool(getattr(runtime, "artifact_improvement_required", True))


class SubmitArtifactImprovementTool(_AttributionTool):
    """提交产物/任务执行改进意见。"""

    tool_name = "submit_artifact_improvement"
    tool_id = "SubmitArtifactImprovementTool"
    description = (
        "提交产物/任务执行改进意见（markdown，宽泛信息，每个改进点 ≤200 token）；重复调用整条覆盖。/ "
        "Submit artifact/execution improvement feedback (markdown, broad; <=200 tokens per "
        "point); re-calls overwrite the whole text."
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


class SubmitSkillImprovementTool(_AttributionTool):
    """提交技能改进意见（仅在问题源于 skill 时）。"""

    tool_name = "submit_skill_improvement"
    tool_id = "SubmitSkillImprovementTool"
    description = (
        "提交技能改进意见；仅当归因发现问题是某个 skill 导致、或可通过优化 skill 改善时才调用；"
        "轨迹中没有任何 `skill_tool`/`list_skill` 工具调用、也未读取任何 skill.md 文件时禁止调用；"
        "同一 key 重复调用整条覆盖。/ Submit a skill improvement; only call when a skill caused "
        "the problem or optimizing one would help; never call when the trajectory has no "
        "`skill_tool`/`list_skill` tool calls and no skill.md reads; re-calls with the same key "
        "overwrite."
    )
    input_schema = _schema(
        {
            "skill_name_or_filename": _text_prop("skill 名称或文件名", "Skill name or filename"),
            "md_string": _text_prop(
                "markdown 改进意见；每个改进点 ≤200 token", "Markdown improvement notes; each point <=200 tokens"
            ),
        },
        ["skill_name_or_filename", "md_string"],
    )

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:  # type: ignore[override]
        del kwargs
        key = str(inputs.get("skill_name_or_filename") or "")
        md_string = str(inputs.get("md_string") or "")
        if not key.strip():
            return self.fail("skill_name_or_filename must be a non-empty string")
        status = self._runtime.record_skill_improvement(key, md_string)
        return self.ok({"status": status, "key": key})


class CheckAttributionCompleteTool(_AttributionTool):
    """校验归因是否收口；结束前必须调用并收到 all_done=true。"""

    tool_name = "check_attribution_complete"
    tool_id = "CheckAttributionCompleteTool"
    description = (
        "校验归因是否收口：每条诊断信号已归因或已标无效，且已提交产物/任务执行改进意见；返回 "
        "all_done，false 时附未完成项；宣布结束前必须调用并收到 all_done=true。/ Verify "
        "attribution closure: every signal attributed or invalidated, and the artifact/execution "
        "improvement submitted; returns all_done, with pending items when false; must be called "
        "and return all_done=true before finishing."
    )
    input_schema = _schema({}, [])

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:  # type: ignore[override]
        del inputs, kwargs
        overview = self._runtime.completion_overview()
        attributed = set(overview.get("attributed_signal_ids") or ())
        invalid = set(overview.get("invalid_signal_ids") or ())
        artifact_submitted = bool(overview.get("artifact_improvement_submitted")) or not (
            artifact_improvement_required(self._runtime)
        )
        pending = sorted(self._signal_ids - attributed - invalid)
        if not pending and artifact_submitted:
            return self.ok({"all_done": True})
        return self.ok(
            {
                "all_done": False,
                "pending_signal_ids": pending,
                "missing_artifact_improvement": not artifact_submitted,
            }
        )


_TOOLS: list[type[_AttributionTool]] = [
    KeywordRegTool,
    ViewTrajectoryTool,
    ViewPartialTrajectoryTool,
    ViewNodeDetailTool,
    ViewNodeFieldTool,
    SubmitAttributionTool,
    MarkSignalInvalidTool,
    SubmitArtifactImprovementTool,
    SubmitSkillImprovementTool,
    CheckAttributionCompleteTool,
]


def build_attribution_tools(runtime: AttributionAgentRuntime, signal_ids: list[str]) -> list[Tool]:
    """Instantiate the ten tools bound to one run's runtime."""
    return [tool_cls(runtime, list(signal_ids)) for tool_cls in _TOOLS]


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
    selected = nodes[low : high + 1]
    if len(selected) > 500:
        return render_overview(selected)
    return render_summary(selected, language)


__all__ = [
    "build_attribution_tools",
    "render_partial_view",
    "render_trajectory_view",
    "same_subagent",
    "split_node_id",
]
