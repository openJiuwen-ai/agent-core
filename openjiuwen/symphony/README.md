# Symphony

Symphony 提供 Skill、专家和 MCP 检索，以及 Skill 编排和执行观测 / Flow。统一入口为 `SymphonyRuntime(model, config)`；扫描、指纹和服务组装由 Core 管理。

## 配置与调用

```python
from openjiuwen.symphony import SymphonyConfig, SymphonyRuntime

config = SymphonyConfig.model_validate({
    "capability": {"sources": [{"type": "skill", "root": "./capabilities"}]},
    "storage": {"artifact_root": "./.symphony/runtime"},
    "retrieval": {
        "index_build": {"request": {"max_concurrency": 2}},
        "use_existing_index": True,
    },
    "orchestration": {
        "min_edge_confidence": 0.7,
        "plan": {"mode": "fast"},
        "experience": {"enabled": True},
    },
})

# model 是宿主已创建的 openjiuwen.core.foundation.llm.Model。
runtime = SymphonyRuntime(model=model, config=config)
try:
    await runtime.retrieval.build(force=False, progress=on_progress)
    await runtime.orchestration.build(force=False, progress=on_progress)
    runtime.retrieval.bind_search(
        session_scope="conversation-1",
        visible_capability_ids=lambda: {"extract", "summarize"},
        disabled_capability_ids=lambda: disabled_ids,
        source_by_id={"extract": "installed"},
        pin_index_revision=True,
    )
    candidates = await runtime.retrieval.search(type=["skill"], query="summarize documents")
    plan = await runtime.orchestration.plan(
        "Summarize the document", candidate_ids=["extract", "summarize"],
        disabled_capability_ids=disabled_ids,
    )
finally:
    await runtime.aclose()
```

`on_progress` 可以是同步或异步回调。构造函数只保存独立配置快照并固定相对路径，不扫描、构建、请求模型或启动任务。配置模型使用 Pydantic v2，支持 `model_validate`、`model_dump_json`、`model_validate_json` 和中文 JSON Schema。

`capability.sources` 是非空目录列表，每项声明 `type` 和 `root`，整组共用 `scan`、`fingerprint` 和 `storage.artifact_root`。多个 `skill` 和 `expert` 目录可共用一棵分类树。专家读取器从独立专家包的 `manifest.json` 投影 `name`（ID 和默认显示名）、`description` 及 `package_type: agent_template`。关系图与 Flow 只使用 Skill，并要求恰有一个 Skill 目录。

顶层配置包含 `capability`、`storage`、`retrieval` 和 `orchestration`；Flow 策略位于 `orchestration.experience`。公共配置树及默认值见 [config.tree.txt](config.tree.txt)。

## 三个入口

| 入口 | 能力 |
|---|---|
| `runtime.retrieval` | `status()` / `read()` / `await build()` / `await cancel_build()` / `search()` / `get_tools()` / `bind_search()` |
| `runtime.orchestration` | `status()` / `read()` / `await build()` / `await cancel_build()` / `await plan()` |
| `runtime.flow` | 惰性默认 Flow；`status()`、`await start()`、提交和候选操作，以及 `create_scope(store=...)` |

树构建返回含 `success/result/data` 的字典，并携带 `build_id`、`version`、变化 / 复用计数和诊断。图构建返回 `GraphBuildResult`；图状态和图内容提供 `to_dict()`。构建 ID 表示本次尝试，版本表示最后发布产物；复用会取得新构建 ID 并保留版本。整图复用的 `graph_reused_count=1`，本次关系匹配调用计数为零。

两个构建入口的 `prepare_artifact(version_dir)` 回调可写入宿主附属产物，完成后才发布。构建取消或发布前集合变化均保留之前的可用版本。关闭 Runtime 会等待自己的构建、图增量、规划、Flow 和会话任务收尾；取消某个关闭等待者不会取消内部关闭任务。注入的宿主模型始终由宿主关闭。

检索与编排开关独立；经验沉淀和 Flow 需要 `orchestration.enabled` 与 `orchestration.experience.enabled` 同时开启。禁用能力可查询状态，执行入口拒绝操作。`model=None` 支持状态、产物读取和确定性发现；构图与规划会报告缺少模型。构树按 `task.allow_fallback_tree` 处理；启用旧索引保护时，不用失败后的回退树替换已有可用分类树。

## 构建与模型

分类树保留一次性生成、递归细分、后处理、等价分组和增量更新算法，直接消费 `retrieval.index_build` 的子模型。同步算法由 Runtime 在工作线程调度，模型请求桥接至宿主事件循环。未绑定事件循环的原生 `Model` 不支持直接同步构建；异步 Runtime 和 CLI 会完成绑定。

请求超时、任务超时和取消均传播到模型请求。鉴权和参数等永久 HTTP 错误不会重试；瞬态错误按请求策略重试，前一次调用完成清理后才允许下一次调用。取消清理超界时终止整个构建，不重试、不回退、不发布。截断结果会被拒绝，模型用量由宿主响应观察器计量。

指纹语义提取、质量评估和缓存分别消费 `capability.fingerprint.extraction/evaluation/cache`。构图提取并发为提取上限与 `orchestration.build.max_concurrency` 的较小值；批量上限仍限制每批对象数。`max_output_tokens=None` 沿用宿主模型配置。

构图和 fast / beam 规划共用 `orchestration.min_edge_confidence`。`candidate_ids=None` 表示未限制候选；显式空列表或全部无效 ID 返回空计划，不扩大到全量。`dynamic_graph_enabled` 只决定规划是否使用已收集的观测，不关闭观测写入。

## 能力检索

直接调用 `runtime.retrieval.search(query="invoice")`，默认搜索当前会话可见的 Skill、专家和 MCP 服务。用 `type=["expert", "mcp"]` 可将搜索限定在专家和 MCP。模型工具 `capability_search` 通过 `runtime.retrieval.get_tools()` 获取，使用 JSON 数组选择类型。Python 也接受元组和单个类型字符串。两个入口共用检索范围、已查看记录和游标。

宿主可先调用 `runtime.retrieval.bind_search(session_scope=..., visible_capability_ids=..., disabled_capability_ids=..., pin_index_revision=True)` 绑定当前 Agent 的实时可见范围和索引版本。返回的 `SkillFS` 供提示词和状态面板读取。并发会话或团队成员各用自己的 Runtime。重新绑定后，旧工具和游标失效，宿主应重新注册工具。

| 操作 | 接口与返回值 |
|---|---|
| 构建分类树 | `await runtime.retrieval.build()` 返回包含 `success`、`version` 和 `diagnostics` 的字典。设置 `retrieval.use_existing_index=True` 后，会话可使用已发布的树。 |
| 查找多种能力 | `await runtime.retrieval.search(query="invoice", type=["skill", "expert"])` 返回 `matches` 和 `next_cursor`。用 `(type, id)` 区分同名的 Skill 和专家。 |
| 只查 Skill | `await runtime.retrieval.search(query="invoice", type=["skill"])` 返回文本结果。用 `result.detailed_output["observed_skill_ids"]` 读取本页 Skill ID。 |
| 只查专家 | `await runtime.retrieval.search(query="invoice", type=["expert"])` 从专家目录返回 `matches` 和 `next_cursor`。 |
| 只查 MCP 服务 | `await runtime.retrieval.search(type=["mcp"], query=["代码仓库", "代码执行"])` 返回匹配的服务及当前会话可用的工具和 Skill。 |
| 浏览分类和续页 | `operation="list"` 列出子分类和条目，`category` 可限定范围。混合页的分类带有 `capability_type`。传 `cursor` 即可续页，沿用原类型和操作。只查 Skill 时游标在 `detailed_output["next_cursor"]` 中。 |

多个 Skill 或专家目录可以共用分类树。专家来源配置为 `type="expert"`，独立专家包的 `manifest.json` 需声明 `package_type="agent_template"`、`name` 和 `description`。`name` 用作专家 ID。

宿主通过 `runtime.retrieval.bind_search(catalog_candidates=...)` 提供异步回调。每次搜索、列举或续页时，Core 按 `expert` 或 `mcp` 请求一份当前会话可见的 `CapabilityCandidate` 列表。配置了专家目录时按 manifest 路径取交集。MCP 服务和连接器统一用 `type="mcp"`，按候选声明的 `classification` 浏览，未分类服务直接列在根目录。分类树只扫描 Skill 和专家目录。

三种类型的 `query` 均可传一个字符串或 1 至 8 条查询组成的列表。每类目录轮流取各条查询的命中并去重，混合页按所选类型的顺序轮流取结果。`top_k` 限制整页的总条数，搜索和列举都受 `retrieval.output.max_results` 限制，列举还受 `max_list_entries` 限制。同一 MCP 服务被多条查询命中时，结果会一起展示匹配的操作示例。

单类和多类型检索都按 `retrieval.output.max_chars` 裁剪展示内容，从实际展示的条目之后续页。成功续页会消费旧游标。目录临时读取失败可以用原游标重试，已选目录的版本发生变化则需要重新发起查询。分类分支的全部后代都对当前成员可见时，检索保留该分支的说明和选择条件。

## 产物与版本

单 Skill 目录继续使用以下布局。多目录及专家集合的分类树位于 `taxonomy_collections/<scope_id>/tree`，集合身份由排序去重的绝对来源路径和类型确定，来源重排复用同一棵树。添加专家来源可直接在原产物根下构建新树。

```text
storage.artifact_root/
├── runtime.json       # 布局版本与集合身份
├── inventory/         # Core 生成的集合快照
├── cache/             # 指纹等缓存
├── tree/              # 当前分类树及构建状态
├── graph/             # 原子发布图版本、关系缓存和观测
└── flow/              # 默认 Flow 证据与候选
```

目录按固定能力根和类型绑定，其他集合不能共用。新签名包含对象内容、相关构建策略、协议和模型身份；规划参数、Flow 阈值和展示开关不参与树内容签名。无模型状态查询保留产物模型身份，同时继续检查内容和构建策略，并明确未核验模型身份。

不兼容旧 Runtime 构造参数、配置别名、旧缓存签名及旧布局。历史目录不自动迁移或删除，请为新配置选择独立产物目录并重新构建。

## 编排经验与宿主职责

`orchestration.experience.enabled` 默认关闭。与 `orchestration.enabled` 同时开启时，Runtime 接收图观测，并将合格的成功子图送给 Flow 沉淀组合能力包。`orchestration.plan.dynamic_graph_enabled` 独立控制规划是否采用已有动态图。

`orchestration.experience.acceptance` 默认要求至少 3 次成功及 0.8 成功率，`narrative.max_examples` 默认保留最多 5 个示例。`runtime.flow.create_scope(store=user_store)` 用宿主受限存储创建隔离视图，共用默认服务的打包和审核组件。用户证据范围、候选确认和安装授权由宿主决定。

`capture_graph_snapshot()` 和 `submit_evolution()` 保留供现有 Rail 使用。旧模型检索服务、分支浏览工具、紧凑码表和独立 Experience 包已退役，不恢复旧入口。

## 配置文档与验证

修改配置后运行 `python scripts/export_symphony_config.py` 自动更新配置树。`--include-internal` 额外输出内部配置参考。

相关行为测试位于 `tests/unit_tests/symphony/`。配置默认值与基线快照比较；运行测试覆盖真实目录、模型替身、HTTP 连接复用、取消、原子发布、会话可见性、观测和 Flow。此流程不运行格式化或静态检查。
