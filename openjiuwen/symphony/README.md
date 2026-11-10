# Symphony

Symphony 从一个能力目录构建分类树与关系图，提供会话发现、在线编排和执行观测 / Flow。统一入口为 `SymphonyRuntime(model, config)`；扫描、指纹和服务组装由 Core 管理。

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
    session = runtime.retrieval.create_session(
        session_scope="conversation-1",
        visible_capability_ids=lambda: {"extract", "summarize"},
        disabled_capability_ids=lambda: disabled_ids,
        source_by_id={"extract": "installed"},
        pin_index_revision=True,
    )
    candidates = await session.capability_search(type="skill", query="summarize documents")
    plan = await runtime.orchestration.plan(
        "Summarize the document", candidate_ids=["extract", "summarize"],
        disabled_capability_ids=disabled_ids,
    )
finally:
    await runtime.aclose()
```

`on_progress` 可以是同步或异步回调。构造函数只保存独立配置快照并固定相对路径，不扫描、构建、请求模型或启动任务。配置模型使用 Pydantic v2，支持 `model_validate`、`model_dump_json`、`model_validate_json` 和中文 JSON Schema。

`capability.sources` 是非空目录列表，每项声明 `type` 和 `root`，整组共用 `scan`、`fingerprint` 和 `storage.artifact_root`。多个 `skill` 和 `expert` 目录可共用一棵分类树。专家读取器从独立专家包的 `manifest.json` 投影 `name`（ID 和默认显示名）、`description` 及 `package_type: agent_template`。关系图与 Flow 只使用 Skill，并要求恰有一个 Skill 目录。

公共配置树及默认值见 [config.tree.txt](config.tree.txt)。

## 三个入口

| 入口 | 能力 |
|---|---|
| `runtime.retrieval` | `status()` / `read()` / `await build()` / `await cancel_build()` / `create_session()` |
| `runtime.orchestration` | `status()` / `read()` / `await build()` / `await cancel_build()` / `await plan()` |
| `runtime.flow` | 惰性默认 Flow；`status()`、`await start()`、提交和候选操作，以及 `create_scope(store=...)` |

树构建返回含 `success/result/data` 的字典，并携带 `build_id`、`version`、变化 / 复用计数和诊断。图构建返回 `GraphBuildResult`；图状态和图内容提供 `to_dict()`。构建 ID 表示本次尝试，版本表示最后发布产物；复用会取得新构建 ID 并保留版本。整图复用的 `graph_reused_count=1`，本次关系匹配调用计数为零。

两个构建入口的 `prepare_artifact(version_dir)` 回调可写入宿主附属产物，完成后才发布。构建取消或发布前集合变化均保留之前的可用版本。关闭 Runtime 会等待自己的构建、图增量、规划、Flow 和会话任务收尾；取消某个关闭等待者不会取消内部关闭任务。注入的宿主模型始终由宿主关闭。

三个开关独立。禁用能力可查询状态，执行入口拒绝操作。`model=None` 支持状态、产物读取和确定性发现；构图与规划会报告缺少模型。构树按 `task.allow_fallback_tree` 处理；启用旧索引保护时，不用失败后的回退树替换已有可用分类树。

## 构建与模型

分类树保留一次性生成、递归细分、后处理、等价分组和增量更新算法，直接消费 `retrieval.index_build` 的子模型。同步算法由 Runtime 在工作线程调度，模型请求桥接至宿主事件循环。未绑定事件循环的原生 `Model` 不支持直接同步构建；异步 Runtime 和 CLI 会完成绑定。

请求超时、任务超时和取消均传播到模型请求。鉴权和参数等永久 HTTP 错误不会重试；瞬态错误按请求策略重试，前一次调用完成清理后才允许下一次调用。取消清理超界时终止整个构建，不重试、不回退、不发布。截断结果会被拒绝，模型用量由宿主响应观察器计量。

指纹语义提取、质量评估和缓存分别消费 `capability.fingerprint.extraction/evaluation/cache`。构图提取并发为提取上限与 `orchestration.build.max_concurrency` 的较小值；批量上限仍限制每批对象数。`max_output_tokens=None` 沿用宿主模型配置。

构图和 fast / beam 规划共用 `orchestration.min_edge_confidence`。`candidate_ids=None` 表示未限制候选；显式空列表或全部无效 ID 返回空计划，不扩大到全量。`dynamic_graph_enabled` 只决定规划是否使用已收集的观测，不关闭观测写入。

## Discovery 会话

`create_session` 的会话标识、目录范围、可见对象、禁用对象、来源和索引快照属于调用上下文，不放进配置，也没有 Source 构造层。来源以对象 ID 为键。返回的 `DiscoverySession` 同时服务工具和提示 Rail；动态禁用实时生效，冻结的预算和索引版本不随后续配置修改漂移。

可通过 `create_session(config=...)` 传入 `RetrievalConfig` 来覆盖单个会话的提示、输出和索引读取策略。Runtime 持有统一的检索开关与构树策略。

`retrieval.use_existing_index=True` 在已有索引时读取分类树。尚无索引时使用确定性目录发现，不创建占用 Runtime 布局的临时目录。单次返回数量受 `retrieval.output.max_results` 限制，列表同时受 `max_list_entries` 限制。续页继续使用同一会话游标。会话附加目录只改变发现视图，宿主全局构建使用稳定目录。

单 Skill 目录的检索构建与构图共用指纹服务和缓存。共用分类树的指纹按类型发布在所属集合下。Skill 和专家目录按会话可见范围读取记录，并按类型、来源和内容哈希匹配指纹。指纹缺失或过期时使用当前目录描述。

`session.get_tools()` 提供单个 `capability_search` 工具。SDK 也可直接调用 `session.capability_search()`。`type` 默认为 `skill`。Skill 与专家都支持 `operation=list`、按 `category` 搜索和 `cursor` 续页。专家接受一条搜索词，结果的 `matches` 带分类，列表的 `categories` 带可见项计数。续页沿用原 `type` 和 `operation`。

独立 SDK 会话直接读配置的专家目录。宿主可向 `create_session(expert_candidates=...)` 提供返回可调用候选的异步回调。配置了专家来源时，Core 按候选的 `manifest_path` 与配置目录实际读到的包取交集。省略专家来源时使用宿主候选。现有 `search_catalog` 回调继续服务连接器和 MCP，也可在未配置专家来源及新回调时处理旧版专家关键词搜索。

会话检索使用扫描中通过校验的能力。跨类型可有相同 ID，同类型的重复 ID 会被排除。若扫描返回错误，`runtime.retrieval.status()` 报告诊断并将索引标为过期，构建保留上一发布版本。

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
