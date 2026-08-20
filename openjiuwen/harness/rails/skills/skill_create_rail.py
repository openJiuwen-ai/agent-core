# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SkillCreateRail: independent rail for Agent Skill creation suggestions."""

from __future__ import annotations

from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.agent_evolving.prompts.sections import (
    build_skill_creation_guidance_section,
)
from openjiuwen.agent_evolving.signal.skill_creation import (
    SKILL_CREATION_SIGNAL_PROMPT_ELIGIBLE,
    SKILL_CREATION_SIGNAL_SKILL_TOOL_COVER,
    SkillCreationSignal,
    SkillCreationSignalDetector,
    SkillCreationWindowMetrics,
)
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.rails.evolution.evolution_rail import EvolutionRail, EvolutionTriggerPoint

_AUTO_SKILL_CREATION_FOLLOW_UP_TAG = "auto_skill_creation_followup"
_NORMAL_REPLY_CREATION_ACTION_TERMS = (
    "创建",
    "沉淀",
    "create",
    "creation",
    "capture",
)
_NORMAL_REPLY_SKILL_TERMS = ("skill", "技能")

_SKILL_CREATION_FOLLOW_UP_CN = (
    "这是运行时插入的 Skill 创建自检，不是用户的新需求。\n"
    "参考常驻“技能沉淀自检”规则，只判断本轮是否形成可复用方法，不重新判断运行时触发门槛。\n"
    "如需建议，只在普通最终回复末尾追加一至两句，并同时包含可复用方法和是否创建 Skill 的确认问题；"
    "否则自然回复，不提本提醒或内部判断。"
)

_SKILL_CREATION_FOLLOW_UP_EN = (
    "This runtime-inserted Skill creation self-check is not a new user request.\n"
    'Refer to the standing "Skill Capture Self-Check" rules and judge only whether this round produced a reusable '
    "method; do not re-evaluate the runtime trigger threshold.\n"
    "If suggesting, append only one or two sentences to the normal final reply and include both the reusable method "
    "and the Skill creation question; otherwise reply naturally without mentioning this reminder or internal judgment."
)


class SkillCreateRail(EvolutionRail):
    """Independent rail for 1D skill creation.

    Injects stable guidance and, when strong execution signals appear,
    enqueues a conservative follow-up self-check round.
    """

    priority = 85

    def __init__(
        self,
        skills_dir: str,
        *,
        trajectory_span_processor: TrajectorySpanProcessor,
        language: str = "cn",
        auto_trigger: bool = True,
    ) -> None:
        super().__init__(
            evolution_trigger=EvolutionTriggerPoint.NONE,
            trajectory_span_processor=trajectory_span_processor,
        )
        self._skills_dir = skills_dir
        self._auto_trigger = auto_trigger
        self._language = language
        self._follow_up_sent = False
        self._skill_tool_seen_this_invoke = False
        self._last_followed_tool_call_counts: dict[str, int] = {}
        self._last_prompted_tool_totals: dict[str, tuple[int, int]] = {}
        self._trajectory: Trajectory | None = None
        self._signal_detector = SkillCreationSignalDetector()
        self._system_prompt_builder = None

    def init(self, agent) -> None:
        """Capture the agent system prompt builder."""
        self._system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent) -> None:
        """Remove prompt sections owned by this rail."""
        _ = agent
        try:
            if self._system_prompt_builder is not None:
                self._system_prompt_builder.remove_section(SectionName.SKILL_CREATION_GUIDANCE)
        finally:
            self._system_prompt_builder = None
            self._trajectory = None
            super().uninit(agent)

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject stable skill creation guidance."""
        builder = self._get_prompt_builder(ctx)
        if builder is None:
            return

        language = str(getattr(builder, "language", "") or self._language)
        builder.add_section(build_skill_creation_guidance_section(language))

    async def _on_before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Reset per-invoke follow-up flags."""
        del ctx
        self._follow_up_sent = False
        self._skill_tool_seen_this_invoke = False
        self._trajectory = None

    async def _on_after_task_iteration(
        self,
        ctx: AgentCallbackContext,
        trajectory: Trajectory | None,
    ) -> None:
        """Enqueue a one-shot follow-up when deterministic strong signals appear."""
        if trajectory is not None:
            self._trajectory = trajectory
        self._maybe_enqueue_creation_follow_up(ctx)

    def _maybe_enqueue_creation_follow_up(self, ctx: AgentCallbackContext) -> bool:
        """Enqueue a follow-up self-check when thresholds and runtime gates pass."""
        signal = self._should_enqueue_creation_follow_up(ctx)
        if signal is None:
            return False

        controller = getattr(getattr(ctx, "agent", None), "_loop_controller", None)
        if self._assistant_output_proposes_skill_creation(ctx):
            self._follow_up_sent = True
            self._record_prompted_tool_totals(signal.metrics)
            self._refresh_follow_up_watermark(signal.metrics)
            logger.info(
                "[SkillCreateRail] skill creation follow-up skipped: normal reply already proposed creation "
                "session_id=%s",
                self._current_session_id(),
            )
            return False

        if controller is None:
            logger.info("[SkillCreateRail] skill creation follow-up skipped: no task loop controller")
            return False

        controller.enqueue_follow_up(self._build_follow_up_prompt())
        self._follow_up_sent = True
        self._record_prompted_tool_totals(signal.metrics)
        session_id = self._current_session_id()
        logger.info(
            "[SkillCreateRail] skill creation follow-up enqueued: session_id=%s",
            session_id,
        )
        return True

    # ---- Threshold detection ----

    def _should_enqueue_creation_follow_up(
        self,
        ctx: AgentCallbackContext,
    ) -> SkillCreationSignal | None:
        """Return a prompt-eligible signal when a follow-up reminder should be enqueued."""
        if not self._auto_trigger:
            logger.debug("[SkillCreateRail] skill creation follow-up skipped: auto trigger disabled")
            return None
        if self._trajectory is None:
            logger.debug("[SkillCreateRail] skill creation follow-up skipped: no trajectory")
            return None

        session_id = self._current_session_id()
        raw_tool_call_watermark = self._last_followed_tool_call_counts.get(session_id, 0)
        metrics = self._signal_detector.collect_metrics(
            self._trajectory,
            raw_tool_call_watermark=raw_tool_call_watermark,
        )
        if self._follow_up_blocked_by_context(ctx):
            self._refresh_follow_up_watermark(metrics)
            logger.debug("[SkillCreateRail] skill creation follow-up skipped: blocked context")
            return None

        signals = self._signal_detector.detect(
            self._trajectory,
            raw_tool_call_watermark=raw_tool_call_watermark,
            prompted_snapshot=self._last_prompted_tool_totals.get(session_id),
            metrics=metrics,
        )
        cover_signal = self._find_signal(signals, SKILL_CREATION_SIGNAL_SKILL_TOOL_COVER)
        if cover_signal is not None:
            self._skill_tool_seen_this_invoke = True
        if self._skill_tool_seen_this_invoke:
            self._refresh_follow_up_watermark(metrics)
            logger.debug("[SkillCreateRail] skill creation follow-up skipped: skill tool already used")
            return None

        if self._follow_up_sent:
            logger.debug("[SkillCreateRail] skill creation follow-up skipped: already sent in this invoke")
            return None

        prompt_signal = self._find_signal(signals, SKILL_CREATION_SIGNAL_PROMPT_ELIGIBLE)
        if prompt_signal is not None:
            logger.debug(
                "[SkillCreateRail] skill creation follow-up threshold met: "
                "reason=%s, window_effective_tool_calling_iterations=%d, window_effective_tool_calls=%d",
                prompt_signal.reason,
                prompt_signal.metrics.window_effective_tool_calling_iterations,
                prompt_signal.metrics.window_effective_tool_calls,
            )
            return prompt_signal
        logger.debug(
            "[SkillCreateRail] skill creation follow-up skipped: threshold not met "
            "window_effective_tool_calling_iterations=%d, window_effective_tool_calls=%d",
            metrics.window_effective_tool_calling_iterations,
            metrics.window_effective_tool_calls,
        )
        return None

    @staticmethod
    def _find_signal(signals: list[SkillCreationSignal], signal_type: str) -> SkillCreationSignal | None:
        for signal in signals:
            if signal.signal_type == signal_type:
                return signal
        return None

    @staticmethod
    def _follow_up_blocked_by_context(ctx: AgentCallbackContext) -> bool:
        """Suppress follow-ups for non-user runs or follow-up rounds."""
        extra = getattr(ctx, "extra", {}) or {}
        if bool(extra.get("is_follow_up", False)):
            return True

        inputs = getattr(ctx, "inputs", None)
        if bool(getattr(inputs, "is_follow_up", False)):
            return True

        run_kind = extra.get("run_kind", "")
        if not run_kind and inputs is not None:
            run_kind = getattr(inputs, "run_kind", "")
        run_kind_value = getattr(run_kind, "value", run_kind)
        return str(run_kind_value or "").strip().lower() in {"background", "heartbeat", "cron"}

    def _build_follow_up_prompt(self) -> str:
        if self._language.lower().startswith("en"):
            return self._wrap_follow_up_prompt(_SKILL_CREATION_FOLLOW_UP_EN)
        return self._wrap_follow_up_prompt(_SKILL_CREATION_FOLLOW_UP_CN)

    @staticmethod
    def _wrap_follow_up_prompt(prompt: str) -> str:
        return f"<{_AUTO_SKILL_CREATION_FOLLOW_UP_TAG}>\n{prompt}\n</{_AUTO_SKILL_CREATION_FOLLOW_UP_TAG}>"

    @classmethod
    def _assistant_output_proposes_skill_creation(cls, ctx: AgentCallbackContext) -> bool:
        """Return True when the visible assistant output already proposed creating a Skill."""
        output = cls._extract_assistant_output(ctx)
        if not output:
            return False
        normalized = output.lower()
        has_action = any(term.lower() in normalized for term in _NORMAL_REPLY_CREATION_ACTION_TERMS)
        has_skill = any(term.lower() in normalized for term in _NORMAL_REPLY_SKILL_TERMS)
        return has_action and has_skill

    @staticmethod
    def _extract_assistant_output(ctx: AgentCallbackContext) -> str:
        inputs = getattr(ctx, "inputs", None)
        result = getattr(inputs, "result", None)
        if result is None:
            return ""
        if not isinstance(result, dict):
            return str(result) if result else ""

        for key in ("output", "message", "content", "text", "response"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                content = value.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()
        return ""

    def _current_session_id(self) -> str:
        return self._trajectory.session_id if self._trajectory is not None and self._trajectory.session_id else ""

    def _refresh_follow_up_watermark(self, metrics: SkillCreationWindowMetrics) -> None:
        """Advance the session watermark to the metrics snapshot."""
        session_id = self._current_session_id()
        self._last_followed_tool_call_counts[session_id] = metrics.total_raw_tool_calls

    def _record_prompted_tool_totals(self, metrics: SkillCreationWindowMetrics) -> None:
        """Record the totals already used to prompt without consuming the stats window."""
        self._last_prompted_tool_totals[self._current_session_id()] = (
            metrics.total_effective_tool_calling_iterations,
            metrics.total_effective_tool_calls,
        )

    def _get_prompt_builder(self, ctx: AgentCallbackContext):
        builder = self._system_prompt_builder
        if builder is None:
            builder = getattr(getattr(ctx, "agent", None), "system_prompt_builder", None)
            self._system_prompt_builder = builder
        return builder


__all__ = ["SkillCreateRail"]
