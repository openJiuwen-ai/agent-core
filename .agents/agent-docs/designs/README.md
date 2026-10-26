# 设计资产

本目录保存 core 各模块及跨仓设计。设计规则与模板由
[`jw-design`](../../skills/jw-design/SKILL.md) 提供，开发记忆与交付生命周期由
[`jw-dev`](../../skills/jw-dev/SKILL.md) 维护。

| 目录或文件 | 用途 |
|---|---|
| `proposals/` | 起草、评审、已批准待开发或已否决设计 |
| `active/` | 已批准并实际进入开发的设计 |
| `archive/` | 已交付、被取代或终止的设计及历史资料 |
| `agent_teams/` | 从模块 docs 迁入的总体设计 |
| `INDEX.md` | 自动生成的模块目录索引，模块内按生命周期展示 |
| `REGISTRY.md` | 旧 SDD 编号历史台账，新设计不取号 |

新设计使用 `D-YYYYMMDD-HHMM-<slug>/design.md`，时间为 Asia/Singapore、精确到分钟。
主题属于完整标识，建档后不随评审或阶段迁移改名。原 SDD、SSD 及散落历史资料保持文件名、状态和内容。
有标准 frontmatter 的阶段设计进入自动索引；旧格式资料见 [legacy-documents.md](legacy-documents.md)。
原始来源为 `swarm-design-docs` 子模块，首轮集成保留源仓库，未删除子模块。
新工作以 core 内资产为维护入口，原仓库后续变更需另行明确同步范围。

新设计是开发前资产，使用 jw-design 的唯一 design.md 模板；管理字段留在设计 frontmatter，
评审与方案修订按日期记录，不要求递增版本。开发后的实施、实际决策、偏差和验证证据写入 features，
不回填设计；设计保留验证方案与通过条件。历史文档的版本和 implementation 字段原样保留，
索引只展示设计管理信息，不再展示评审版本与实现状态。
`modules` 声明相关模块目录，省略固定的 `openjiuwen/` 前缀（如 `agent_teams/workflow`），跨仓模块保留仓库前缀。
跨模块设计在各模块下展示，全局计数按文档去重；
缺少该字段的设计归入“未分类”，`domain` 仅作为领域信息保留。

从 core 根目录生成索引：

```bash
source .venv/bin/activate
export PYTHONPATH=.:$PYTHONPATH
python .agents/skills/jw-design/scripts/generate_index.py --root .agents/agent-docs/designs
```

specs 是当前契约，features 是特性落地记忆，bugfix 是故障档案，分别位于 `../specs/`、
`../features/`、`../bugfix/`。设计状态不替代代码事实与验证结果。
