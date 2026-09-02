**What type of PR is this?**
/kind feature

---

**What does this PR do / why do we need it**:

为 `scenario=artifact`、`artifact_type=program` 提供程序优化 Provider 的完整实现（PUCT 搜索），并与 feat/rsi 最新契约（`fix(rsi): align artifact contracts and stage events`）对齐。

**实现（`openjiuwen/rsi/artifact_rsi/program_opt/`）**

- `PuctProgramArtifactProvider`（`puct_provider.py`）：满足 `ProgramArtifactProvider` 协议的实现。契约名归可 `isinstance` 的 Protocol，实现类单独命名，两者由 `program_opt/__init__` 一并导出。
- `run`/`resume` 共用一条 `_drive` 流程：装配（沙箱探测、RunSpec）→ `EventStatus(running)` → 判别力探针（评分无法区分好坏候选时以 `PROBE_REFUSED` 拒绝，发生在预算花掉之前）→ 工作线程跑搜索 → 事件先落盘、再经 `run_coroutine_threadsafe` 交回调用方事件循环并等待返回（满足契约的顺序与背压要求）。
- `resume` 从 `run_dir` 读回上次的树快照，节点编号接续，不产生同编号两份内容。
- `pause` 与 `terminate` 共用同一套节点边界停止机制，分岔只在停下的搜索折叠成什么状态：`paused`（非终态，**唯一为续跑设计的状态**）或 `terminated`（终态）。`resume` 拒绝 `terminated`（`TERMINATED_NOT_RESUMABLE`，不构造引擎）；`completed` **允许**续跑——请求自带 `max_iterations`，带更大预算延长一个已完成的搜索是合法用法，预算算术按剩余数接续；`failed` 留作修好配置后的重试路径；对在飞任务的第二次 `run`/`resume` 整体拒绝（`TASK_ALREADY_RUNNING`，防两个引擎双写一个 run_dir 并偷走停止标志）。pause 与 terminate 竞态时 terminate 赢。评到一半的候选照样记完分。
- 产物按内容寻址：每个候选是一个目录（文件树按各自相对路径铺开 + `_tree.json` manifest），两个节点写出同一程序共享一个产物；`locate_artifact(None)` 走最佳节点自己的 `snapshot_artifact_id`。

**提示词按任务拼接**

- 上游（`Birfy/agentdescent` `examples/era`）一个任务一个模块，`Domain.prompt` 是任务自己写的函数。Provider 的信任模型是 run_dir 只作数据，所以拆成两半：**措辞**归 `run_dir/prompts/{mutation,repair,prior}.md`（`${slot}` 语法，缺省用内置模板），**动态反馈段落**归评测脚本的 `error` 通道（任务代码，在沙箱内计算，流入 `${feedback}`）。
- 槽位词表是框架的（`MUTATION_SLOTS` 等）；模板加载时校验，未知占位符按名拒绝并列出词表——`safe_substitute` 会把笔误留成字面量，模型对着带洞的提示优化一整个预算。

**契约对齐**

- `RsiUsage`/`RsiUsageTokens` 按契约决定整体删除：schema 类型、`EngineState`/`EngineReport`/`EventProgress` 的 `usage` 字段、state 的 `_cost` 折叠器（只为刷新 usage 而存在）、provider 的调用计数包装器一并退役。引擎内部的 `CompletionUsage`（空回复触顶判定）与续跑 tokens 不属于该通道，保留。旧 state.json 的 `usage` 键被忽略，读取自然兼容。

- `agentdescent==0.4.6` 进主依赖（本项目按应用部署，精确钉死不再给第三方解析器添负担；vendored 的 PUCT 移植 import 了不承诺稳定的内部件，所以钉死而非下限。`program-opt` extra 留空壳保持老安装命令合法）。引擎顶层直连，`_load_engine`/`SearchEngineUnavailable` 的"可选轮子"路径整体退役。
- **模型调用只有一条通道**：`ArtifactEngineRequest.model`（AgentServer 注入的已初始化 `Model` 实例）。`completion_factory_from_model` 是工作线程到异步 `Model.invoke` 的桥（轮询等待，`should_stop` 触发时放弃等待而不取消调用）。所有端点式旁路整体拆除——`load_model_endpoint`/`completion_factory_for`/引擎默认缝 `_default_completion`/`RunSpec.llm_url/llm_token` 全删，`PuctEngine` 的 `completion_factory` 必传，`completion.py` 从 HTTP 客户端瘦身为缝的词汇表（226→51 行），并有测试钉住「引擎无法在没有模型通道时被构造」。
- token 上限从任务的 `scorecard.json` 读取（模型实例不透出自身配置）；`RunSpec.thinking` 随端点客户端一并删除——思考开关归注入 `Model` 自己的 `ModelRequestConfig`，不生效的旋钮比没有旋钮更误导。
- **打分只有沙箱评测一种**：`llm_judge` 死路整体退役（-465 行：`judge_domain`/`text_candidate`/`_judge_spec`/rubric 通道/RunSpec 四字段）——契约只有一个 `model_refs["optimizer"]`，judge 与 mutator 共用模型是自我评分；等契约有 `model_refs["judge"]` 再接回。未移植的 `dataset_metric`/`test_gate` 也按名拒绝，顺带封掉旧的 UnboundLocalError 死状。
- `EngineState.best_node_id` 在三处构造点全部补齐。

---

**Which issue(s) this PR fixes**:
Fixes #

---

**Test Plan and Test result：What scenarios were tested, and what were the verification results（Function, performance, reliability, etc.）**：

- `tests/unit_tests/rsi` 全树：**1072 passed, 3 skipped**（Python 3.12）。
- `tests/unit_tests/rsi/artifact_rsi`：67+19 passed，含上游契约测试（`test_contract.py`，Protocol 可结构化实现、包面不外泄内部结构、`build_request` 要求已解析模型实例）与本实现的行为测试（探针拒绝先于预算、崩溃的搜索变成 failed 任务而非炸掉服务、慢消费者被等待、terminate 到达运行中的搜索、同一程序两节点共享产物时最终产物定位正确、候选沙箱环境不继承宿主机环境变量等）。
- 模型注入路径用 `_FakeModel`（仅暴露 `invoke`）驱动；缺失模型实例时任务以 `MODELCONFIG` 失败并落盘，`read_state` 与返回一致。

---

**Self-checklist**:（**请自检，在[ ]内打上x，我们将检视你的完成情况，否则会导致pr无法合入**）

+ - [x] **设计**：PR对应的方案是否已经经过Maintainer评审，方案检视意见是否均已答复并完成方案修改
+ - [x] **测试**：PR中的代码是否已有UT/ST测试用例进行充分的覆盖，新增测试用例是否随本PR一并上库或已经上库
+ - [x] **验证**：PR描述信息中是否已包含对该PR对应的Feature、Refactor、Bugfix的预期目标达成情况的详细验证结果描述
+ - [x] **接口**：是否涉及对外接口变更，相应变更已得到接口评审组织的通过，API对应的注释信息已经刷新正确
+ - [ ] **文档**：是否涉及官网文档修改，如果涉及请及时提交资料到Doc仓

🤖 Generated with [Claude Code](https://claude.com/claude-code)
