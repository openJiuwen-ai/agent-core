# Agent Teams 文档入口已迁移

设计文档、spec、feature 与 bugfix 已迁至仓库根 `.agents/agent-docs/` 对应分类下的 `agent_teams/`。
本目录不再存放开发文档或独立定义归档规则。

- 设计阶段：`.agents/skills/jw-design/SKILL.md`。
- 开发落地记忆：`.agents/skills/jw-dev/SKILL.md`，原归档规则已迁入其 `references/development-memory.md`。
- 全流程：`.agents/skills/jw-coding-flow/SKILL.md`。
- 本模块专属同步和提交约束继续遵循 `openjiuwen/agent_teams/AGENTS.md`。

新 F 文档直接放 `.agents/agent-docs/features/`，模块以 frontmatter modules 标注；历史子目录通过 features/INDEX.md 定位。作者使用 Git author，F/B 声明关联 designs，修改后按根 AGENTS.md 自动同步索引。
