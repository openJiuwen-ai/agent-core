---
description: Shared instructions for AI coding assistants in agent-core.
---

# AGENTS.md

Shared instructions for AI coding assistants working in `agent-core`.
Keep this file specific, factual, and cross-tool. Prefer nearby code and
tests over assumptions.

`pyproject.toml` is the canonical source of truth for Python/tooling
settings, and `Makefile` defines the common lint/test entry points.

## What This Repo Is

- `openjiuwen/core/`: public SDK/runtime for agents, workflows, sessions,
  memory, retrieval, security, and system operations.
- `openjiuwen/harness/`: coding-agent framework built on core
  primitives; includes prompts, rails, tools, subagents, task loop, and
  workspace handling. Tool permission engine lives in `openjiuwen/harness/security/`;
  prompt/tool security rails live in `openjiuwen/harness/rails/security/`.
- `openjiuwen/harness_protocol/`: provider-neutral SPI (contracts and frozen
  value objects only) for driving third-party agent harnesses.
- `openjiuwen/harness_providers/`: built-in implementations of that SPI
  (`native` DeepAgent, `claudecode`, `codex`, `dsh`), the DeepAgent-style
  `HarnessIOAdapter`, and the manifest-driven `create_harness` factory, which
  also resolves third-party provider packages through the
  `openjiuwen.harness_providers` entry point.
- `openjiuwen/extensions/`: optional integrations such as storage,
  checkpointers, sandbox providers, and vendor-specific adapters.
- `openjiuwen/agent_evolving/` and `openjiuwen/dev_tools/`: optimization,
  evaluation, and developer tooling.
- `tests/unit_tests/`: fast deterministic coverage used in CI.
- `tests/system_tests/`: higher-level E2E tests; may require real
  credentials and are commonly skipped in CI.
- `examples/` and `docs/`: user-facing usage references. Update them when
  public behavior changes.

## Instruction Priority

- Follow system, tool, and user instructions first, then this file, then
  module-local docs.
- Before changing behavior, inspect the touched module, its exported
  surface in `__init__.py`, and nearby tests/examples.
- Prefer small, targeted diffs. Do not refactor unrelated areas
  opportunistically.

## Core Architecture

- Treat exports from `__init__.py`, documented APIs, examples, and README
  snippets as public API.
- `openjiuwen.core.single_agent` is the current single-agent API.
  `openjiuwen.core.single_agent.legacy` exists for compatibility only.
- Preserve the Card/Config split: cards define identity/metadata
  (`AgentCard`, `ToolCard`, `WorkflowCard`, `SysOperationCard`); runtime
  behavior belongs in configs, agents, managers, or resource instances.
- `Runner.resource_mgr` is shared process-global state. Use stable IDs
  and keep tests isolated.

## Commands

- Setup: `uv sync`
- Install tooling: `make install`
- Run all tests: `make test`
- Run targeted test: `make test TESTFLAGS="path/to/test.py"`
- Lint staged files: `make check`
- Check last N commits: `make check COMMITS=N`
- Type-check: `make type-check`
- Auto-fix: `make fix`

`make check` and `make type-check` operate on staged files by default.
Pass `COMMITS=N` to check recent commits instead.

## More Detail

- Architecture, subsystem maps, change rules: `.claude/rules/architecture.md`
- Code style: `.claude/rules/code-style.md` and `.claude/rules/python/coding-style.md`
- Testing: `.claude/rules/testing.md`
- Security: `.claude/rules/security.md`
- Error codes (StatusCode / BaseError): `.claude/rules/error-codes.md`
- Logging: `.claude/rules/logging.md`
- Git workflow: `.claude/rules/git-workflow.md`
- Reviewer/merger ownership files (`CODEREVIEWER`, `COMMITTER`, `USER.json`): `.claude/rules/ownership-files.md`
- Deep operational guides: `.claude/skills/`
- Permissions and env vars: `.claude/settings.json`

## AI coding 流程与文档基础设施

本仓库项目级 skill 维护在 `.agents/skills/`，按当前任务阶段读取：

- `.agents/skills/jw-coding-flow/SKILL.md`：需求、设计、开发、检视与交付的流程入口。
- `.agents/skills/jw-design/SKILL.md`：仅设计阶段，包含设计概览、架构和特性方案参考。
- `.agents/skills/jw-dev/SKILL.md`：开发、测试、契约同步及 feature / bugfix 记忆维护。
- `.agents/skills/jw-review/SKILL.md`：代码检视入口；按 agent-core 技术定位、模块 AGENTS/spec 审视工作区、提交或分支差异，核对跨层影响、兼容与回归证据并交接复查，不限定 PR 场景。
- `.agents/skills/jw-testing/SKILL.md`：独立撰写与维护转测文档，输出暂存本地被忽略的 designs/testing/，不进入设计生命周期或索引。

实际资产位于 `.agents/agent-docs/` 的 `designs/`、`specs/`、`features/`、`bugfix/`。
新 design 使用 `D-YYYYMMDD-HHMM-<slug>`；feature / bugfix 使用 `F_` / `B_` 加 `YYYYMMDD_HHMM` 和主题。时间均采用 Asia/Singapore，
精确到分钟；历史编号不调整，spec 沿用原命名规则。具体位置与交接规则见对应 skill。
不运行格式化工具或静态检查，不运行 ruff、pylint、mypy、make check 或 make format。


## D / F / B / S 文档约定

文档根目录为 `.agents/agent-docs/`；元数据、作者与索引规则见 `.agents/skills/jw-design/references/document-metadata.md`。

| 类型 | 用途 | 位置 |
|---|---|---|
| D | 开发前方案、取舍与真实评审/确认 | `designs/proposals/` → `active/` → `archive/`，保留 D/SDD 标识 |
| F | 实际实施、开发决策、设计偏差、验证与遗留 | 新文件直接放 `features/F_YYYYMMDD_HHMM_<slug>.md`，不再分模块子目录 |
| B | 故障现象、根因、修复决策与回归证据 | `bugfix/<module>/B_YYYYMMDD_HHMM_<slug>.md` |
| S | 当前有效契约、接口、状态与不变量 | `specs/<module>/S_NN_<slug>.md`，沿用已有编号 |

受管文档（含说明与生成索引）必须有 YAML frontmatter。author 使用创建时仓库有效的 Git user.name，author_email 使用对应 user.email；后续普通修改不覆盖原作者。
不再使用 owner 字段；历史 owner 就是 author，迁移时优先将原值保留为 author，不丢失或用 Git 推断值覆盖。只有原 owner/author 均不存在且无法可靠追溯时才以 null 和 author_note 明示；模板占位在创建时由工具填入。
F/B 必须显式声明 designs；有 D 填完整标识列表，无独立 D 填 [] 并写 design_note，历史待核实用 null 并说明。
modules/files 记录实际模块和代码范围，不能用作者表示长期维护权或把历史设计当成当前实现事实。
写作、修改、迁移文档后在本任务中自动执行：

```bash
python .agents/skills/jw-design/scripts/sync_documents.py --document <本次新建文档路径>
```

执行 Python 前按本仓要求初始化环境；仅刷新索引时省略 --document。工具维护根总索引及 D/F/B/S 分类索引，F 平铺后通过作者、模块、文件和关联 D 定位，不手工改索引。
这项同步是写作收尾，不安装 watcher/Git hook，不授权提交；检视阶段保持候选只读。转测资料继续由 jw-testing 独立管理，不进入正式索引。
