**What type of PR is this?**
/kind feature

---

**What does this PR do / why do we need it**:

为 `scenario=artifact`、`artifact_type=program` 提供程序优化 Provider 的完整实现（PUCT 搜索），并与 feat/rsi 最新契约（`fix(rsi): align artifact contracts and stage events`）对齐。

**实现（`openjiuwen/rsi/artifact_rsi/program_opt/`）**

- `PuctProgramArtifactProvider`（`puct_provider.py`）：满足 `ProgramArtifactProvider` 协议的实现。契约名归可 `isinstance` 的 Protocol，实现类单独命名，两者由 `program_opt/__init__` 一并导出。
- `run`/`resume` 共用一条 `_drive` 流程：装配（引擎延迟导入、沙箱探测、RunSpec）→ `EventStatus(running)` → 判别力探针（评分无法区分好坏候选时以 `PROBE_REFUSED` 拒绝，发生在预算花掉之前）→ 工作线程跑搜索 → 事件先落盘、再经 `run_coroutine_threadsafe` 交回调用方事件循环并等待返回（满足契约的顺序与背压要求）。
- `resume` 从 `run_dir` 读回上次的树快照，节点编号接续，不产生同编号两份内容。
- `pause` 诚实返回 `NOT_IMPLEMENTED`；`terminate` 在节点边界停止，评到一半的候选先记完分。
- 产物按内容寻址：每个候选是一个目录（文件树按各自相对路径铺开 + `_tree.json` manifest），两个节点写出同一程序共享一个产物；`locate_artifact(None)` 走最佳节点自己的 `snapshot_artifact_id`。

**契约对齐**

- `ArtifactEngineRequest.model` 为 AgentServer 注入的已初始化 `Model` 实例。新增 `completion_factory_from_model`：工作线程到异步 `Model.invoke` 的桥（轮询等待，`should_stop` 触发时放弃等待而不取消调用）。删除 `load_model_endpoint`/`completion_factory_for` —— 契约明确 Provider 不读模型配置、不自建客户端，保留它们等于留下被禁止的回退路径。
- token 上限 / thinking 从任务的 `scorecard.json` 读取（模型实例不透出自身配置）；`llm_judge` 评分卡由 `_judge_spec` 明确拒绝而不是用错误的模型悄悄评。
- `EngineState.best_node_id` 在三处构造点全部补齐。

---

**Which issue(s) this PR fixes**:
Fixes #

---

**Test Plan and Test result：What scenarios were tested, and what were the verification results（Function, performance, reliability, etc.）**：

- `tests/unit_tests/rsi` 全树：**1063 passed, 3 skipped**（Python 3.12）。
- `tests/unit_tests/rsi/artifact_rsi`：86 passed，含上游契约测试（`test_contract.py`，Protocol 可结构化实现、包面不外泄内部结构、`build_request` 要求已解析模型实例）与本实现的行为测试（探针拒绝先于预算、崩溃的搜索变成 failed 任务而非炸掉服务、慢消费者被等待、terminate 到达运行中的搜索、同一程序两节点共享产物时最终产物定位正确、候选沙箱环境不继承宿主机环境变量等）。
- 模型注入路径用 `_FakeModel`（仅暴露 `invoke`）驱动；缺失模型实例时任务以 `MODELCONFIG` 失败并落盘，`read_state` 与返回一致。

---

**Self-checklist**:（**请自检，在[ ]内打上x，我们将检视你的完成情况，否则会导致pr无法合入**）

+ - [x] **设计**：PR对应的方案是否已经经过Maintainer评审，方案检视意见是否均已答复并完成方案修改
+ - [x] **测试**：PR中的代码是否已有UT/ST测试用例进行充分的覆盖，新增测试用例是否随本PR一并上库或已经上库
+ - [x] **验证**：PR描述信息中是否已包含对该PR对应的Feature、Refactor、Bugfix的预期目标达成情况的详细验证结果描述
+ - [x] **接口**：是否涉及对外接口变更，相应变更已得到接口评审组织的通过，API对应的注释信息已经刷新正确
+ - [ ] **文档**：是否涉及官网文档修改，如果涉及请及时提交资料到Doc仓

🤖 Generated with [Claude Code](https://claude.com/claude-code)
