# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Built-in static and trace evaluators for capability fingerprints."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from math import isfinite
from typing import Any

from openjiuwen.symphony.evaluation.base import (
    BaseEvaluator,
    EvaluationContext,
    LLMJudgeEvaluator,
    redacted_evidence_reference,
)
from openjiuwen.symphony.models import (
    EvidenceRef,
    FailureReason,
    FailureSeverity,
    ImprovementSuggestion,
    MetricResult,
    SuggestionPriority,
)
from openjiuwen.symphony.models._message_trace import (
    message_has_assistant_or_tool_evidence,
    message_has_user_input,
    project_message_calls,
)


def _fingerprint_evidence(context: EvaluationContext, description: str) -> EvidenceRef:
    return EvidenceRef(
        evidence_type="capability_fingerprint",
        reference=f"capability:{context.fingerprint.capability_id}",
        description=description,
    )


def _trace_evidence(context: EvaluationContext, description: str) -> EvidenceRef:
    case = context.case
    reference = (
        redacted_evidence_reference("case", case.case_id) if case is not None else f"capability:{context.capability_id}"
    )
    return EvidenceRef(evidence_type="evaluation_case", reference=reference, description=description)


def _has_output_evidence(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (dict, list, tuple)):
        return bool(value)
    return True


def _child_has_input_context(context: EvaluationContext) -> bool:
    case = context.case
    if case is None:
        return False
    if case.query.strip():
        return True
    return any(message_has_user_input(case.message[: call.assistant_message_index]) for call in context.matching_calls)


def _output_evaluation_payload(context: EvaluationContext) -> dict[str, Any]:
    """Keep only the matching tool-call fragment for an indirectly evaluated child."""

    payload = context.payload()
    case = context.case
    if case is None or (case.capability_id, case.capability_type) == (
        context.capability_id,
        context.capability_type,
    ):
        return payload
    raw_case = payload.get("case")
    if not isinstance(raw_case, dict):
        return payload
    case_payload = dict(raw_case)
    case_payload["expected_output"] = None
    case_payload["output"] = None
    case_payload["success"] = None
    matching_calls = context.matching_calls
    matching_ids = {call.tool_call_id for call in matching_calls}
    matching_assistant_indexes = {call.assistant_message_index for call in matching_calls}
    matching_tool_indexes = {call.tool_message_index for call in matching_calls if call.tool_message_index is not None}
    raw_message = case_payload.get("message")
    filtered_message: list[dict[str, Any]] = []
    if isinstance(raw_message, list):
        relevant_user_indexes: set[int] = set()
        latest_user_index: int | None = None
        for index, item in enumerate(raw_message):
            if not isinstance(item, dict):
                continue
            if item.get("role") == "user":
                latest_user_index = index
            elif index in matching_assistant_indexes and latest_user_index is not None:
                relevant_user_indexes.add(latest_user_index)
        for index, item in enumerate(raw_message):
            if not isinstance(item, dict):
                continue
            if index in relevant_user_indexes and item.get("role") == "user":
                filtered_message.append(item)
            elif index in matching_assistant_indexes and item.get("role") == "assistant":
                raw_tool_calls = item.get("tool_calls")
                if not isinstance(raw_tool_calls, list):
                    continue
                filtered_tool_calls = [
                    tool_call
                    for tool_call in raw_tool_calls
                    if isinstance(tool_call, dict) and tool_call.get("id") in matching_ids
                ]
                if filtered_tool_calls:
                    filtered_message.append(
                        {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": filtered_tool_calls,
                        }
                    )
            elif index in matching_tool_indexes and item.get("role") == "tool":
                filtered_message.append(item)
    case_payload["message"] = filtered_message
    payload["case"] = case_payload
    return payload


def _compact_accuracy_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Omit empty case fields from the Accuracy judge prompt only."""

    raw_case = payload.get("case")
    if not isinstance(raw_case, dict):
        return payload
    case_payload = {key: value for key, value in raw_case.items() if value is not None}
    if case_payload.get("query") == "":
        case_payload.pop("query")
    if case_payload.get("message") == []:
        case_payload.pop("message")
    payload["case"] = case_payload
    return payload


class StructureConformanceEvaluator(BaseEvaluator):
    """Check the normalized fingerprint contract without reading source assets."""

    metric_id = "structure_conformance"
    scope = "static"

    def evaluate(self, context: EvaluationContext) -> MetricResult:
        fingerprint = context.fingerprint
        invalid: list[tuple[str, str]] = []
        for field_name in ("capability_id", "capability_type", "name", "content_hash"):
            value = getattr(fingerprint, field_name, None)
            if not isinstance(value, str) or not value.strip():
                invalid.append((field_name, f"{field_name} must be a non-empty string"))
        for field_name in ("inputs", "outputs", "tags"):
            value = getattr(fingerprint, field_name, None)
            if not isinstance(value, (tuple, list)):
                invalid.append((field_name, f"{field_name} must be a sequence"))
        for direction in ("inputs", "outputs"):
            values = getattr(fingerprint, direction, ())
            names = [getattr(value, "name", None) for value in values]
            if any(not isinstance(name, str) or not name.strip() for name in names):
                invalid.append((direction, f"{direction} entries must have non-empty names"))
            elif len(names) != len(set(names)):
                invalid.append((direction, f"{direction} names must be unique"))

        evidence = _fingerprint_evidence(context, "Normalized fingerprint structure was inspected.")
        if not invalid:
            return self.result(
                context,
                score=1.0,
                status="pass",
                reason="The fingerprint conforms to the public structure contract.",
                details={"checked_fields": 7},
                evidence=(evidence,),
            )
        failures = tuple(
            FailureReason(
                code=f"invalid_{field_name}",
                message=message,
                severity=FailureSeverity.WARNING,
                evidence=(evidence,),
            )
            for field_name, message in invalid
        )
        suggestions = tuple(
            ImprovementSuggestion(
                code=f"fix_{field_name}",
                message=f"Normalize the fingerprint {field_name} field before evaluation.",
                priority=SuggestionPriority.HIGH,
                related_failures=(f"invalid_{field_name}",),
            )
            for field_name, _ in invalid
        )
        return self.result(
            context,
            score=0.0,
            status="fail",
            reason=f"The fingerprint has {len(invalid)} structural issue(s).",
            details={"invalid_fields": [field_name for field_name, _ in invalid]},
            evidence=(evidence,),
            failures=failures,
            suggestions=suggestions,
        )


class DescriptionQualityEvaluator(LLMJudgeEvaluator):
    """Judge whether a description is specific enough for discovery and use."""

    metric_id = "description_quality"
    scope = "static"
    rubric = (
        "Judge whether the capability description clearly states what the capability does, "
        "when it should be used, and material limits without unsupported claims."
    )

    def validate_context(self, context: EvaluationContext) -> MetricResult | None:
        if context.fingerprint.description.strip():
            return None
        evidence = _fingerprint_evidence(context, "The normalized description field is empty.")
        return self.result(
            context,
            score=0.0,
            status="fail",
            reason="The capability description is empty.",
            evidence=(evidence,),
            failures=(
                FailureReason(
                    code="missing_description",
                    message="The capability description is empty.",
                    severity=FailureSeverity.WARNING,
                    evidence=(evidence,),
                ),
            ),
            suggestions=(
                ImprovementSuggestion(
                    code="add_capability_description",
                    message="Describe the capability purpose, intended use, and important limits.",
                    priority=SuggestionPriority.HIGH,
                    related_failures=("missing_description",),
                ),
            ),
        )


class ClassificationConsistencyEvaluator(LLMJudgeEvaluator):
    """Judge consistency among description, semantic profile, classification, and tags."""

    metric_id = "classification_consistency"
    scope = "static"
    rubric = (
        "Judge whether classification and tags are semantically consistent with the capability "
        "description, profile, inputs, and outputs."
    )

    def validate_context(self, context: EvaluationContext) -> MetricResult | None:
        fingerprint = context.fingerprint
        if fingerprint.classification.strip() or fingerprint.tags:
            return None
        evidence = _fingerprint_evidence(context, "Classification and tag fields are both empty.")
        return self.result(
            context,
            score=0.0,
            status="fail",
            reason="The fingerprint has neither a classification nor tags.",
            evidence=(evidence,),
            failures=(
                FailureReason(
                    code="missing_classification",
                    message="The fingerprint has neither a classification nor tags.",
                    severity=FailureSeverity.WARNING,
                    evidence=(evidence,),
                ),
            ),
            suggestions=(
                ImprovementSuggestion(
                    code="add_classification",
                    message="Add a classification or tags derived from the capability semantics.",
                    priority=SuggestionPriority.MEDIUM,
                    related_failures=("missing_classification",),
                ),
            ),
        )


class TraceEvaluator(BaseEvaluator):
    """Base class for metrics that require a caller-supplied trace case."""

    scope = "trace"

    def require_case(self, context: EvaluationContext) -> MetricResult | None:
        if context.case is not None:
            return None
        return self.not_applicable(context, "No evaluation trace case was supplied.", code="missing_trace_case")


class LLMTraceEvaluator(LLMJudgeEvaluator, TraceEvaluator):
    """Base class for opt-in semantic trace metrics."""

    scope = "trace"

    def validate_context(self, context: EvaluationContext) -> MetricResult | None:
        return self.require_case(context)


class SuccessRateEvaluator(TraceEvaluator):
    """Report whether a supplied execution case succeeded."""

    metric_id = "success_rate"

    def evaluate(self, context: EvaluationContext) -> MetricResult:
        missing = self.require_case(context)
        if missing is not None:
            return missing
        case = context.case
        if case is None:
            return self.not_applicable(context, "No evaluation trace case was supplied.", code="missing_trace_case")
        directly_evaluated = (case.capability_id, case.capability_type) == (
            context.fingerprint.capability_id,
            context.fingerprint.capability_type,
        )
        success = case.success if directly_evaluated else None
        if success is None:
            return self.not_applicable(
                context,
                "The trace does not contain an execution outcome.",
                code="missing_success_outcome",
            )
        evidence = _trace_evidence(context, "The caller-supplied execution outcome was evaluated.")
        if success:
            return self.result(
                context,
                score=1.0,
                status="pass",
                reason="The execution succeeded.",
                evidence=(evidence,),
            )
        return self.result(
            context,
            score=0.0,
            status="fail",
            reason="The execution did not succeed.",
            evidence=(evidence,),
            failures=(
                FailureReason(
                    code="execution_unsuccessful",
                    message="The caller-supplied trace reports an unsuccessful execution.",
                    severity=FailureSeverity.WARNING,
                    evidence=(evidence,),
                ),
            ),
            suggestions=(
                ImprovementSuggestion(
                    code="inspect_execution_failure",
                    message="Inspect the referenced trace and address its reported failure.",
                    priority=SuggestionPriority.HIGH,
                    related_failures=("execution_unsuccessful",),
                ),
            ),
        )


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def latency_statistics(values: Iterable[float]) -> dict[str, Any]:
    """Return raw latency distribution statistics without applying business thresholds."""

    samples = [float(value) for value in values if isfinite(float(value)) and float(value) >= 0]
    if not samples:
        return {}
    return {
        "avg_ms": sum(samples) / len(samples),
        "p50_ms": _percentile(samples, 0.50),
        "p95_ms": _percentile(samples, 0.95),
        "max_ms": max(samples),
        "count": len(samples),
        "samples_ms": samples,
    }


class LatencyEvaluator(TraceEvaluator):
    """Observe caller-supplied TTFT and end-to-end latency without scoring."""

    metric_id = "latency"

    def evaluate(self, context: EvaluationContext) -> MetricResult:
        missing = self.require_case(context)
        if missing is not None:
            return missing
        case = context.case
        if case is None:
            return self.not_applicable(context, "No evaluation trace case was supplied.", code="missing_trace_case")
        directly_evaluated = (case.capability_id, case.capability_type) == (
            context.fingerprint.capability_id,
            context.fingerprint.capability_type,
        )
        if not directly_evaluated:
            return self.not_applicable(
                context,
                "The trace does not contain latency for the evaluated child capability.",
                code="missing_latency_observation",
            )
        if case.latency is None:
            return self.not_applicable(context, "The trace has no latency value.", code="missing_latency_observation")
        details: dict[str, Any] = {}
        if case.latency.ttft is not None:
            details["ttft_ms"] = case.latency.ttft
        if case.latency.e2e is not None:
            details["e2e_ms"] = case.latency.e2e
        if not details:
            return self.not_applicable(
                context,
                "The trace has no latency observations.",
                code="missing_latency_observation",
            )
        evidence = _trace_evidence(context, "Caller-supplied latency observations were recorded.")
        return self.result(
            context,
            score=None,
            status="observed",
            reason="Latency was observed without applying a target or admission threshold.",
            details=details,
            evidence=(evidence,),
        )


class AccuracyEvaluator(LLMTraceEvaluator):
    """Judge factual or expected-output correctness of a supplied result."""

    metric_id = "accuracy"
    allows_null_score = True
    response_instruction = (
        "Return only JSON with score 0, 1, or null and a concise reason.\n"
        "When score is null, reason must state which not-applicable condition applies."
    )
    rubric = (
        "准确性三值评分标准：\n"
        "请先判断本次准确性评估是否适用。仅在满足下列“不适用”条件之一时，"
        "返回 score=null；否则必须继续按照准确或不准确规则返回 score=1 或 score=0。\n"
        "不适用（score=null）：\n"
        "1. 用户 query 与 Evaluation data 中 fingerprint.description 描述的 Skill"
        " 能力范围明确无关，且 query 中不存在任何属于该 Skill 的实质子意图。\n"
        "2. 完整 message 和可选 output 中没有任何可供用户使用、可进行事实准确性"
        " 判断的实质回答，只包含内部规划、路由标记、工具调用参数、空工具结果、"
        "状态信息、NO_REPLY 或无实质内容的拒答。\n"
        "3. 工具结果中如果包含明确、完整、实际可作为会话结果的自然语言回答，"
        "应视为存在实质回答；仅有工具调用或原始结构化数据不自动等于实质回答。\n"
        "不得返回 score=null 的情况：\n"
        "- 已存在面向用户的实质回答，但回答不完整、质量较差或包含事实错误；"
        "此时必须返回 score=0 或 score=1。\n"
        "- 事实证据不足、缺少后续确认或评估模型不了解新知识；"
        "这些情况必须继续遵循本 rubric 的证据规则，不能以此返回 null。\n"
        "- query 同时包含多个意图，只要其中存在属于当前 Skill 的实质子意图，"
        "就必须评估相关回答并返回 0 或 1。\n"
        "优秀（score=1，满足以下所有条件）：\n"
        "- 无事实性错误，核心信息准确。\n"
        "- 完全符合客观事实，无任何事实性错误。\n"
        "- 逻辑严密，推理严谨，无逻辑谬误。\n"
        "- 符合行业规范/专业规范，表述专业。\n"
        "- 新知识/新产品场景：当回答涉及新知识、新产品、新功能等内容时，"
        "即使评估模型的历史训练数据中未包含这些信息，只要回答逻辑合理、"
        "符合用户需求、表述清晰，也应判断为准确回答。\n"
        "不及格（score=0，满足以下条件之一）：\n"
        "- 存在事实性错误，与客观事实不符。\n"
        "- 存在逻辑谬误，推理过程断裂。\n"
        "- 存在专业偏差，违背行业标准/专业规范。\n"
        "- 输出内容不可用，可能误导用户。\n"
        "补充规则：\n"
        "1. 事实核查：以有无事实错误为首要判断标准，检查执行结果中是否存在"
        "事实性错误。\n"
        "2. 逻辑验证：检查推理过程是否严密，是否存在逻辑谬误。\n"
        "3. 专业规范：评估是否符合行业规范/专业规范。\n"
        "4. 新知识处理：对于涉及新知识、新产品、新功能的内容，评估其逻辑"
        "合理性和表述清晰度。\n"
        "5. 时间基准：Evaluation data 中的 reference_time 是本次评估的权威"
        "当前时间，即使它晚于模型训练截止时间，也必须将其视为真实时间"
        "基准。不得仅因电影、产品、功能或事件晚于模型训练时间，就认定其"
        "尚未发生或存在事实错误。对于训练截止时间之后的信息，优先依据"
        "工具结果、对话上下文和回答的内部一致性判断；没有相反证据时，"
        "不得仅以模型不知道该信息为由判定 score=0；但如果工具结果或"
        "上下文与回答矛盾，仍应判定为事实错误。\n"
        "6. 时间证据判断：将今年、今天、当前等相对时间统一按 reference_time"
        " 解析。工具内容的发布时间与其描述的事件日期是两个不同时间，不得"
        "混淆。如果较早发布的工具结果说明事件计划于某个日期发生，且事件"
        "日期不晚于 reference_time，则回答称该事件已经发生不与该工具结果"
        "矛盾。只有存在延期、取消、尚未发生等相反证据时，才能据此判定事实"
        "错误。缺少事件发生后的二次确认属于证据不足；证据不足不等于事实"
        "错误，也不等于证据矛盾。\n"
        "7. 错误判定的证据要求：判定 score=0 时，必须指出回答中的具体事实，"
        "以及与其直接冲突的工具结果、上下文证据或确定的客观事实。新闻发布"
        "时间早于事件日期本身不构成冲突。如果没有明确相反证据，不得把不"
        "确定或缺少后续确认解释为事实错误。\n"
        "注意事项：如果数据不完整但不存在明确错误或相反证据，应判定"
        " score=1，并在 reason 中简要说明证据边界。核心评估重点是有无事实"
        "错误，只要存在事实性错误即判定为不及格。"
    )

    def validate_context(self, context: EvaluationContext) -> MetricResult | None:
        missing = self.require_case(context)
        if missing is not None:
            return missing
        case = context.case
        if case is None:
            return self.not_applicable(context, "No evaluation trace case was supplied.", code="missing_trace_case")
        directly_evaluated = (case.capability_id, case.capability_type) == (
            context.fingerprint.capability_id,
            context.fingerprint.capability_type,
        )
        outputs_match = (
            case.output is not None and case.expected_output is not None and case.output == case.expected_output
        )
        if directly_evaluated and outputs_match:
            evidence = _trace_evidence(context, "Output exactly matches the supplied expected output.")
            return self.result(
                context,
                score=1.0,
                status="pass",
                reason="Output exactly matches the supplied expected output.",
                details={"evaluation_method": "exact_match"},
                evidence=(evidence,),
            )
        if directly_evaluated:
            has_input = bool(case.query.strip()) or message_has_user_input(case.message)
            has_evidence = _has_output_evidence(case.output) or message_has_assistant_or_tool_evidence(case.message)
        else:
            has_input = _child_has_input_context(context)
            has_evidence = any(
                call.tool_message_index is not None and _has_output_evidence(call.output)
                for call in context.matching_calls
            )
        if not has_input or not has_evidence:
            return self.not_applicable(context, "The trace has no usable message or output.", code="missing_output")
        return None

    def evaluation_payload(self, context: EvaluationContext) -> dict[str, Any]:
        payload = _compact_accuracy_payload(_output_evaluation_payload(context))
        event_time = context.case.event_time if context.case is not None else None
        if event_time is None:
            reference_time = datetime.now(UTC)
            reference_time_source = "evaluation_time"
        else:
            reference_time = event_time
            reference_time_source = "event_time"
        payload["reference_time"] = reference_time.isoformat()
        payload["reference_time_source"] = reference_time_source
        return payload


class CompletenessEvaluator(LLMTraceEvaluator):
    """Judge whether a result completes the requested work."""

    metric_id = "completeness"
    rubric = (
        "Judge whether the complete message trace and optional output complete every material part "
        "of the caller-supplied query. Treat assistant requests to tools and the corresponding tool responses "
        "as execution evidence."
    )

    def validate_context(self, context: EvaluationContext) -> MetricResult | None:
        missing = self.require_case(context)
        if missing is not None:
            return missing
        case = context.case
        if case is None:
            return self.not_applicable(context, "No evaluation trace case was supplied.", code="missing_trace_case")
        directly_evaluated = (case.capability_id, case.capability_type) == (
            context.fingerprint.capability_id,
            context.fingerprint.capability_type,
        )
        if directly_evaluated:
            has_input = bool(case.query.strip()) or message_has_user_input(case.message)
            has_evidence = _has_output_evidence(case.output) or message_has_assistant_or_tool_evidence(case.message)
        else:
            has_input = _child_has_input_context(context)
            has_evidence = any(
                call.tool_message_index is not None and _has_output_evidence(call.output)
                for call in context.matching_calls
            )
        if has_input and has_evidence:
            return None
        evidence = _trace_evidence(context, "The trace contains no usable message or output.")
        return self.result(
            context,
            score=0.0,
            status="fail",
            reason="The trace contains no usable message or output.",
            evidence=(evidence,),
            failures=(
                FailureReason(
                    code="missing_output",
                    message="The trace contains no usable message or output.",
                    severity=FailureSeverity.WARNING,
                    evidence=(evidence,),
                ),
            ),
            suggestions=(
                ImprovementSuggestion(
                    code="produce_complete_output",
                    message="Return an output that addresses the requested task.",
                    priority=SuggestionPriority.HIGH,
                    related_failures=("missing_output",),
                ),
            ),
        )

    def evaluation_payload(self, context: EvaluationContext) -> dict[str, Any]:
        return _output_evaluation_payload(context)


class CapabilitySelectionEvaluator(LLMTraceEvaluator):
    """Judge whether the observed calls selected suitable capabilities."""

    metric_id = "capability_selection"
    rubric = (
        "Judge whether the capabilities selected in the trace are relevant and sufficient "
        "for the query, without relying on capability execution outside the supplied trace."
    )

    def validate_context(self, context: EvaluationContext) -> MetricResult | None:
        missing = self.require_case(context)
        if missing is not None:
            return missing
        case = context.case
        if case is None:
            return self.not_applicable(context, "No evaluation trace case was supplied.", code="missing_trace_case")
        if not project_message_calls(case.message):
            return self.not_applicable(
                context,
                "The trace contains no capability selections.",
                code="missing_capability_calls",
            )
        return None


class CompositionEffectivenessEvaluator(LLMTraceEvaluator):
    """Judge the ordering and hand-off of a multi-capability trace."""

    metric_id = "composition_effectiveness"
    rubric = (
        "Judge whether the order, inputs, outputs, and hand-offs among the selected capabilities "
        "form an effective composition for the query."
    )

    def validate_context(self, context: EvaluationContext) -> MetricResult | None:
        missing = self.require_case(context)
        if missing is not None:
            return missing
        case = context.case
        if case is None:
            return self.not_applicable(context, "No evaluation trace case was supplied.", code="missing_trace_case")
        if len(project_message_calls(case.message)) < 2:
            return self.not_applicable(
                context,
                "Composition evaluation requires at least two observed capability calls.",
                code="insufficient_composition_calls",
            )
        return None


BUILTIN_EVALUATORS = (
    StructureConformanceEvaluator,
    DescriptionQualityEvaluator,
    ClassificationConsistencyEvaluator,
    SuccessRateEvaluator,
    LatencyEvaluator,
    AccuracyEvaluator,
    CompletenessEvaluator,
    CapabilitySelectionEvaluator,
    CompositionEffectivenessEvaluator,
)


__all__ = [
    "BUILTIN_EVALUATORS",
    "AccuracyEvaluator",
    "CapabilitySelectionEvaluator",
    "ClassificationConsistencyEvaluator",
    "CompletenessEvaluator",
    "CompositionEffectivenessEvaluator",
    "DescriptionQualityEvaluator",
    "LatencyEvaluator",
    "StructureConformanceEvaluator",
    "SuccessRateEvaluator",
    "latency_statistics",
]
