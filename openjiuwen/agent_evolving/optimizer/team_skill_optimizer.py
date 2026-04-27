# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""TeamSkillOptimizer: LLM-based patch generation for team skills."""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Dict, List, Optional

from openjiuwen.agent_evolving.checkpointing.types import (
    EvolutionPatch,
    EvolutionRecord,
    EvolutionTarget,
)
from openjiuwen.agent_evolving.optimizer.llm_resilience import (
    LLMInvokePolicy,
    invoke_text_with_retry,
)
from openjiuwen.agent_evolving.trajectory import Trajectory
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model


_PROPOSE_PROMPT_CN = """\
你是一个多角色协作 Skill 设计专家。根据 AgentTeam 的完整执行 trajectory，判断是否值得提炼一个可复用的 Team Skill。

## Trajectory 摘要
{trajectory_summary}

## 已有 Team Skill 列表
{existing_skill_names}

## Team Skill 文件结构规范

Team Skill 是一个目录，包含以下必填文件：

1. **SKILL.md** — 入口文件，包含 YAML frontmatter + Markdown body
   - frontmatter 必须包含：name（kebab-case）、description（触发条件+适用场景）、kind: team-skill
   - frontmatter 的 roles 列表：每个角色的 id（kebab-case）、可选的 skills/tools/model
   - body 概览各角色分工，并引用子文件（如"详见 roles/reviewer.md"）

2. **roles/<role-id>.md** — 每个角色一个文件，文件名 = role id（kebab-case）
   - 三段式：(1) 专业背景与技术栈；(2) 优先认领的任务范围；(3) 不负责的边界
   - 内容会被 Leader 塞进 spawn_member.desc 参数

3. **workflow.md** — 任务流程
   - 自然语言描述或 Mermaid 图；标注任务依赖关系（哪些可并行、哪些串行）

4. **bind.md** — 约束信息
   - 防止执行发散：消息轮数上限、单任务超时、质量门等

## 判断规则（重要）
**只要 trajectory 中有 ≥ 2 次 spawn_member 调用，你必须输出 should_create=true，并尽你所能提炼出一个有意义的 Team Skill。**
不要以"模式不够通用""协作太简单""没有学到新东西"等理由拒绝；这些判断由后续审批环节处理，你的职责只是从 trajectory 提炼并输出结构化 Team Skill 草案。
即便协作很简单，也要按 trajectory 中实际出现的角色分工，老老实实写出 SKILL.md / roles / workflow / bind 四个文件。

## 输出格式
如果值得提炼，输出：
```json
{{
  "should_create": true,
  "name": "kebab-case-skill-name",
  "description": "一句话描述适用场景和触发条件",
  "body": "SKILL.md 的 body 部分（Markdown）",
  "reason": "为什么值得提炼",
  "roles": [
    {{"id": "role-id", "skills": [], "tools": []}},
  ],
  "extra_files": {{
    "roles/role-id.md": "角色描述全文...",
    "workflow.md": "工作流描述全文...",
    "bind.md": "约束描述全文..."
  }}
}}
```

如果不值得，输出：
```json
{{"should_create": false, "reason": "..."}}
```

只输出 JSON。"""

_PROPOSE_PROMPT_EN = """\
You are a multi-agent collaboration Skill designer. Based on the complete AgentTeam execution trajectory, determine whether a reusable Team Skill should be extracted.

## Trajectory Summary
{trajectory_summary}

## Existing Team Skills
{existing_skill_names}

## Team Skill File Structure

A Team Skill is a directory with these required files:

1. **SKILL.md** — entry file with YAML frontmatter + Markdown body
   - frontmatter must include: name (kebab-case), description (trigger + scenario), kind: team-skill
   - frontmatter roles list: each role's id (kebab-case), optional skills/tools/model
   - body overviews role assignments and references sub-files

2. **roles/<role-id>.md** — one file per role, filename = role id (kebab-case)
   - Three sections: (1) expertise & tech stack; (2) task scope; (3) out-of-scope boundaries
   - Content is passed to spawn_member.desc

3. **workflow.md** — task flow with dependency annotations (parallel vs sequential)

4. **bind.md** — constraints (round limits, timeouts, quality gates)

## Criteria
1. Trajectory has ≥ 2 roles (≥ 2 spawn_member calls)
2. Clear role differentiation
3. Reusable workflow pattern
4. Not covered by existing Team Skills

## Output Format
If worth extracting:
```json
{{
  "should_create": true,
  "name": "kebab-case-skill-name",
  "description": "One-sentence trigger and scenario description",
  "body": "SKILL.md body (Markdown)",
  "reason": "Why extract this",
  "roles": [
    {{"id": "role-id", "skills": [], "tools": []}}
  ],
  "extra_files": {{
    "roles/role-id.md": "Role description...",
    "workflow.md": "Workflow description...",
    "bind.md": "Constraints..."
  }}
}}
```

If not:
```json
{{"should_create": false, "reason": "..."}}
```

Output JSON only."""

_PATCH_PROMPT_CN = """\
你是 Team Skill 演进专家。对比本次 AgentTeam 执行 trajectory 与当前 Team Skill 内容，判断有没有值得沉淀的经验。

## 当前 Team Skill 内容
{skill_content}

## 本次 Trajectory 摘要
{trajectory_summary}

## 决策原则
- **大多数情况应该输出 need_patch=false**——只有真正学到了新东西才值得沉淀
- 值得沉淀的好例子：角色间沟通出了问题、某个步骤应该调整顺序、缺少边界约束导致发散
- 不值得沉淀的：正常执行无异常、仅有微小措辞差异

## 输出格式
```json
{{
  "need_patch": true/false,
  "section": "body 中的章节名（如 Workflow、Critical Rules 等）",
  "content": "Markdown 格式的经验内容",
  "reason": "为什么值得沉淀（仅 need_patch=true 时填写）"
}}
```

只输出 JSON。"""

_PATCH_PROMPT_EN = """\
You are a Team Skill evolution expert. Compare this execution trajectory against the current Team Skill and determine if any new learnings should be captured.

## Current Team Skill Content
{skill_content}

## Trajectory Summary
{trajectory_summary}

## Decision Principles
- **Most of the time you should output need_patch=false** — only patch when genuinely new insight exists
- Good examples: role communication issues, step ordering improvements, missing constraints
- Not worth patching: normal successful execution, minor wording differences

## Output Format
```json
{{
  "need_patch": true/false,
  "section": "Section name in body (e.g. Workflow, Critical Rules)",
  "content": "Markdown experience content",
  "reason": "Why worth capturing (only when need_patch=true)"
}}
```

Output JSON only."""

_PROPOSE_PROMPT = {"cn": _PROPOSE_PROMPT_CN, "en": _PROPOSE_PROMPT_EN}
_PATCH_PROMPT = {"cn": _PATCH_PROMPT_CN, "en": _PATCH_PROMPT_EN}

_USER_PATCH_PROMPT_CN = """\
根据用户的改进意见，为团队技能生成演进 patch。

当前团队技能：
- 名称：{skill_name}
- 描述：{description}
- 角色：{roles_summary}
- 工作流：{workflow_summary}

用户意见：{user_intent}

请分析用户意见属于以下哪类演进：
- Roles：角色增删或数量调整
- Constraints：新增或修改约束
- Collaboration：角色间协作经验
- Instructions：角色职责或任务指引
- Examples：协作流程示例
- Troubleshooting：问题排查

生成 patch，包含：
- section: 上述之一
- action: append
- content: 具体的演进内容

输出格式：
```json
{{
  "section": "章节名",
  "action": "append",
  "content": "Markdown 格式的经验内容"
}}
```

只输出 JSON。"""

_USER_PATCH_PROMPT_EN = """\
Based on the user's improvement suggestions, generate an evolution patch for the team skill.

Current team skill:
- Name: {skill_name}
- Description: {description}
- Roles: {roles_summary}
- Workflow: {workflow_summary}

User suggestion: {user_intent}

Please classify the user feedback into one of these evolution categories:
- Roles: role addition/removal or count adjustment
- Constraints: new or modified constraints
- Collaboration: inter-role collaboration experience
- Instructions: role responsibilities or task guidance
- Examples: collaboration workflow examples
- Troubleshooting: problem resolution

Generate a patch with:
- section: one of the above
- action: append
- content: specific evolution content in Markdown

Output format:
```json
{{
  "section": "section name",
  "action": "append",
  "content": "Markdown formatted experience content"
}}
```

Output JSON only."""

_USER_PATCH_PROMPT = {"cn": _USER_PATCH_PROMPT_CN, "en": _USER_PATCH_PROMPT_EN}


_TRAJECTORY_PATCH_PROMPT_CN = """\
分析以下执行轨迹，判断团队技能是否需要演进。

当前团队技能：{skill_content}
执行轨迹：{trajectory_summary}
轨迹分析发现的不足：{trajectory_issues}

如果轨迹显示团队技能存在不足（角色配合不当、约束被违反、流程低效），
生成演进 patch。多数情况下不需要 patch（need_patch=false）。

输出格式：
```json
{{
  "need_patch": true/false,
  "section": "章节名（如 Workflow、Collaboration、Constraints 等）",
  "content": "Markdown 格式的经验内容",
  "reason": "为什么值得沉淀（仅 need_patch=true 时填写）"
}}
```

只输出 JSON。"""

_TRAJECTORY_PATCH_PROMPT_EN = """\
Analyze the following execution trajectory and determine whether the team skill needs evolution.

Current team skill: {skill_content}
Trajectory summary: {trajectory_summary}
Detected issues: {trajectory_issues}

If the trajectory shows team skill deficiencies (poor role coordination, constraint violations, inefficient workflows),
generate an evolution patch. Most of the time need_patch should be false.

Output format:
```json
{{
  "need_patch": true/false,
  "section": "section name (e.g. Workflow, Collaboration, Constraints)",
  "content": "Markdown formatted experience content",
  "reason": "Why worth capturing (only when need_patch=true)"
}}
```

Output JSON only."""

_TRAJECTORY_PATCH_PROMPT = {"cn": _TRAJECTORY_PATCH_PROMPT_CN, "en": _TRAJECTORY_PATCH_PROMPT_EN}

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)

_TEAM_SKILL_PATCH_LLM_POLICY = LLMInvokePolicy(
    attempt_timeout_secs=40,
    total_budget_secs=75,
    max_attempts=2,
)

_PATCH_RETRY_SKILL_CONTENT_CHARS = 3000
_PATCH_RETRY_TRAJECTORY_CHARS = 6000
_TRAJECTORY_ISSUES_RETRY_CHARS = 2000
_USER_INTENT_RETRY_CHARS = 500
_SUMMARY_RETRY_CHARS = 200


def _try_parse_json(text: str) -> Optional[Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _fix_json_text(text: str) -> str:
    """Common LLM-output fixes: strip code fences, remove trailing commas / line comments."""
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"```\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text.strip()


def _extract_balanced_object(text: str, opener: str, closer: str) -> Optional[str]:
    """Scan text and return the first balanced {...} or [...] substring.

    Aware of JSON string boundaries so braces/brackets inside strings are ignored.
    More reliable than a greedy regex when LLM output contains code blocks
    that themselves include braces.
    """
    start = text.find(opener)
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


class TeamSkillOptimizer:
    """LLM-based patch generation and rewrite support for team skills."""

    def __init__(
        self,
        llm: Model,
        model: str,
        language: str = "cn",
        debug_dir: Optional[str] = None,
    ) -> None:
        self._llm = llm
        self._model = model
        self._language = language
        self._debug_dir = debug_dir

    # Public properties for external access
    @property
    def language(self) -> str:
        """Get the language setting (cn or en)."""
        return self._language

    @property
    def llm(self) -> Model:
        """Get the LLM client."""
        return self._llm

    @property
    def model(self) -> str:
        """Get the model name."""
        return self._model

    def update_llm(self, llm: Model, model: str) -> None:
        self._llm = llm
        self._model = model

    async def generate_patch(
        self,
        trajectory: Trajectory,
        skill_name: str,
        current_skill_content: str,
    ) -> Optional[EvolutionRecord]:
        """Analyze trajectory against existing skill and generate a patch if warranted."""
        summary = self._build_trajectory_summary(trajectory)
        logger.info(
            "[TeamSkillOptimizer] patch: skill='%s', summary_len=%d, content_len=%d",
            skill_name,
            len(summary),
            len(current_skill_content),
        )

        prompt = _PATCH_PROMPT.get(self._language, _PATCH_PROMPT_EN).format(
            skill_content=current_skill_content[:15000],
            trajectory_summary=summary,
        )
        retry_prompt = _PATCH_PROMPT.get(self._language, _PATCH_PROMPT_EN).format(
            skill_content=current_skill_content[:_PATCH_RETRY_SKILL_CONTENT_CHARS],
            trajectory_summary=summary[:_PATCH_RETRY_TRAJECTORY_CHARS],
        )
        approx_tokens = len(prompt) // 4
        logger.info(
            "[TeamSkillOptimizer] patch: prompt_len=%d (~%d tokens), model=%s",
            len(prompt),
            approx_tokens,
            self._model,
        )

        t0 = time.time()
        raw = await self._call_llm(
            prompt,
            retry_prompt=retry_prompt,
            policy=_TEAM_SKILL_PATCH_LLM_POLICY,
            is_result_usable=lambda text: self._parse_json(text) is not None,
        )
        elapsed = time.time() - t0
        logger.info("[TeamSkillOptimizer] patch: LLM responded in %.1fs, raw_len=%d", elapsed, len(raw))

        parsed = self._parse_json(raw)
        if not parsed:
            logger.warning("[TeamSkillOptimizer] patch: failed to parse LLM response")
            return None
        if not parsed.get("need_patch"):
            reason = parsed.get("reason", "N/A")
            logger.info("[TeamSkillOptimizer] patch: no patch needed for '%s', reason: %s", skill_name, reason)
            return None

        section = parsed.get("section", "Instructions")
        content = parsed.get("content", "")
        if not content.strip():
            logger.warning("[TeamSkillOptimizer] patch: LLM returned empty content")
            return None

        logger.info(
            "[TeamSkillOptimizer] patch: generated for section='%s', content_len=%d",
            section,
            len(content),
        )
        return EvolutionRecord.make(
            source="team_skill_evolution",
            context=parsed.get("reason", "Auto-detected from trajectory"),
            change=EvolutionPatch(
                section=section,
                action="append",
                content=content,
                target=EvolutionTarget.BODY,
            ),
        )

    async def generate_user_patch(
        self,
        trajectory: Trajectory,
        skill_name: str,
        user_intent: str,
    ) -> Optional[EvolutionRecord]:
        """Generate a patch based on explicit user improvement intent."""
        description = "team-skill"
        roles_summary = "N/A"
        workflow_summary = "N/A"

        # Try to extract frontmatter info from trajectory if available
        summary = self._build_trajectory_summary(trajectory)
        # Derive rough roles/workflow from trajectory tool calls
        if "spawn_member" in summary:
            role_mentions = re.findall(r"role[_-]?([a-z]+)", summary, re.IGNORECASE)
            if role_mentions:
                roles_summary = ", ".join(set(role_mentions[:5]))
        if "workflow" in summary.lower() or "mermaid" in summary.lower():
            workflow_summary = "Present in trajectory"

        prompt_template = _USER_PATCH_PROMPT.get(self._language, _USER_PATCH_PROMPT_EN)
        prompt = prompt_template.format(
            skill_name=skill_name,
            description=description,
            roles_summary=roles_summary,
            workflow_summary=workflow_summary,
            user_intent=user_intent,
        )
        retry_prompt = prompt_template.format(
            skill_name=skill_name,
            description=description,
            roles_summary=roles_summary[:_SUMMARY_RETRY_CHARS],
            workflow_summary=workflow_summary[:_SUMMARY_RETRY_CHARS],
            user_intent=user_intent[:_USER_INTENT_RETRY_CHARS],
        )

        t0 = time.time()
        raw = await self._call_llm(
            prompt,
            retry_prompt=retry_prompt,
            policy=_TEAM_SKILL_PATCH_LLM_POLICY,
            is_result_usable=lambda text: self._parse_json(text) is not None,
        )
        elapsed = time.time() - t0

        if not raw or not raw.strip():
            logger.warning("[TeamSkillOptimizer] user_patch: empty LLM response (%.1fs)", elapsed)
            return None

        parsed = self._parse_json(raw)
        if not parsed:
            logger.warning("[TeamSkillOptimizer] user_patch: failed to parse LLM response")
            return None

        section = parsed.get("section", "Instructions")
        content = parsed.get("content", "")
        if not content.strip():
            logger.warning("[TeamSkillOptimizer] user_patch: LLM returned empty content")
            return None

        logger.info(
            "[TeamSkillOptimizer] user_patch: section='%s', content_len=%d (%.1fs)",
            section,
            len(content),
            elapsed,
        )
        return EvolutionRecord.make(
            source="team_skill_user_patch",
            context=f"User intent: {user_intent[:200]}",
            change=EvolutionPatch(
                section=section,
                action="append",
                content=content,
                target=EvolutionTarget.BODY,
            ),
        )

    async def generate_trajectory_patch(
        self,
        trajectory: Trajectory,
        skill_name: str,
        trajectory_issues: list[dict],
    ) -> Optional[EvolutionRecord]:
        """Generate a patch based on trajectory issue analysis."""
        try:
            summary = self._build_trajectory_summary(trajectory)
        except Exception as exc:
            logger.warning("[TeamSkillOptimizer] trajectory_patch: failed to build summary: %s", exc)
            return None

        issues_text = json.dumps(trajectory_issues, ensure_ascii=False, indent=2) if trajectory_issues else "N/A"

        prompt_template = _TRAJECTORY_PATCH_PROMPT.get(self._language, _TRAJECTORY_PATCH_PROMPT_EN)
        prompt = prompt_template.format(
            skill_content="(see current skill content)",
            trajectory_summary=summary,
            trajectory_issues=issues_text[:5000],
        )
        retry_prompt = prompt_template.format(
            skill_content="(see current skill content)",
            trajectory_summary=summary[:_PATCH_RETRY_TRAJECTORY_CHARS],
            trajectory_issues=issues_text[:_TRAJECTORY_ISSUES_RETRY_CHARS],
        )

        t0 = time.time()
        raw = await self._call_llm(
            prompt,
            retry_prompt=retry_prompt,
            policy=_TEAM_SKILL_PATCH_LLM_POLICY,
            is_result_usable=lambda text: self._parse_json(text) is not None,
        )
        elapsed = time.time() - t0

        if not raw or not raw.strip():
            logger.warning("[TeamSkillOptimizer] trajectory_patch: empty LLM response (%.1fs)", elapsed)
            return None

        parsed = self._parse_json(raw)
        if not parsed:
            logger.warning("[TeamSkillOptimizer] trajectory_patch: failed to parse LLM response")
            return None

        if not parsed.get("need_patch"):
            reason = parsed.get("reason", "N/A")
            logger.info("[TeamSkillOptimizer] trajectory_patch: no patch needed, reason: %s", reason)
            return None

        section = parsed.get("section", "Workflow")
        content = parsed.get("content", "")
        if not content.strip():
            logger.warning("[TeamSkillOptimizer] trajectory_patch: LLM returned empty content")
            return None

        logger.info(
            "[TeamSkillOptimizer] trajectory_patch: section='%s', content_len=%d (%.1fs)",
            section,
            len(content),
            elapsed,
        )
        return EvolutionRecord.make(
            source="team_skill_trajectory_patch",
            context=f"Trajectory issues: {issues_text[:200]}",
            change=EvolutionPatch(
                section=section,
                action="append",
                content=content,
                target=EvolutionTarget.BODY,
            ),
        )

    @staticmethod
    def _build_trajectory_summary(trajectory: Trajectory) -> str:
        """Build a concise text summary of the trajectory for LLM consumption.

        Strategy: Tool calls carry the collaboration pattern (roles, tasks,
        workflow), so they get priority. LLM responses are secondary context.
        Budget: ~20k for tools, ~10k for LLM responses.
        """
        _tool_budget = 20000
        _llm_budget = 10000

        # Key tools get longer excerpts; others get shorter
        _key_tools = {"spawn_member", "create_task", "build_team", "view_task", "send_message"}
        tool_lines: list[str] = []
        llm_lines: list[str] = []
        llm_count = 0
        tool_count = 0

        for step in trajectory.steps:
            if step.kind == "tool" and step.detail:
                tool_count += 1
                tool_name = getattr(step.detail, "tool_name", "unknown")
                is_key = tool_name in _key_tools
                args_limit = 500 if is_key else 150
                result_limit = 500 if is_key else 200
                args = str(getattr(step.detail, "call_args", ""))[:args_limit]
                result = str(getattr(step.detail, "call_result", ""))[:result_limit]
                tool_lines.append(f"[Tool:{tool_name}] args={args} result={result}")
            elif step.kind == "llm" and step.detail:
                llm_count += 1
                response = getattr(step.detail, "response", None)
                if response:
                    text = str(response)[:300]
                    llm_lines.append(f"[LLM] {text}")

        tool_section = "\n".join(tool_lines)
        if len(tool_section) > _tool_budget:
            tool_section = tool_section[:_tool_budget] + "\n... (tool section truncated)"

        llm_section = "\n".join(llm_lines)
        if len(llm_section) > _llm_budget:
            llm_section = llm_section[:_llm_budget] + "\n... (LLM section truncated)"

        full = f"### Tool Calls ({tool_count})\n{tool_section}\n\n### LLM Responses ({llm_count})\n{llm_section}"

        logger.info(
            "[TeamSkillOptimizer] trajectory summary: %d LLM steps, %d tool steps, "
            "tool_section_len=%d, llm_section_len=%d, total_len=%d",
            llm_count,
            tool_count,
            len(tool_section),
            len(llm_section),
            len(full),
        )
        return full

    @staticmethod
    def _build_frontmatter(name: str, description: str, roles: List[Dict]) -> str:
        """Build YAML frontmatter string."""
        lines = [
            "---",
            f"name: {name}",
            "description: |",
            f"  {description}",
            "kind: team-skill",
            "teammate_mode: build_mode",
        ]
        if roles:
            lines.append("roles:")
            for role in roles:
                role_id = role.get("id", "unknown")
                skills = json.dumps(role.get("skills", []), ensure_ascii=False)
                tools = json.dumps(role.get("tools", []), ensure_ascii=False)
                lines.append(f"  - id: {role_id}")
                lines.append(f"    skills: {skills}")
                lines.append(f"    tools: {tools}")
        lines.append("provenance:")
        lines.append("  origin: auto-generated")
        lines.append("---")
        return "\n".join(lines)

    async def _call_llm(
        self,
        prompt: str,
        *,
        retry_prompt: Optional[str] = None,
        policy: Optional[LLMInvokePolicy] = None,
        is_result_usable: Optional[Callable[[str], bool]] = None,
    ) -> str:
        """Call the LLM and return raw text response."""
        try:
            logger.info("[TeamSkillOptimizer] LLM call start: model=%s, prompt_len=%d", self._model, len(prompt))
            t0 = time.time()
            if policy is None:
                response = await self._llm.invoke(
                    messages=[{"role": "user", "content": prompt}],
                    model=self._model,
                )
                if hasattr(response, "content"):
                    result = str(response.content)
                elif isinstance(response, dict):
                    result = response.get("content", "") or response.get("text", "")
                else:
                    result = str(response)
            else:
                result = await invoke_text_with_retry(
                    llm=self._llm,
                    model=self._model,
                    prompt=prompt,
                    retry_prompt=retry_prompt,
                    policy=policy,
                    is_result_usable=is_result_usable,
                )
            elapsed = time.time() - t0

            logger.info(
                "[TeamSkillOptimizer] LLM call done: %.1fs, response_len=%d",
                elapsed,
                len(result),
            )
            return result
        except Exception as exc:
            logger.error("[TeamSkillOptimizer] LLM call failed: %s", exc, exc_info=True)
            return ""

    async def regenerate_body(
        self,
        skill_name: str,
        current_body: str,
        evolution_records: List[Any],
        user_intent: Optional[str] = None,
    ) -> Optional[str]:
        """Regenerate Team Skill body using current content + evolutions.

        Returns new body text, or None if LLM fails or declines.
        """
        evo_summary = (
            "\n".join(
                f"- [{getattr(r, 'id', '?')}] {getattr(getattr(r, 'change', None), 'section', '?')}: "
                f"{getattr(getattr(r, 'change', None), 'content', '')[:200]}"
                for r in evolution_records[:20]
            )
            or "(no evolutions)"
        )

        intent_section = f"\n\n## 用户意图\n{user_intent}" if user_intent else ""

        prompt = (
            f"你是多角色协作 Skill 文档重写专家。请根据当前 Team Skill body 和积累的演进经验，"
            f"重新编写一份更优的 body。\n\n"
            f"## 当前 Team Skill: {skill_name}\n\n"
            f"```markdown\n{current_body[:8000]}\n```\n\n"
            f"## 积累的演进经验\n{evo_summary}"
            f"{intent_section}\n\n"
            f"## 要求\n"
            f"1. 保留 YAML frontmatter 不动（不要输出 frontmatter）\n"
            f"2. 将有价值的演进经验融入 body 正文\n"
            f"3. 保持 roles 子文件的引用结构\n"
            f"4. 精简冗余内容，保持结构清晰\n"
            f"5. 直接输出 Markdown body，不要加任何解释\n"
        )

        raw = await self._call_llm(prompt)
        body = raw.strip()
        if not body or len(body) < 50:
            return None
        return body

    def _dump_raw(self, tag: str, raw: str) -> None:
        """Persist raw LLM output to debug_dir for offline inspection."""
        if not self._debug_dir or not raw:
            return
        try:
            from datetime import datetime, timezone
            from pathlib import Path
            import uuid

            d = Path(self._debug_dir)
            d.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
            path = d / f"llm_raw_{tag}_{ts}_{uuid.uuid4().hex[:8]}.txt"
            path.write_text(raw, encoding="utf-8")
            logger.info("[TeamSkillOptimizer] raw response dumped to %s", path)
        except Exception as exc:
            logger.warning("[TeamSkillOptimizer] dump raw failed: %s", exc)

    @staticmethod
    def _parse_json(raw: str) -> Optional[Dict]:
        """Best-effort JSON extraction from LLM output.

        Strategy (mirrors SkillExperienceOptimizer._extract_json):
        1. ```json fenced block (non-greedy regex)
        2. raw as-is
        3. _fix_json_text (strip fences / trailing commas / line comments)
        4. balanced {...} scan that is aware of JSON string boundaries
           (handles bodies that themselves contain ``` code blocks)
        Logs the raw text head when all strategies fail so it can be inspected
        in agent_server.log.
        """
        if not raw:
            return None

        candidates: List[str] = []

        match = _JSON_BLOCK_RE.search(raw)
        if match:
            candidates.append(match.group(1).strip())

        candidates.append(raw.strip())
        candidates.append(_fix_json_text(raw))

        balanced = _extract_balanced_object(raw, "{", "}")
        if balanced:
            candidates.append(balanced)
            candidates.append(_fix_json_text(balanced))

        seen: set = set()
        for cand in candidates:
            if not cand or cand in seen:
                continue
            seen.add(cand)
            data = _try_parse_json(cand)
            if isinstance(data, (dict, list)):
                return data

        head = raw[:600].replace("\n", "\\n")
        logger.warning(
            "[TeamSkillOptimizer] JSON parse failed (raw_len=%d, head=%r)",
            len(raw),
            head,
        )
        return None

    # Public wrappers for static methods (used by TeamSkillRail)
    @staticmethod
    def build_trajectory_summary(trajectory: Trajectory) -> str:
        """Public wrapper for _build_trajectory_summary."""
        return TeamSkillOptimizer._build_trajectory_summary(trajectory)

    @staticmethod
    def parse_json(raw: str) -> Optional[Dict]:
        """Public wrapper for _parse_json."""
        return TeamSkillOptimizer._parse_json(raw)


__all__ = [
    "TeamSkillOptimizer",
    "_USER_PATCH_PROMPT",
    "_TRAJECTORY_PATCH_PROMPT",
]
