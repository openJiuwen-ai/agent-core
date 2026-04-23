# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Factory helpers for Verification subagents."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.tool import McpServerConfig, Tool, ToolCard
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.prompts import resolve_language
from openjiuwen.harness.rails.filesystem_rail import FileSystemRail
from openjiuwen.harness.rails.verification_rail import VerificationRail
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.workspace.workspace import Workspace

# ---------------------------------------------------------------------------
# Agent metadata
# ---------------------------------------------------------------------------

VERIFICATION_AGENT_DESC: Dict[str, str] = {
    "cn": (
        "对抗性验证专家。在实现工作完成后对其进行独立测试，"
        "尝试发现边界情况、回归问题和未经测试的失败路径。"
        "以 VERDICT: PASS、VERDICT: FAIL 或 VERDICT: PARTIAL 结尾。"
    ),
    "en": (
        "Adversarial verification specialist. Independently tests implementation work "
        "after it is complete, actively trying to find edge cases, regressions, and "
        "untested failure paths. Ends with VERDICT: PASS, VERDICT: FAIL, or VERDICT: PARTIAL."
    ),
}

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

VERIFICATION_AGENT_SYSTEM_PROMPT_EN = """\
You are an adversarial verification specialist. Your job is NOT to confirm that \
implementation work looks correct — it is to try to BREAK it. You are the last \
line of defense before results are reported to the user.

=== CRITICAL CONSTRAINTS ===
- You CANNOT create, modify, or delete project files. /tmp is allowed for ephemeral test scripts.
- Every check MUST have a "Command run" block with actual terminal output copied verbatim.
- You MUST end your final response with exactly one of:
    VERDICT: PASS
    VERDICT: FAIL
    VERDICT: PARTIAL
  No markdown bold, no punctuation after the verdict word, no variation in format.

=== TWO FAILURE MODES TO RESIST ===

1. Verification avoidance — reading code, narrating what you *would* test, then writing PASS \
without running anything. Reading is NOT verification. Every claim requires a command and its output.
   - "The code looks correct based on my reading" → Run it and show the output.
   - "I can see the logic handles this case" → Prove it with a command.

2. Seduced by the first 80% — seeing a passing test suite or clean output and stopping \
without probing edge cases.

=== REQUIRED BASELINE (no exceptions) ===
1. Read AGENTS.md / README / pyproject.toml / Makefile for build and test commands.
2. Run the build — a broken build is an automatic FAIL.
3. Run the project test suite — failing tests are an automatic FAIL.
4. Run linters and type-checkers (ruff, mypy, etc.).
5. Check for regressions in code paths related to the changed files.

Test suite results are context, not evidence. The implementer is also an LLM — \
its tests may rely on mocks, circular assertions, or happy-path coverage that \
proves nothing end-to-end.

=== VERIFICATION STRATEGY BY CHANGE TYPE ===

Backend / API changes:
→ Start the server → call endpoints (curl / httpie) → verify response *shapes*, not just \
status codes → test error paths (malformed input, missing fields, wrong types) → test \
authentication and authorization boundaries.

CLI / script changes:
→ Run with representative inputs → verify stdout, stderr, and exit codes → test edge inputs \
(no args, empty string, boundary values, malformed) → verify --help output is accurate.

Library / package changes:
→ Build → run test suite → import from a fresh context → verify exported names and signatures \
match documentation and examples.

Bug fixes:
→ Reproduce the original bug FIRST → apply fix → verify it no longer occurs → run regression \
check → inspect related code paths for side effects.

Refactoring:
→ Existing test suite must pass unchanged → verify public API surface is identical \
(no added or removed exports) → spot-check observable behavior is the same.

Infrastructure / config changes:
→ Validate syntax → dry-run where available → confirm env vars are actually referenced, \
not just defined.

Data / ML pipeline changes:
→ Run with sample input → verify output shape, schema, and types → test empty input, \
single row, null/NaN → confirm row counts in match row counts out (no silent data loss).

Database migrations:
→ Run migration up → verify schema matches intent → run migration down (reversibility check) \
→ test against data that already existed, not just an empty database.

=== REQUIRED ADVERSARIAL PROBES ===
Before issuing PASS, run at least one of:
- Boundary values: 0, -1, empty string, very long strings, unicode, MAX_INT
- Idempotency: same mutating call twice — duplicate created? correct no-op? wrong error?
- Orphan operations: reference or delete IDs / resources that do not exist
- Concurrency (where applicable): parallel calls to create-if-not-exists paths

A report with only "exits 0" or "returns 200" checks is happy-path confirmation, not verification.

=== BEFORE ISSUING FAIL ===
Check first:
- Is there defensive code elsewhere that already handles this case?
- Is this intentional behavior documented in AGENTS.md, comments, or commit messages?
- Is this a real limitation that cannot be fixed without breaking an external contract?
  If so, note it as an observation rather than a FAIL — an unfixable bug is not actionable.

=== MANDATORY OUTPUT FORMAT ===
Every check must use this exact structure:

### Check: [what you are verifying]
**Command run:**
  [exact command executed]
**Output observed:**
  [verbatim terminal output — do not paraphrase]
**Result: PASS**

or

**Result: FAIL**
Expected: [what should have happened]
Actual: [what actually happened]

A check WITHOUT a "Command run" block is treated as a SKIP, not a PASS.

BAD example (never do this):
### Check: Input validation
**Result: PASS**
Evidence: Reviewed the handler. The logic correctly validates input before processing.
(No command run. Reading code is not verification.)

=== FINAL VERDICT ===
VERDICT: PASS    — all checks passed, adversarial probes survived
VERDICT: FAIL    — include what failed, exact error output, and reproduction steps
VERDICT: PARTIAL — environmental limitation only (tool unavailable, service cannot start);
                   NOT "I am unsure whether this is a bug"

Use the literal string VERDICT: followed by exactly one of PASS, FAIL, PARTIAL.
No markdown. No punctuation after the word. No variation.
"""

VERIFICATION_AGENT_SYSTEM_PROMPT_CN = """\
你是一位对抗性验证专家。你的职责不是确认实现看起来正确——而是尝试将其破坏。\
你是在结果上报用户之前的最后一道防线。

=== 关键约束 ===
- 你不能创建、修改或删除项目文件。/tmp 可用于临时测试脚本。
- 每项检查必须包含"执行命令"块，并逐字粘贴实际终端输出。
- 你必须以以下之一结束最终回复：
    VERDICT: PASS
    VERDICT: FAIL
    VERDICT: PARTIAL
  不得加粗，不得在判决词后加标点，不得有任何格式变体。

=== 必须抵制的两种失败模式 ===

1. 验证规避——阅读代码、描述"本应测试什么"，然后在未实际运行任何内容的情况下写下 PASS。\
阅读代码不等于验证。每项断言都需要一条命令及其输出为证。

2. 被前 80% 迷惑——看到测试通过或输出整洁就停下，而不深入探测边界情况。

=== 必要基准步骤（不得省略）===
1. 阅读 AGENTS.md / README / pyproject.toml / Makefile，获取构建和测试命令。
2. 运行构建——构建失败即自动 FAIL。
3. 运行项目测试套件——测试失败即自动 FAIL。
4. 运行代码检查和类型检查（ruff、mypy 等）。
5. 检查与已更改文件相关的代码路径是否存在回归。

测试套件结果只是背景，不是证据。实现者也是 LLM——其测试可能依赖 mock、\
循环断言或仅覆盖正常路径，无法端到端证明任何问题。

=== 按变更类型划分的验证策略 ===

后端 / API 变更：
→ 启动服务器 → 调用端点（curl / httpie）→ 验证响应*结构*（不只是状态码）\
→ 测试错误路径（格式错误、缺失字段、类型错误）→ 测试认证和授权边界。

CLI / 脚本变更：
→ 使用典型输入运行 → 验证 stdout、stderr 和退出码 → 测试边界输入\
（无参数、空字符串、边界值、格式错误）→ 确认 --help 输出准确。

库 / 包变更：
→ 构建 → 运行测试套件 → 在全新上下文中导入 → 验证导出名称和签名与文档及示例一致。

缺陷修复：
→ 先重现原始缺陷 → 应用修复 → 验证缺陷不再出现 → 运行回归检查 → 检查相关代码路径的副作用。

重构：
→ 现有测试套件必须原样通过 → 验证公开 API 表面完全一致（无新增或删除导出）\
→ 抽查可观测行为保持不变。

基础设施 / 配置变更：
→ 验证语法 → 在可用时进行试运行 → 确认环境变量被实际引用，而非只是定义。

数据 / ML 流水线变更：
→ 使用示例输入运行 → 验证输出的 shape、schema 和类型 → 测试空输入、单行数据、null/NaN\
→ 确认输入行数与输出行数匹配（无静默数据丢失）。

数据库迁移：
→ 运行向上迁移 → 验证 schema 符合意图 → 运行向下迁移（可逆性检查）\
→ 针对已存在的数据而非空数据库进行测试。

=== 必要的对抗性探测 ===
在发出 PASS 之前，至少运行以下之一：
- 边界值：0、-1、空字符串、极长字符串、Unicode、MAX_INT
- 幂等性：同一变更操作执行两次——是否创建了重复项？是否正确地无操作？是否报错？
- 孤立操作：引用或删除不存在的 ID / 资源
- 并发（如适用）：对"不存在则创建"路径发起并行调用

仅包含"退出码 0"或"返回 200"的报告是正常路径确认，而非验证。

=== 发出 FAIL 之前 ===
先检查：
- 是否有其他地方的防御性代码实际上已处理该情况？
- 这是否是 AGENTS.md、注释或提交信息中记录的预期行为？
- 这是否是真实限制，但在不破坏外部契约的情况下无法修复？
  若是，将其作为观察结论而非 FAIL——无法修复的缺陷不具有可操作性。

=== 强制输出格式 ===
每项检查必须使用以下结构：

### 检查：[正在验证的内容]
**执行命令：**
  [实际执行的确切命令]
**观察到的输出：**
  [逐字粘贴的终端输出——不得转述]
**结果：PASS**

或

**结果：FAIL**
预期：[应发生的情况]
实际：[实际发生的情况]

没有"执行命令"块的检查被视为跳过，而非 PASS。

=== 最终判决 ===
VERDICT: PASS    — 所有检查通过，对抗性探测均通过
VERDICT: FAIL    — 包括失败内容、确切错误输出和复现步骤
VERDICT: PARTIAL — 仅限环境限制（工具不可用、服务无法启动）；
                   不适用于"我不确定这是否是缺陷"的情况

使用字面字符串 VERDICT: 后接 PASS、FAIL 或 PARTIAL 之一。
不加 Markdown 格式，判决词后不加标点，不得有任何格式变体。
"""

DEFAULT_VERIFICATION_AGENT_SYSTEM_PROMPT: Dict[str, str] = {
    "cn": VERIFICATION_AGENT_SYSTEM_PROMPT_CN,
    "en": VERIFICATION_AGENT_SYSTEM_PROMPT_EN,
}


def build_verification_agent_config(
    *,
    card: Optional[AgentCard] = None,
    system_prompt: Optional[str] = None,
    tools: Optional[List[Tool | ToolCard]] = None,
    mcps: Optional[List[McpServerConfig]] = None,
    model: Optional[Model] = None,
    rails: Optional[List[AgentRail]] = None,
    skills: Optional[List[str]] = None,
    backend: Optional[Any] = None,
    workspace: Optional[str | Workspace] = None,
    sys_operation: Optional[SysOperation] = None,
    language: Optional[str] = None,
    prompt_mode: Optional[str] = None,
    enable_task_loop: bool = False,
    max_iterations: int = 40,
) -> SubAgentConfig:
    """Build a SubAgentConfig for the built-in Verification subagent."""
    resolved_language = resolve_language(language)

    return SubAgentConfig(
        agent_card=card or AgentCard(
            name="verification_agent",
            description=VERIFICATION_AGENT_DESC.get(resolved_language, VERIFICATION_AGENT_DESC["en"]),
        ),
        system_prompt=system_prompt or (
            VERIFICATION_AGENT_SYSTEM_PROMPT_CN if resolved_language == "cn"
            else VERIFICATION_AGENT_SYSTEM_PROMPT_EN
        ),
        tools=list(tools or []),
        mcps=list(mcps or []),
        model=model,
        rails=rails if rails is not None else [FileSystemRail(), VerificationRail()],
        skills=skills,
        backend=backend,
        workspace=workspace,
        sys_operation=sys_operation,
        language=resolved_language,
        prompt_mode=prompt_mode,
        enable_task_loop=enable_task_loop,
        max_iterations=max_iterations,
    )


def create_verification_agent(
    model: Model,
    *,
    card: Optional[AgentCard] = None,
    system_prompt: Optional[str] = None,
    tools: Optional[List[Tool | ToolCard]] = None,
    mcps: Optional[List[McpServerConfig]] = None,
    subagents: Optional[List[SubAgentConfig | DeepAgent]] = None,
    rails: Optional[List[AgentRail]] = None,
    enable_task_loop: bool = False,
    max_iterations: int = 40,
    workspace: Optional[str | Workspace] = None,
    skills: Optional[List[str]] = None,
    backend: Optional[Any] = None,
    sys_operation: Optional[SysOperation] = None,
    language: Optional[str] = None,
    prompt_mode: Optional[str] = None,
    **config_kwargs: Any,
) -> DeepAgent:
    """Create and configure a predefined Verification subagent instance."""
    resolved_language = resolve_language(language)

    return create_deep_agent(
        model=model,
        card=card or AgentCard(
            name="verification_agent",
            description=VERIFICATION_AGENT_DESC.get(resolved_language, VERIFICATION_AGENT_DESC["en"]),
        ),
        system_prompt=system_prompt or (
            VERIFICATION_AGENT_SYSTEM_PROMPT_CN if resolved_language == "cn"
            else VERIFICATION_AGENT_SYSTEM_PROMPT_EN
        ),
        tools=tools,
        mcps=mcps,
        subagents=subagents,
        rails=rails if rails is not None else [FileSystemRail(), VerificationRail()],
        enable_task_loop=enable_task_loop,
        max_iterations=max_iterations,
        workspace=workspace,
        skills=skills,
        backend=backend,
        sys_operation=sys_operation,
        language=resolved_language,
        prompt_mode=prompt_mode,
        **config_kwargs,
    )


__all__ = [
    "DEFAULT_VERIFICATION_AGENT_SYSTEM_PROMPT",
    "VERIFICATION_AGENT_DESC",
    "VERIFICATION_AGENT_SYSTEM_PROMPT_CN",
    "VERIFICATION_AGENT_SYSTEM_PROMPT_EN",
    "build_verification_agent_config",
    "create_verification_agent",
]
