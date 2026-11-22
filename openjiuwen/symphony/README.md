# openjiuwen.symphony

`openjiuwen.symphony` 为 Agent 应用提供能力检索、能力编排和经验沉淀。宿主通过 `SymphonyRuntime(model, config)` 调用这些能力，并根据编排计划调度任务。

## 快速开始

在 `./capabilities/invoice/SKILL.md` 中写入以下 Skill 定义。

```markdown
---
name: invoice
description: Extract invoice numbers and totals from documents.
---

Extract the invoice number, currency, and total from each document.
```

在同一工作目录运行以下 Python 示例，搜索会话中的 Skill。目录发现可直接使用 `model=None`。

```python
import asyncio

from openjiuwen.symphony import SymphonyConfig, SymphonyRuntime

config = SymphonyConfig.model_validate({
    "capability": {"sources": [{"type": "skill", "root": "./capabilities"}]},
    "storage": {"artifact_root": "./.symphony/runtime"},
})


async def main():
    runtime = SymphonyRuntime(model=None, config=config)
    try:
        result = await runtime.retrieval.search(query="invoice")
        print([(match["type"], match["id"]) for match in result["matches"]])
    finally:
        await runtime.aclose()


asyncio.run(main())
```

`capability.sources` 至少包含一个来源，每项指定 `type` 和 `root`。`storage.artifact_root` 指定索引、关系图和经验沉淀产物的根目录。接入构图或规划时，传入宿主创建的 `openjiuwen.core.foundation.llm.Model`。使用完毕后调用 `await runtime.aclose()`，模型仍由宿主管理。

顶层配置包含 `capability`、`storage`、`retrieval` 和 `orchestration`。Flow 策略位于 `orchestration.experience`。公开配置和默认值见仓库中的 [config.tree.txt](config.tree.txt)。程序也可以通过 `SymphonyConfig.model_json_schema()` 获取配置说明。

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

## 能力编排

`await runtime.orchestration.build()` 要求配置恰好一个 Skill 来源，可同时纳入专家来源。调用返回 `GraphBuildResult`，可用 `version` 记录产物版本，用 `graph_path` 定位文件。以下函数沿用快速开始的配置，由宿主在异步环境中传入模型调用。

```python
async def plan_invoice(model):
    runtime = SymphonyRuntime(model=model, config=config)
    try:
        await runtime.orchestration.build()
        result = await runtime.orchestration.plan(
            "Extract the total from an invoice",
            type="skill",
            candidate_ids=["invoice"],
            inputs=[{"name": "invoice", "kind": "file", "value": "invoices/latest.pdf"}],
        )
        return result["planned_graph"]["graph"]
    finally:
        await runtime.aclose()
```

- `plan(query, type="auto", context=...)` 是统一规划入口，默认值为 `auto`。使用当前注入模型选择直接回答、单项能力、多项能力协作、单专家或多专家。路由是 `plan()` 内部步骤，宿主不单独调用路由函数。`auto` 表示调用时自动选择策略，不会在宿主每条新任务开始时提前调用规划。
- 允许委派时，可分别验收的独立判断或修改优先并行。各分支可以共用资料和工具，合并验收在分支产出后进行。同一结果的多格式导出、批量转换或反复修订适合由主代理处理。
- 自动结果始终包含 `execution_route`（`strategy`、选定的能力 ID、`reason`、`requires_planning`），`planned_graph` 可选。策略依次为 `direct_answer`、`single_capability`、`capability_workflow`、`single_expert`、`multi_expert`。单项能力由 `skill_ids` 或 `tool_ids` 指明；不含工具的多 Skill 工作流生成技能图，专家策略返回 `plan_kind=expert_tasks`，支持纯内联角色。直接回答、单项能力和含工具的工作流不要求图。工具只作为宿主上下文，不参与 Symphony 工具检索或建图。
- 迁移自动结果的消费者时，改读 `execution_route.strategy`；原 `tier`、`execution_mode` 和 `initial_tier` 已移除。单节点计划按实际执行归一为单项能力或单专家，`initial_strategy` 记录归一前的策略。统一入口、显式类型及两类图的接口保持不变。
- `context` 接受 `PlanningContext` 或等价字典，仅提供可见 `skills/experts/tools` 摘要、必选能力 ID 和宿主策略。省略时从配置目录获取 Skill/专家清单。宿主负责实时可见性，Core 在模型前过滤禁用 Skill 和专家 manifest 范围。自动规划传入 `candidate_ids` 时，路由和后续图规划均以候选与可见能力的交集为范围；省略时才从全部可见能力选择。空列表或无效 ID 不会回退到全目录，必选能力不在有效候选中时在模型调用前报错。候选不代表必选，专家策略仍允许临时角色。
- `allow_experts=False` 由程序移除专家候选和专家策略，并校验模型不能返回专家策略，包括内联角色。该开关、请求 ID 和重判预算不进入模型上下文。任务和约束写入 `query`，已有资料通过 `inputs` 提供；原 `context.facts/constraints/work_mode` 字段移除，不再复制会话或系统提示词。路由只读取有界目录和输入预览，完整具名输入继续交给规划与执行；只有显式必选能力会附加对应要求。
- 自动路由一般增加一次模型调用；无效 JSON/决定最多修复一次，规划不可行最多重新判级一次。`execution_route.reconsiderations` 记录已消耗次数，`context.reconsiderations=1` 保留为已消耗预算的兼容输入；Swarm 按需调用时不管理该预算。取消和模型异常向调用方传播。
- `SymphonyGraphEngine.plan(..., prepare_skill_graph=callback)` 在自动选择不含工具的多 Skill 工作流，或显式 `type="skill"` 时请求宿主准备图，再检查技能图并读取动态图快照。其他自动策略不检查技能图。固定图修订或 `task_cluster_id` 要显式使用 `type="skill"`。
- 显式 `plan(type="skill"|"expert")` 直接选择规划实现，不调用路由模型。`type="expert"` 返回专家任务图，替代早期版本返回专家能力链的行为。迁移原先省略类型的技能图调用时，请补上 `type="skill"`，或者改为消费自动结果的可选图。
- `plan_skill_graph(query, candidate_ids=...)` 与 `plan(type="skill")` 使用相同的技能规划实现。`candidate_ids=None` 表示使用当前可见候选，传入 ID 列表可指定起始候选。列表为空或所有 ID 均无效时，返回 `no_plan`。动态图、指定修订与禁用技能过滤沿用技能规划契约。
- `plan_subagent_graph(query, candidate_ids=...)` 与 `plan(type="expert")` 生成相同的子智能体任务图。规划模型通过 `expert_id` 引用已安装专家，或通过含 `name`、`description` 的 `role` 定义内联角色，二者只能选一个。Core 据此生成节点的 `metadata.type`（`expert` 或 `inline`）。未知专家 ID 会触发校验错误。没有技能图、已发布的专家图或可用专家时，也可以生成纯内联计划。`expert_manifest_paths` 限定可用专家；候选为空或均不可用时仍可使用内联角色。
- 两个专用入口不接收 `type`。统一入口拒绝未知类型；专家规划不接受技能图修订、动态覆盖与禁用技能等专属参数。`SymphonyGraphEngine` 和直接使用的 `OrchestrationService` 遵循相同的类型分流语义。
- 三个入口均支持通过 `orchestration.plan.mode` 选择 `fast` 或 `beam`。显式技能/专家规划通过 `result["planned_graph"]["graph"]` 返回图；自动结果先检查 `execution_route` 和可选图。节点和依赖分别位于 `nodes` 和 `edges`。专家任务图的交接边引用本轮任务的 `source_output` 和 `target_input`。
- 可选的 `inputs` 接收一组 `PlanningInput` 或等价字典。每项的 `name` 唯一，`kind` 为 `text` 或 `file`，`value` 为已知文本或宿主已校验的文件引用，`description` 可注明用途。直接使用 SDK 时，由宿主负责解析文件引用。

Skill 和子智能体编排均以 `graph.metadata.status=ready` 返回有效任务图，无法形成计划时返回 `no_plan`。宿主可结合已掌握的资料、图中的交接关系和 `graph.metadata.missing_inputs` 判断哪些节点可以执行。Skill 节点的 `metadata.inputs` 和 `outputs` 描述已声明的端口，`metadata.missing_inputs` 标记规划时发现的节点缺项。子智能体节点用 `metadata.external_inputs` 引用所需资料名。Core 会将已引用但未提供的资料汇总到图级 `missing_inputs`，模型也可以补充缺项的类型和用途。节点自身入边已经提供的产物由交接传递，只有单独声明的同名外部资料才计入外部输入。

子智能体草稿只需给出 `tasks` 和必要的 `handoffs`。非空任务图通过校验后，Core 补上 `ready`。节点可以省略 `title`，此时使用任务正文的首句或首行作为显示标题。

子智能体草稿结构或交接校验失败时，Core 将原请求、错误和限长的上一份草稿交给模型补试一次。只报告缺项的空草稿也使用这一次补试额度。再次无法形成计划时，`graph.metadata.reason` 给出原因。模型明确返回的 `no_plan`（无资料缺项）直接保留，模型调用异常和取消沿调用链传播。

专家任务图的 `metadata.task` 是 Markdown 正文，包含节点目标、约束、产物内容和验收要求。产物的名称和类型由 `metadata.outputs` 声明。Core 根据交接边生成 `metadata.inputs`，接收端口默认沿用源产物名。交接用 `target_input` 指定别名或把多个产物汇入同一端口。

分类树支持按类浏览和搜索。关系图按能力的输入输出连接候选。`plan_subagent_graph` 可沿这些关系补充候选专家，规划模型收到的每位专家只有 `id`、`name` 和 `description`。模型先按用户可独立验收的结果划分任务，同一结果的支撑材料、核对和各类交付文件由同一节点负责，再按所需能力选择角色。已安装专家的专长直接匹配核心工作时采用该专家，匹配较弱时使用内联角色。需要相同能力的独立任务优先复用同一专家或内联角色，分别创建节点，供宿主并行派发。单节点计划同样有效，跨节点交接按实际依赖定义。

## 管理构建与产物

通过 `runtime.retrieval.status()` 和 `runtime.orchestration.status()` 查看索引与图的状态。两者都支持 `read()` 读取当前产物，关系图还可用 `read(version)` 读取指定版本。构建可传 `progress` 接收进度，用 `await cancel_build()` 取消。默认配置下，重建失败或取消时仍可读取上次成功发布的版本。

如需随构建发布宿主附属文件，可传入 `prepare_artifact(version_dir)` 回调，在收到的目录中写入文件。

检索与编排可独立启用。禁用时仍可查状态，执行入口会拒绝操作。不提供模型时，仍可查状态、读取产物和发现能力。若启用了 `retrieval.index_build.task.allow_fallback_tree`，构树也可使用回退策略。启用已有索引保护时，失败后的回退树会保留已有的可用分类树。

## 执行观测与经验沉淀

同时启用 `orchestration.enabled` 与 `orchestration.experience.enabled` 后，`openjiuwen.symphony` 接收 Skill 执行证据，更新动态关系，并为满足接纳条件的组合生成候选能力包。

- 宿主可在执行开始时调用 `runtime.capture_graph_snapshot()`，将返回值写入 `execution_graph["graph_snapshot"]`。执行结束后用 `await runtime.submit_evolution(planned_graph, execution_graph, session_id=..., capture_mode=...)` 提交。返回的 `EvolutionSubmitResult` 包含 `graph_receipt` 和 `new_candidates`。`capture_mode` 可取 `agent` 或 `team`。
- 用 `runtime.flow.list_candidates()` 查看候选，或通过 `runtime.flow.create_scope(store=...)` 使用宿主管理的存储。候选确认和安装由宿主负责。
- 使用 `orchestration.plan.dynamic_graph_enabled` 控制 Skill 规划是否读取已有的动态观测。
