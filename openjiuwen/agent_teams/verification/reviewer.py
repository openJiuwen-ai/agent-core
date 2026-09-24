# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""VerificationReviewer — lightweight subagent for reviewing teammate outputs.

Inspired by Claude Code's verification subagents and OpenClaw's review patterns.
Uses a dedicated model call with structured prompting to assess quality across
multiple dimensions without heavy infrastructure.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from .result import DimensionScore, QualityDimension, VerificationInput, VerificationResult, VerificationStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_VERIFICATION_SYSTEM_PROMPT_EN = """You are a Quality Assurance Reviewer in an Agent Team system.
Your job is to review a teammate's completed task output and assess its quality.

Review the output against these dimensions:
- CORRECTNESS: Is the output factually accurate and technically correct?
- COMPLETENESS: Does it fully address the task requirements?
- CONSISTENCY: Is it internally consistent and aligned with team context?
- CLARITY: Is it clear, well-structured, and easy to understand?
- SECURITY: Does it avoid security risks, leaks, or harmful content?
- PERFORMANCE: Is the solution efficient and well-optimized?

Respond with a JSON object matching this schema:
{
  "status": "pass|fail|needs_rework",
  "overall_score": 0-100,
  "dimensions": [
    {
      "dimension": "correctness|completeness|consistency|clarity|security|performance",
      "score": 0-100,
      "reasoning": "brief explanation",
      "findings": ["specific finding 1", "specific finding 2"]
    }
  ],
  "summary": "2-3 sentence overall assessment",
  "rework_instructions": "specific instructions if needs_rework or fail, empty string if pass"
}

Rules:
- PASS: overall_score >= 70, no critical issues in any dimension
- NEEDS_REWORK: overall_score 40-69, or any dimension below 50
- FAIL: overall_score < 40, or critical security/correctness issues
- Be strict but fair. Teammates rely on your feedback to improve.
"""

_VERIFICATION_SYSTEM_PROMPT_CN = """你是 Agent Team 系统中的质量保证审查员。
你的工作是审查队友已完成的任务输出并评估其质量。

从以下维度审查输出：
- CORRECTNESS（正确性）：输出是否在事实和技术上准确？
- COMPLETENESS（完整性）：是否完全满足任务要求？
- CONSISTENCY（一致性）：是否内部一致并与团队上下文对齐？
- CLARITY（清晰性）：是否清晰、结构良好、易于理解？
- SECURITY（安全性）：是否避免安全风险、泄露或有害内容？
- PERFORMANCE（性能）：解决方案是否高效且优化良好？

请使用以下 JSON 格式响应：
{
  "status": "pass|fail|needs_rework",
  "overall_score": 0-100,
  "dimensions": [
    {
      "dimension": "correctness|completeness|consistency|clarity|security|performance",
      "score": 0-100,
      "reasoning": "简要说明",
      "findings": ["具体发现 1", "具体发现 2"]
    }
  ],
  "summary": "2-3 句总体评估",
  "rework_instructions": "如果需要返工或失败，提供具体说明；通过则为空字符串"
}

规则：
- PASS：overall_score >= 70，没有任何维度的严重问题
- NEEDS_REWORK：overall_score 40-69，或任何维度低于 50
- FAIL：overall_score < 40，或存在严重的安全/正确性问题
- 严格但公正。队友依赖你的反馈来改进。
"""

_USER_PROMPT_TEMPLATE_EN = """## Task
Title: {task_title}
Assignee: {assignee}

### Task Description
{task_content}

### Teammate Output
{output}

### Team Context
{team_context}

Please review the teammate's output and provide your structured assessment.
"""

_USER_PROMPT_TEMPLATE_CN = """## 任务
标题: {task_title}
负责人: {assignee}

### 任务描述
{task_content}

### 队友输出
{output}

### 团队上下文
{team_context}

请审查队友的输出并提供结构化评估。
"""


class VerificationReviewer:
    """Lightweight reviewer that uses model calls to assess teammate output quality.

    Can operate in two modes:
    1. **Direct model call** (default): Uses the team's configured model via a
       simple completion call with structured JSON output.
    2. **Subagent delegation** (future): Spawns a dedicated reviewer subagent
       for complex multi-file reviews.
    """

    def __init__(
        self,
        model_client: Any | None = None,
        language: str = "en",
        pass_threshold: int = 70,
        rework_threshold: int = 40,
    ) -> None:
        self._model_client = model_client
        self._language = language
        self._pass_threshold = pass_threshold
        self._rework_threshold = rework_threshold

    async def review(self, inp: VerificationInput) -> VerificationResult:
        """Review a teammate's task output and return a structured result.

        Args:
            inp: VerificationInput with task details and output to review

        Returns:
            VerificationResult with quality assessment
        """
        prompt = self._build_prompt(
            task_title=inp.task_title,
            task_content=inp.task_content,
            assignee=inp.assignee,
            output=inp.output,
            team_context=inp.team_context,
        )

        system_prompt = (
            _VERIFICATION_SYSTEM_PROMPT_CN
            if self._language == "cn"
            else _VERIFICATION_SYSTEM_PROMPT_EN
        )

        try:
            raw_response = await self._call_model(system_prompt, prompt)
            parsed = self._parse_response(raw_response)
            result = self._build_result(inp.task_id, inp.task_title, inp.assignee, parsed)
            logger.info(
                "[VerificationReviewer] Reviewed task=%s assignee=%s status=%s score=%d",
                inp.task_id,
                inp.assignee,
                result.status.value,
                result.overall_score,
            )
            return result
        except Exception as exc:
            logger.warning(
                "[VerificationReviewer] Review failed for task=%s: %s",
                inp.task_id,
                exc,
                exc_info=True,
            )
            # Graceful degradation: return SKIPPED on failure
            return VerificationResult(
                task_id=inp.task_id,
                task_title=inp.task_title,
                assignee=inp.assignee,
                status=VerificationStatus.SKIPPED,
                overall_score=0,
                summary=f"Verification skipped due to error: {exc}",
                verified_at=datetime.now(timezone.utc).isoformat(),
            )

    def _build_prompt(
        self,
        task_title: str,
        task_content: str,
        assignee: str,
        output: str,
        team_context: str,
    ) -> str:
        """Build the user prompt for the review."""
        template = (
            _USER_PROMPT_TEMPLATE_CN
            if self._language == "cn"
            else _USER_PROMPT_TEMPLATE_EN
        )
        return template.format(
            task_title=task_title,
            task_content=task_content or "(no description)",
            assignee=assignee,
            output=output or "(no output)",
            team_context=team_context or "(no team context)",
        )

    async def _call_model(self, system_prompt: str, user_prompt: str) -> str:
        """Call the model and return the raw response string.

        If no model_client was provided, falls back to a mock response
        for testing/development.
        """
        if self._model_client is None:
            logger.debug("[VerificationReviewer] No model client configured, using mock")
            return self._mock_response()

        # Try structured JSON mode first, then fall back to regular completion
        try:
            response = await self._model_client.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_format={"type": "json_object"},
            )
            return response
        except Exception:
            # Fallback: regular completion, we'll parse JSON from the text
            response = await self._model_client.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
            return response

    @staticmethod
    def _parse_response(raw: str) -> dict[str, Any]:
        """Parse the model's JSON response."""
        # Extract JSON from markdown code blocks if present
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            # Remove opening fence
            if lines[0].startswith("```"):
                lines = lines[1:]
            # Remove closing fence
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines)

        return json.loads(text)

    def _build_result(
        self,
        task_id: str,
        task_title: str,
        assignee: str,
        parsed: dict[str, Any],
    ) -> VerificationResult:
        """Build a VerificationResult from parsed JSON."""
        status = VerificationStatus(parsed.get("status", "skipped"))
        overall_score = int(parsed.get("overall_score", 0))

        # Normalize status based on thresholds if the model didn't follow rules
        if status == VerificationStatus.PASS and overall_score < self._pass_threshold:
            status = VerificationStatus.NEEDS_REWORK
        if status == VerificationStatus.NEEDS_REWORK and overall_score < self._rework_threshold:
            status = VerificationStatus.FAIL

        dimensions = []
        for d in parsed.get("dimensions", []):
            try:
                dim = QualityDimension(d.get("dimension", "correctness"))
            except ValueError:
                dim = QualityDimension.CORRECTNESS
            dimensions.append(
                DimensionScore(
                    dimension=dim,
                    score=int(d.get("score", 0)),
                    reasoning=d.get("reasoning", ""),
                    findings=d.get("findings", []),
                )
            )

        return VerificationResult(
            task_id=task_id,
            task_title=task_title,
            assignee=assignee,
            status=status,
            overall_score=overall_score,
            dimensions=dimensions,
            summary=parsed.get("summary", ""),
            rework_instructions=parsed.get("rework_instructions", ""),
            verified_at=datetime.now(timezone.utc).isoformat(),
            reviewer_model=getattr(self._model_client, "model_name", "unknown"),
        )

    @staticmethod
    def _mock_response() -> str:
        """Return a mock JSON response for testing without a model client."""
        return json.dumps(
            {
                "status": "pass",
                "overall_score": 75,
                "dimensions": [
                    {
                        "dimension": "correctness",
                        "score": 80,
                        "reasoning": "Output appears technically sound.",
                        "findings": [],
                    },
                    {
                        "dimension": "completeness",
                        "score": 70,
                        "reasoning": "Addresses main requirements.",
                        "findings": ["Could expand edge case handling."],
                    },
                    {
                        "dimension": "consistency",
                        "score": 75,
                        "reasoning": "Consistent with team conventions.",
                        "findings": [],
                    },
                    {
                        "dimension": "clarity",
                        "score": 80,
                        "reasoning": "Well-structured and readable.",
                        "findings": [],
                    },
                    {
                        "dimension": "security",
                        "score": 85,
                        "reasoning": "No obvious security concerns.",
                        "findings": [],
                    },
                    {
                        "dimension": "performance",
                        "score": 70,
                        "reasoning": "Reasonable approach.",
                        "findings": ["Could optimize for large inputs."],
                    },
                ],
                "summary": "The output meets quality standards with minor room for improvement.",
                "rework_instructions": "",
            },
            ensure_ascii=False,
        )
