# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""LlmThinkingLoopDetector tests — aligned with thinking_loop_lock_rail.py."""
from __future__ import annotations

import asyncio
import contextvars
import time
from unittest.mock import patch

import pytest

from openjiuwen.harness.agent_ras.config import LlmThinkingLoopConfig
from openjiuwen.harness.agent_ras.detectors.llm_thinking_loop import (
    LlmThinkingLoopDetector,
    LoopDetector,
    _clause_similarity,
    _extract_lexical_key,
)
from openjiuwen.harness.agent_ras.models import AnomalyKind, Severity, Signal, SignalKind

# ---------------------------------------------------------------------------
# Synthetic L2 windows (anonymized; structure mirrors real FP shapes)
# ---------------------------------------------------------------------------

# Scenario 1: pixel checklist — multiple small similar pairs (union would FP;
# largest-cluster constraint should not).
WIN_PIXEL = """_map.json for P2 images. Let me check quickly.

Actually, let me just proceed. The template already has a background image, and for a content page with timeline and comparison, I don't need additional images. The task says image mapping is optional when missing.

Let me now write the complete HTML file.

I need to think about the exact pixel layout:

Content-safe: 1220 × 660px
- Header: 60px
- Main: flex-1 (660 - 60 - 30 - some gaps = ~560px)
- Footer: 30px
- Gaps: ~10px

Main area: ~560px height, 1220px width

Left column (flex-[3]): ~732px width
- Title: ~25px
- Timeline: ~525px (7 items × ~75px each)

Right column (flex-[2]): ~488px width
- Comparison card: ~280px
- Winters card: ~250px
- Gap: ~10px

Let me calculate more precisely:
- Main height: 660 - 60 (header) - 30 (footer) - 12 (gaps) = 558px
- Left column: 558px
  - Section title: 24px + 8px margin = 32px
  - Timeline: 558 - 32 = 526px
  - 7 items in 526px = ~75px per item

Each timeline item (75px):
- Year (16px bold) + Event name (14px) on one line: ~20px
- Description (12px): ~16px
- Padding/margin: ~39px (including dot, spacing)

This should work. Let me use a compact design.

For the right column:
- Comparison card: ~270px
  - Title: 24px + 8px = 32px
  - Table: 4 rows × ~50px = 200px
  - Padding: ~38px
- Winters card: ~278px
  - Title: 24px + 8px = 32px
  - 2 winter items: ~100px each = 200px
  - Padding: ~46px

This should fit.

OK, let me write the HTML now. I'll be very precise.

Actually, let me reconsider the timeline design. Instead of 7 individual items, let me group them into 3 phases with visual separation:

**Phase 1: foundation (stop overthinking and execute. Inputs ready:

1. Template shell (page-2.html) - read
2. Research content (research-P2.md) - read
3. Outline (outline.md) - read
4. Designer skill - loaded
5. Charts.md - read
6. Templ"""

# Scenario 2: multi-page research checklist / search-mode boilerplate.
WIN_CHECKLIST = """now i need to create 3 worker subagents in parallel, one for each content page (p2, p3, p4). each worker will:
read the research-writer skill.md
read the outline.md
generate research-p{n}.md for their assigned page
the output_dir is:
`/workspace/project/output/`
the research_depth for 3 content pages should be l1 (quick research, >=1200 words).
let me create all 3 worker subagents in the same message.let me break down the task:
read the skill instruction file at `/workspace/skills/research-writer/skill.md`
read the outline at the specified path
generate a deep research report fragment for p2 "early exploration (1943-1990s)"
write to `research-p2.md` in the output directory
research depth: l1 (>=1200 words)
search mode: auto
only research page 2, not other pages
let me start by reading the skill file and the outline.let me break down the task:
read the skill.md file first (must use skill_tool since it's a skill file)
read the outline.md file
generate a deep research report fragment for p3 (page 3) about "deep learning era (2006-2023)"
write to research-p3.md
research depth: l1 (>=1200 words)
search mode: auto
let me start by loading the skill and reading the outline.let me start by reading the skill.md file as required, and also read the outline t
**page scope**: p3 only
**research depth**: l1 (>=1200 words, >=3 sources per page)
**search mode**: auto
**output**: `research-p3.md` in the output directory
p3 details from outline:
- **title**: deep learning era — from alexnet to chatgpt (2006-2023)
- **type**: technology
- **research need**: yes
"""

# Scenario 3: digit-stripped same lexical key on research-p{n} file list.
WIN_SAME_KEY_DIGITS = """
- research-p2.md (约2650字)
- research-p3.md (约2100+字)
- research-p4.md (约2000+字)
- research-p5.md (约2200字)
- research-p6.md (约2300字)
- no need to read styles/{style_id}.md
for image preparation:
- {image_paths} is empty (no user-provided images)
"""

# Four disjoint similar pairs: union size=8, largest cluster size=2.
WIN_MULTI_SMALL_CLUSTERS = """
Comparison card height is about 280px for the layout.
Comparison card height is about 270px for the layout.
Winters card height is about 250px for the layout.
Winters card height is about 278px for the layout.
Title block uses 24px plus 8px margin totaling 32px.
Title block uses 24px and 8px margin totaling 32px.
Padding margin is about 38px around the card.
Padding margin is about 46px around the card.
"""


def _stream_chunk(text: str, chunk_type: str = "llm_output", member: str = "m") -> Signal:
    return Signal(
        kind=SignalKind.STREAM_CHUNK,
        member_name=member,
        chunk_type=chunk_type,
        chunk_text=text,
    )


class _CaptureAgents:
    def __init__(self, *, response: dict | str | None = None):
        self.calls: list[tuple[str, str, str, float]] = []
        self._response = response or {
            "abnormal": True,
            "primary_fault": "semantic_deadlock",
        }

    async def invoke_skill(self, *, role: str, skill_name: str, payload: str, timeout: float):
        self.calls.append((role, skill_name, payload, timeout))
        return self._response


class _SlowAgents:
    def __init__(self, *, delay: float, response: dict):
        self.delay = delay
        self.response = response

    async def invoke_skill(self, *, role: str, skill_name: str, payload: str, timeout: float):
        await asyncio.sleep(self.delay)
        return self.response


class TestSuffixCycle:
    @pytest.mark.asyncio
    async def test_ac001_suffix_cycle_low(self):
        det = LlmThinkingLoopDetector(
            LlmThinkingLoopConfig(
                detection_start_chars=1,
                window_max_chars=100,
                loop_repeat_threshold=5,
            )
        )
        pattern = "abcdefghij"
        text = pattern * 15  # >= window_max_chars (100)
        a = await det.observe(_stream_chunk(text))
        assert a is not None
        assert a.kind == AnomalyKind.LLM_THINKING_LOOP
        assert a.severity == Severity.LOW
        assert a.evidence.get("mode") == "suffix_cycle"
        assert a.evidence.get("channel") == "text_repetition"
        assert "start_pos" in a.evidence
        assert "threshold" in a.evidence

    def test_p_l1_loop_detector_suffix_cycle(self):
        detector = LoopDetector(min_text_length=10)
        result = detector.detect("abcdefghij" * 5, threshold=5)
        assert result.detected is True
        assert result.mode == "suffix_cycle"

    def test_p_l1_rangewozhu_repeated(self):
        """L1: Chinese phrase 「让我协助」 repeated N times (period via multi-copy >=10)."""
        # Single copy is 4 chars (< SUFFIX_CYCLE_MIN_PATTERN_LEN=10); 15 copies
        # yield a detectable period of 12 (=3 copies) with count>=5.
        text = "让我协助" * 15
        detector = LoopDetector(min_text_length=10)
        result = detector.detect(text, threshold=5)
        assert result.detected is True
        assert result.mode == "suffix_cycle"
        assert result.count >= 5

    @pytest.mark.asyncio
    async def test_p_l1_rangewozhu_via_observe(self):
        det = LlmThinkingLoopDetector(
            LlmThinkingLoopConfig(
                detection_start_chars=1,
                window_max_chars=100,
                loop_repeat_threshold=5,
            )
        )
        text = "让我协助" * 30  # >= window_max_chars
        a = await det.observe(_stream_chunk(text))
        assert a is not None
        assert a.evidence.get("mode") == "suffix_cycle"


class TestSimilarClauses:
    @pytest.mark.asyncio
    async def test_ac002_similar_clauses_medium(self):
        det = LlmThinkingLoopDetector(
            LlmThinkingLoopConfig(
                detection_start_chars=1,
                window_max_chars=100,
                loop_repeat_threshold=3,
                similar_clause_sim_threshold=0.9,
            )
        )
        core = (
            "明白，我先看看当前磁盘状况和之前的工作上下文，再开始测试。"
            "明白，我先看看当前磁盘状况和之前的工作进展，再开始测试。"
            "明白，我先看看当前磁盘状况和之前的上下文，再开始测试。"
        )
        # Prefix pad: L1/L2 FIFO keeps the tail within window_max_chars.
        text = ("前缀填充。" * 10) + core
        a = await det.observe(_stream_chunk(text))
        assert a is not None
        assert a.kind == AnomalyKind.LLM_THINKING_LOOP
        assert a.evidence.get("mode") == "similar_clauses"

    def test_p_l2_bare_step_digits_share_lexical_key(self):
        """Bare step digits stay out of lexical key so identical bodies collide."""
        k1 = _extract_lexical_key("第1步：检查配置是否正确")
        k2 = _extract_lexical_key("第2步：检查配置是否正确")
        assert k1 == k2
        assert "1" not in k1 and "2" not in k2

    def test_p_l2_identical_step_bodies_detected(self):
        """Incrementing steps with identical bodies must hit L2 (no enum exempt)."""
        text = "。".join(f"第{i}步：检查配置是否正确" for i in range(1, 6))
        detector = LoopDetector(min_text_length=10, similar_clause_sim_threshold=0.95)
        result = detector.detect(text, threshold=5)
        assert result.detected is True
        assert result.mode == "similar_clauses"

    def test_p_l2_different_step_bodies_not_detected(self):
        """Incrementing steps that do different work stay below similarity threshold."""
        bodies = [
            "检查磁盘剩余空间是否充足",
            "备份当前配置文件到临时目录",
            "重启相关服务并观察日志输出",
            "验证接口健康检查返回成功",
            "通知调用方可以继续发布版本",
        ]
        text = "。".join(f"第{i}步：{body}" for i, body in enumerate(bodies, start=1))
        detector = LoopDetector(min_text_length=10, similar_clause_sim_threshold=0.95)
        result = detector.detect(text, threshold=5)
        assert result.detected is False

    def test_p_l2_identical_step_en_bodies_detected(self):
        """English Step N with identical bodies must hit after enum exempt removal."""
        # Newline-separated so normalize can strip each ``Step N:`` line prefix.
        text = "\n".join(
            f"Step {i}: process item and verify result" for i in range(1, 6)
        )
        detector = LoopDetector(min_text_length=10, similar_clause_sim_threshold=0.95)
        result = detector.detect(text, threshold=5)
        assert result.detected is True
        assert result.mode == "similar_clauses"

    def test_p_l2_paraphrase_loop_still_detected(self):
        """Digit-free paraphrase loop must still hit L2 (regression vs scenario-3 fix)."""
        text = "。".join([
            "我需要再确认一下当前磁盘空间和网络连接是否正常",
            "我需要再确认一下当前磁盘空间和网络是否正常",
            "我需要再确认一下磁盘空间和网络连接是否正常",
            "我需要再确认一下当前的磁盘空间和网络连接是否正常",
            "我需要再确认一下磁盘空间与网络连接是否正常",
        ])
        detector = LoopDetector(min_text_length=10, similar_clause_sim_threshold=0.95)
        result = detector.detect(text, threshold=5)
        assert result.detected is True
        assert result.mode == "similar_clauses"

    def test_n_s3_research_p_list_not_detected(self):
        """Scenario 3: research-p2..p6 checklist must not false-positive after fix."""
        detector = LoopDetector(min_text_length=50, similar_clause_sim_threshold=0.95)
        result = detector.detect(WIN_SAME_KEY_DIGITS, threshold=5)
        assert result.detected is False

    def test_n_s3_lexical_key_keeps_glued_id_digits_drops_bare_counts(self):
        key_p2 = _extract_lexical_key("- research-p2.md (约2650字)")
        key_p3 = _extract_lexical_key("- research-p3.md (约2100+字)")
        assert "researchp2" in key_p2.split()
        assert "researchp3" in key_p3.split()
        assert "2650" not in key_p2
        assert "2100" not in key_p3
        assert key_p2 != key_p3

    def test_filename_underscore_digit_glues_into_token(self):
        """xxx_0 → xxx0 (separator deleted, not spaced); distinguishes xxx_1."""
        k0 = _extract_lexical_key("xxx_0.pdf")
        k1 = _extract_lexical_key("xxx_1.pdf")
        assert "xxx0" in k0.split()
        assert "xxx1" in k1.split()
        assert k0 != k1
        assert _clause_similarity("xxx_0.pdf", "xxx_1.pdf") < 0.95

    def test_filename_underscore_list_not_detected(self):
        text = "\n".join(f"- xxx_{i}.pdf (约{1000 + i * 50}字)" for i in range(5))
        detector = LoopDetector(min_text_length=20, similar_clause_sim_threshold=0.95)
        result = detector.detect(text, threshold=5)
        assert result.detected is False

    def test_n_s1_pixel_window_not_detected(self):
        """Pixel layout checklist: small pair clusters must not trip largest-CC L2."""
        detector = LoopDetector(min_text_length=100, similar_clause_sim_threshold=0.95)
        result = detector.detect(WIN_PIXEL, threshold=5)
        assert result.detected is False

    def test_n_s2_checklist_window_not_detected(self):
        """Multi-page research checklist boilerplate should not false-positive."""
        detector = LoopDetector(min_text_length=100, similar_clause_sim_threshold=0.95)
        result = detector.detect(WIN_CHECKLIST, threshold=5)
        assert result.detected is False

    def test_l2_largest_cluster_not_union_of_small_pairs(self):
        """Four size-2 clusters: union=8 would FP; largest CC=2 must not."""
        detector = LoopDetector(min_text_length=50, similar_clause_sim_threshold=0.95)
        result = detector.detect(WIN_MULTI_SMALL_CLUSTERS, threshold=5)
        assert result.detected is False

    def test_l2_single_large_cluster_still_detected(self):
        """One connected similar cluster >= threshold still hits."""
        text = "。".join(
            [
                "我需要再确认一下当前磁盘空间和网络连接是否正常",
                "我需要再确认一下当前磁盘空间和网络是否正常",
                "我需要再确认一下磁盘空间和网络连接是否正常",
                "我需要再确认一下当前的磁盘空间和网络连接是否正常",
                "我需要再确认一下磁盘空间与网络连接是否正常",
            ]
        )
        detector = LoopDetector(min_text_length=10, similar_clause_sim_threshold=0.95)
        result = detector.detect(text, threshold=5)
        assert result.detected is True
        assert result.mode == "similar_clauses"
        assert result.count >= 5


class TestSkillTrigger:
    @pytest.mark.asyncio
    async def test_ac003_skill_triggered_incremental_excerpt(self):
        config = LlmThinkingLoopConfig(
            semantic_eval_chars=200,
            semantic_content_enabled=True,
            detection_start_chars=1,
            window_max_chars=100,
            loop_repeat_threshold=3,
        )
        agents = _CaptureAgents()
        captured: list = []

        async def handler(anomaly, chunk_type):
            captured.append((anomaly, chunk_type))

        det = LlmThinkingLoopDetector(config, agents=agents)
        det.set_async_recovery_handler(handler)
        a = await det.observe(_stream_chunk("x" * 200))
        assert a is None
        assert det.has_eval_in_flight()
        for _ in range(100):
            if agents.calls:
                break
            await asyncio.sleep(0.01)
        assert len(agents.calls) >= 1
        assert len(agents.calls[0][2]) == 200
        for _ in range(100):
            if captured:
                break
            await asyncio.sleep(0.01)
        assert len(captured) == 1
        anomaly, chunk_type = captured[0]
        assert chunk_type == "llm_output"
        assert anomaly.kind == AnomalyKind.LLM_THINKING_DEAD_LOOP
        assert anomaly.evidence.get("mode") == "plan_execution_loop_lock"
        assert anomaly.evidence.get("channel") == "plan_execution"
        assert anomaly.evidence.get("thinking_excerpt") == "x" * 200
        assert anomaly.evidence.get("primary_fault") == "semantic_deadlock"

    @pytest.mark.asyncio
    async def test_ac003b_abnormal_json_response(self):
        config = LlmThinkingLoopConfig(
            semantic_eval_chars=200,
            semantic_content_enabled=True,
            detection_start_chars=1,
            window_max_chars=100,
        )
        agents = _CaptureAgents(response={
            "abnormal": True,
            "primary_fault": "text_degradation",
            "rationale": "garbled text",
        })
        captured: list = []

        async def handler(anomaly, chunk_type):
            captured.append(anomaly)

        det = LlmThinkingLoopDetector(config, agents=agents)
        det.set_async_recovery_handler(handler)
        a = await det.observe(_stream_chunk("x" * 200))
        assert a is None
        for _ in range(100):
            if captured:
                break
            await asyncio.sleep(0.01)
        assert len(captured) == 1
        anomaly = captured[0]
        assert anomaly.kind == AnomalyKind.LLM_THINKING_DEAD_LOOP
        assert anomaly.evidence.get("primary_fault") == "text_degradation"
        assert anomaly.evidence.get("skill_rationale") == "garbled text"

    @pytest.mark.asyncio
    async def test_ac004_skill_and_condition_not_met(self):
        config = LlmThinkingLoopConfig(
            semantic_eval_chars=10000,
            semantic_content_enabled=True,
            detection_start_chars=1,
            window_max_chars=100,
            loop_repeat_threshold=3,
        )
        agents = _CaptureAgents()
        det = LlmThinkingLoopDetector(config, agents=agents)
        await det.observe(_stream_chunk("x" * 3000))
        assert agents.calls == []
        await det.observe(_stream_chunk("x" * 1500))
        assert agents.calls == []


class TestFailOpen:
    @pytest.mark.asyncio
    async def test_ac005_skill_timeout_fail_open(self):
        config = LlmThinkingLoopConfig(
            semantic_eval_chars=200,
            semantic_content_enabled=True,
            detection_start_chars=1,
            window_max_chars=100,
        )

        class TimeoutAgents:
            async def invoke_skill(self, *, role, skill_name, payload, timeout):
                raise asyncio.TimeoutError("timed out")

        det = LlmThinkingLoopDetector(config, agents=TimeoutAgents())
        a = await det.observe(_stream_chunk("x" * 200))
        assert a is None

    @pytest.mark.asyncio
    async def test_ac06_skill_invalid_json_fail_open(self):
        config = LlmThinkingLoopConfig(
            semantic_eval_chars=200,
            semantic_content_enabled=True,
            detection_start_chars=1,
            window_max_chars=100,
        )

        class InvalidAgents:
            async def invoke_skill(self, *, role, skill_name, payload, timeout):
                return "maybe"

        det = LlmThinkingLoopDetector(config, agents=InvalidAgents())
        a = await det.observe(_stream_chunk("x" * 200))
        assert a is None

    @pytest.mark.asyncio
    async def test_ac06b_abnormal_false_fail_open(self):
        config = LlmThinkingLoopConfig(
            semantic_eval_chars=200,
            semantic_content_enabled=True,
            detection_start_chars=1,
            window_max_chars=100,
        )
        agents = _CaptureAgents(response={
            "abnormal": False,
            "primary_fault": "none",
        })
        det = LlmThinkingLoopDetector(config, agents=agents)
        a = await det.observe(_stream_chunk("x" * 200))
        assert a is None


class TestChannelIndependence:
    @pytest.mark.asyncio
    async def test_ac007_dual_channel_independent(self):
        det = LlmThinkingLoopDetector(
            LlmThinkingLoopConfig(detection_start_chars=1, window_max_chars=10000)
        )
        for _ in range(100):
            await det.observe(_stream_chunk("x" * 10, chunk_type="llm_output"))
        for _ in range(100):
            await det.observe(_stream_chunk("y" * 10, chunk_type="llm_reasoning"))
        assert det._buffers["llm_output"][0] == "x" * 1000
        assert det._buffers["llm_reasoning"][0] == "y" * 1000
        assert det._plan_exec_channel("llm_output").buffer == "x" * 1000
        assert det._plan_exec_channel("llm_reasoning").buffer == "y" * 1000

    @pytest.mark.asyncio
    async def test_unknown_channel_ignored(self):
        det = LlmThinkingLoopDetector(LlmThinkingLoopConfig())
        for _ in range(10):
            await det.observe(_stream_chunk("x", chunk_type="unknown_type"))
        assert "unknown_type" not in det._buffers


class TestTextRepetitionGating:
    @pytest.mark.asyncio
    async def test_l1_l2_waits_for_window_max_chars(self):
        det = LlmThinkingLoopDetector(
            LlmThinkingLoopConfig(detection_start_chars=1,
            window_max_chars=100, loop_repeat_threshold=5)
        )
        pattern = "abcdefghij"
        a = await det.observe(_stream_chunk(pattern * 5))
        assert a is None
        a = await det.observe(_stream_chunk(pattern * 5))
        assert a is not None
        assert a.evidence.get("mode") == "suffix_cycle"


class TestBeforeModelCallReset:
    @pytest.mark.asyncio
    async def test_before_model_call_clears_buffers(self):
        det = LlmThinkingLoopDetector(LlmThinkingLoopConfig())
        await det.observe(_stream_chunk("x" * 500))
        assert det._buffers["llm_output"][1] == 500
        await det.observe(Signal(kind=SignalKind.BEFORE_MODEL_CALL, member_name="m"))
        assert "llm_output" not in det._buffers


class TestPlanExecutionBuffer:
    @pytest.mark.asyncio
    async def test_await_in_flight_evals_waits_for_l3(self):
        config = LlmThinkingLoopConfig(
            semantic_eval_chars=200,
            semantic_content_enabled=True,
            detection_start_chars=1,
            window_max_chars=100,
        )
        det = LlmThinkingLoopDetector(config)
        ch = det._plan_exec_channel("llm_output")
        ch.eval_in_flight = True

        async def release():
            await asyncio.sleep(0.1)
            det.release_eval_in_flight()

        asyncio.create_task(release())
        await det.await_in_flight_evals(timeout=2.0)
        assert not det.has_eval_in_flight()

    @pytest.mark.asyncio
    async def test_l3_background_eval_non_blocking(self):
        config = LlmThinkingLoopConfig(
            semantic_eval_chars=200,
            semantic_content_enabled=True,
            detection_start_chars=1,
            window_max_chars=100,
        )

        class SlowAgents:
            async def invoke_skill(self, *, role, skill_name, payload, timeout):
                await asyncio.sleep(0.05)
                return {"abnormal": True, "primary_fault": "semantic_deadlock"}

        det = LlmThinkingLoopDetector(config, agents=SlowAgents())
        started = time.monotonic()
        a = await det.observe(_stream_chunk("x" * 200))
        elapsed = time.monotonic() - started
        assert a is None
        assert elapsed < 0.04
        assert det.has_eval_in_flight()
        await det.await_in_flight_evals(timeout=2.0)
        assert not det.has_eval_in_flight()

    @pytest.mark.asyncio
    async def test_l3_create_task_uses_empty_context(self):
        config = LlmThinkingLoopConfig(
            semantic_eval_chars=200,
            semantic_content_enabled=True,
            detection_start_chars=1,
            window_max_chars=100,
        )

        class InstantAgents:
            async def invoke_skill(self, *, role, skill_name, payload, timeout):
                return {"abnormal": False, "primary_fault": "none"}

        det = LlmThinkingLoopDetector(config, agents=InstantAgents())
        captured: dict = {}
        real_create_task = asyncio.create_task

        def spy_create_task(coro, *args, **kwargs):
            captured["kwargs"] = dict(kwargs)
            return real_create_task(coro, *args, **kwargs)

        with patch(
            "openjiuwen.harness.agent_ras.detectors.llm_thinking_loop.asyncio.create_task",
            side_effect=spy_create_task,
        ):
            await det.observe(_stream_chunk("x" * 200))
            await det.await_in_flight_evals(timeout=2.0)

        assert "context" in captured.get("kwargs", {})
        assert isinstance(captured["kwargs"]["context"], contextvars.Context)

    @pytest.mark.asyncio
    async def test_abnormal_keeps_eval_in_flight_until_release(self):
        config = LlmThinkingLoopConfig(
            semantic_eval_chars=200,
            semantic_content_enabled=True,
            detection_start_chars=1,
            window_max_chars=100,
        )
        gate = asyncio.Event()

        async def slow_handler(anomaly, chunk_type):
            await gate.wait()

        det = LlmThinkingLoopDetector(config, agents=_CaptureAgents())
        det.set_async_recovery_handler(slow_handler)
        await det.observe(_stream_chunk("x" * 200))
        for _ in range(100):
            if det._plan_exec_channel("llm_output").eval_in_flight:
                break
            await asyncio.sleep(0.01)
        assert det.has_eval_in_flight()
        gate.set()
        await asyncio.sleep(0.05)
        assert det.has_eval_in_flight()
        det.release_eval_in_flight()
        assert not det.has_eval_in_flight()

    @pytest.mark.asyncio
    async def test_abnormal_delays_release_until_handler_finishes(self):
        config = LlmThinkingLoopConfig(
            semantic_eval_chars=200,
            semantic_content_enabled=True,
            detection_start_chars=1,
            window_max_chars=100,
        )
        pending_applied = asyncio.Event()

        async def handler(anomaly, chunk_type):
            await asyncio.sleep(0.05)
            pending_applied.set()

        det = LlmThinkingLoopDetector(config, agents=_CaptureAgents())
        det.set_async_recovery_handler(handler)
        await det.observe(_stream_chunk("x" * 200))
        for _ in range(100):
            if det._plan_exec_channel("llm_output").eval_in_flight:
                break
            await asyncio.sleep(0.01)
        assert det.has_eval_in_flight()
        assert not pending_applied.is_set()
        await pending_applied.wait()
        assert det.has_eval_in_flight()
        det.release_eval_in_flight()
        assert not det.has_eval_in_flight()

    @pytest.mark.asyncio
    async def test_abnormal_false_clears_eval_in_flight_immediately(self):
        config = LlmThinkingLoopConfig(
            semantic_eval_chars=200,
            semantic_content_enabled=True,
            detection_start_chars=1,
            window_max_chars=100,
        )
        det = LlmThinkingLoopDetector(
            config,
            agents=_CaptureAgents(response={"abnormal": False}),
        )
        await det.observe(_stream_chunk("x" * 200))
        for _ in range(100):
            if not det.has_eval_in_flight():
                break
            await asyncio.sleep(0.01)
        assert not det.has_eval_in_flight()

    @pytest.mark.asyncio
    async def test_reset_cancels_background_eval_and_blocks_stale_handler(self):
        config = LlmThinkingLoopConfig(
            semantic_eval_chars=200,
            semantic_content_enabled=True,
            detection_start_chars=1,
            window_max_chars=100,
        )
        handler_calls: list = []

        async def handler(anomaly, chunk_type):
            handler_calls.append(anomaly)

        det = LlmThinkingLoopDetector(
            config,
            agents=_SlowAgents(delay=0.2, response={"abnormal": True, "primary_fault": "text_degradation"}),
        )
        det.set_async_recovery_handler(handler)
        await det.observe(_stream_chunk("x" * 200))
        assert det.has_eval_in_flight()
        det.reset()
        assert not det.has_eval_in_flight()
        await asyncio.sleep(0.3)
        assert handler_calls == []

    @pytest.mark.asyncio
    async def test_await_in_flight_evals_times_out_and_releases(self):
        config = LlmThinkingLoopConfig(
            semantic_eval_chars=200,
            semantic_content_enabled=True,
            detection_start_chars=1,
            window_max_chars=100,
        )
        det = LlmThinkingLoopDetector(
            config,
            agents=_SlowAgents(delay=5.0, response={"abnormal": False}),
        )
        await det.observe(_stream_chunk("x" * 200))
        assert det.has_eval_in_flight()
        await det.await_in_flight_evals(0.1)
        assert not det.has_eval_in_flight()

    @pytest.mark.asyncio
    async def test_plan_execution_not_window_trimmed(self):
        config = LlmThinkingLoopConfig(
            window_max_chars=200,
            detection_start_chars=1,
            semantic_eval_chars=5000,
            semantic_content_enabled=True,
        )
        det = LlmThinkingLoopDetector(config)
        for _ in range(30):
            await det.observe(_stream_chunk("z" * 100))
        assert len(det._plan_exec_channel("llm_output").buffer) == 3000
        assert len(det._buffers["llm_output"][0]) <= config.window_max_chars


class TestWindowTruncation:
    @pytest.mark.asyncio
    async def test_ac009_window_truncation(self):
        config = LlmThinkingLoopConfig(
            window_max_chars=200,
            detection_start_chars=1,
            loop_repeat_threshold=3,
        )
        det = LlmThinkingLoopDetector(config)
        for _ in range(10):
            await det.observe(_stream_chunk("x" * 100))
        assert len(det._buffers["llm_output"][0]) <= config.window_max_chars
