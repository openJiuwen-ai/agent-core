# openjiuwen.auto_harness.schema

Auto Harness Agent 数据模型模块，定义了编排器运行过程中使用的所有数据结构，包括优化任务、经验记录、阶段产物、配置、运行时状态等。所有核心数据类均使用 `@dataclass` 装饰器，枚举类继承自 `str, Enum`。

子模块：
- `schema`：数据模型定义，包含任务状态、经验类型、阶段槽位、各类产物数据类、配置类及辅助函数。

---

## openjiuwen.auto_harness.schema.normalize_pipeline_preference

```
normalize_pipeline_preference(value: Any) -> str
```

规范化用户侧的 pipeline 偏好值。支持别名映射（如 `"meta"` → `META_EVOLVE_PIPELINE`），无法识别的值将回退为 `"auto"`。

**参数**：
* **value**(`Any`)：用户输入的 pipeline 偏好值。

**返回**：`str` —— 规范化后的 pipeline 名称。

---

## class openjiuwen.auto_harness.schema.TaskStatus

```
class openjiuwen.auto_harness.schema.TaskStatus(str, Enum)
```

优化任务状态。

**枚举值**：

| 成员 | 值 |
|------|------|
| `PENDING` | `"pending"` |
| `RUNNING` | `"running"` |
| `SUCCESS` | `"success"` |
| `FAILED` | `"failed"` |
| `TIMEOUT` | `"timeout"` |
| `REVERTED` | `"reverted"` |

---

## class openjiuwen.auto_harness.schema.ExperienceType

```
class openjiuwen.auto_harness.schema.ExperienceType(str, Enum)
```

经验记录类型。

**枚举值**：

| 成员 | 值 |
|------|------|
| `OPTIMIZATION` | `"optimization"` |
| `FAILURE` | `"failure"` |
| `INSIGHT` | `"insight"` |

---

## class openjiuwen.auto_harness.schema.StageSlot

```
class openjiuwen.auto_harness.schema.StageSlot(str, Enum)
```

跨 pipeline 共享的规范阶段名称。

**枚举值**：

| 成员 | 值 |
|------|------|
| `ASSESS` | `"assess"` |
| `PLAN` | `"plan"` |
| `IMPLEMENT` | `"implement"` |
| `VERIFY` | `"verify"` |
| `ACTIVATE` | `"activate"` |
| `COMMIT` | `"commit"` |
| `PUBLISH` | `"publish"` |
| `LEARNINGS` | `"learnings"` |

---

## class openjiuwen.auto_harness.schema.Gap

```
@dataclass
class openjiuwen.auto_harness.schema.Gap
```

竞品差距。

**字段**：

* **id**(`str`)：差距唯一标识。默认 `""`。
* **competitor**(`str`)：竞品名称。默认 `""`。
* **feature**(`str`)：功能名称。默认 `""`。
* **current_state**(`str`)：当前状态描述。默认 `""`。
* **gap_description**(`str`)：差距描述。默认 `""`。
* **impact**(`float`)：影响程度评分。默认 `0.0`。
* **feasibility**(`float`)：可行性评分。默认 `0.0`。
* **suggested_approach**(`str`)：建议的解决方案。默认 `""`。
* **target_files**(`List[str]`)：目标文件列表。默认 `[]`。

### priority

```
@property
priority -> float
```

impact x feasibility。

**返回**：`float` —— 优先级得分（影响度 × 可行性的乘积）。

---

## class openjiuwen.auto_harness.schema.OptimizationTask

```
@dataclass
class openjiuwen.auto_harness.schema.OptimizationTask
```

单个优化任务。

**字段**：

* **topic**(`str`)：任务主题。（必填）
* **description**(`str`)：任务描述。默认 `""`。
* **files**(`List[str]`)：相关文件列表。默认 `[]`。
* **issue_ref**(`Optional[str]`)：关联的 issue 引用。默认 `None`。
* **expected_effect**(`str`)：预期效果。默认 `""`。
* **pipeline_name**(`str`)：指定使用的 pipeline 名称。默认 `""`。
* **status**(`TaskStatus`)：任务状态。默认 `TaskStatus.PENDING`。

---

## class openjiuwen.auto_harness.schema.Experience

```
@dataclass
class openjiuwen.auto_harness.schema.Experience
```

经验库记录。

**字段**：

* **type**(`ExperienceType`)：经验类型。默认 `ExperienceType.OPTIMIZATION`。
* **topic**(`str`)：主题。默认 `""`。
* **summary**(`str`)：摘要。默认 `""`。
* **outcome**(`str`)：结果。默认 `""`。
* **details**(`str`)：详细信息。默认 `""`。
* **pr_url**(`str`)：关联 PR 链接。默认 `""`。
* **files_changed**(`List[str]`)：变更文件列表。默认 `[]`。
* **signal**(`str`)：信号描述。默认 `""`。
* **strategy**(`str`)：策略描述。默认 `""`。
* **causal_chain**(`str`)：因果链描述。默认 `""`。
* **signal_frequency**(`int`)：信号频率。默认 `0`。
* **id**(`str`)：记录唯一标识，自动生成 12 位十六进制字符串。默认 `uuid.uuid4().hex[:12]`。
* **timestamp**(`float`)：时间戳，自动取当前时间。默认 `time.time`。

---

## class openjiuwen.auto_harness.schema.ResearchContext

```
@dataclass
class openjiuwen.auto_harness.schema.ResearchContext
```

Research 阶段收集的上下文。

**字段**：

* **experiences**(`List[Experience]`)：相关经验记录列表。默认 `[]`。
* **source_files**(`dict[str, str]`)：源文件路径到内容的映射。默认 `{}`。
* **gap_report**(`Optional[str]`)：差距分析报告。默认 `None`。

---

## class openjiuwen.auto_harness.schema.CycleResult

```
@dataclass
class openjiuwen.auto_harness.schema.CycleResult
```

单个 task 的执行结果。

**字段**：

* **success**(`bool`)：是否成功。默认 `False`。
* **summary**(`str`)：结果摘要。默认 `""`。
* **pr_url**(`str`)：创建的 PR 链接。默认 `""`。
* **error**(`str`)：错误信息。默认 `""`。
* **reverted**(`bool`)：是否已回滚。默认 `False`。
* **error_log**(`str`)：错误日志。默认 `""`。

---

## class openjiuwen.auto_harness.schema.AssessmentArtifact

```
@dataclass
class openjiuwen.auto_harness.schema.AssessmentArtifact
```

Assess 阶段的结构化产物。

**字段**：

* **report**(`str`)：评估报告内容。默认 `""`。

---

## class openjiuwen.auto_harness.schema.TaskPlanArtifact

```
@dataclass
class openjiuwen.auto_harness.schema.TaskPlanArtifact
```

Plan 阶段的结构化产物。

**字段**：

* **tasks**(`List[OptimizationTask]`)：规划出的优化任务列表。默认 `[]`。
* **raw_plan**(`str`)：原始计划文本。默认 `""`。

---

## class openjiuwen.auto_harness.schema.PipelineSelectionArtifact

```
@dataclass
class openjiuwen.auto_harness.schema.PipelineSelectionArtifact
```

select_pipeline 阶段的结构化产物。

**字段**：

* **pipeline_name**(`str`)：选中的 pipeline 名称。默认 `META_EVOLVE_PIPELINE`。
* **reason**(`str`)：选择原因。默认 `""`。
* **alternatives**(`List[str]`)：备选 pipeline 列表。默认 `[]`。
* **confidence**(`float`)：选择置信度。默认 `0.0`。
* **risk_level**(`str`)：风险等级。默认 `""`。
* **required_inputs**(`List[str]`)：所需输入列表。默认 `[]`。
* **fallback_pipeline**(`str`)：降级 pipeline 名称。默认 `""`。

---

## class openjiuwen.auto_harness.schema.GapAnalysisArtifact

```
@dataclass
class openjiuwen.auto_harness.schema.GapAnalysisArtifact
```

扩展 evolve pipeline 的差距分析输出。

**字段**：

* **gaps**(`List[Gap]`)：差距列表。默认 `[]`。
* **competitor_summary**(`str`)：竞品分析摘要。默认 `""`。
* **raw_analysis**(`str`)：原始分析文本。默认 `""`。

---

## class openjiuwen.auto_harness.schema.ExtensionDesign

```
@dataclass
class openjiuwen.auto_harness.schema.ExtensionDesign
```

单个扩展设计候选方案。

**字段**：

* **gap_id**(`str`)：关联的差距 ID。默认 `""`。
* **extension_name**(`str`)：扩展名称。默认 `""`。
* **kind**(`str`)：扩展类型。默认 `"capability"`。
* **depends_on**(`List[str]`)：依赖的扩展列表。默认 `[]`。
* **applies_to**(`List[str]`)：适用范围列表。默认 `[]`。
* **components**(`List[str]`)：组件列表。默认 `[]`。
* **file_plan**(`Dict[str, str]`)：文件规划映射。默认 `{}`。
* **harness_config_patch**(`Dict[str, Any]`)：harness 配置补丁。默认 `{}`。
* **skill_source**(`str`)：技能来源。默认 `""`。

---

## class openjiuwen.auto_harness.schema.ExtensionDesignArtifact

```
@dataclass
class openjiuwen.auto_harness.schema.ExtensionDesignArtifact
```

运行时扩展生成的设计输出。

**字段**：

* **designs**(`List[ExtensionDesign]`)：扩展设计方案列表。默认 `[]`。
* **package_name**(`str`)：最终包名。默认 `""`。

---

## class openjiuwen.auto_harness.schema.ExtensionBuildArtifact

```
@dataclass
class openjiuwen.auto_harness.schema.ExtensionBuildArtifact
```

task worktree 内经过验证的扩展构建输出。

**字段**：

* **extension_name**(`str`)：扩展名称。默认 `""`。
* **extension_root**(`str`)：扩展根目录路径。默认 `""`。
* **config_path**(`str`)：配置文件路径。默认 `""`。

---

## class openjiuwen.auto_harness.schema.RuntimeExtensionArtifact

```
@dataclass
class openjiuwen.auto_harness.schema.RuntimeExtensionArtifact
```

会话本地提升的运行时扩展。

**字段**：

* **extension_name**(`str`)：扩展名称。默认 `""`。
* **runtime_path**(`str`)：运行时路径。默认 `""`。
* **config_path**(`str`)：配置文件路径。默认 `""`。

---

## class openjiuwen.auto_harness.schema.SessionResultsArtifact

```
@dataclass
class openjiuwen.auto_harness.schema.SessionResultsArtifact
```

Session 聚合结果。

**字段**：

* **results**(`List[CycleResult]`)：所有周期结果列表。默认 `[]`。

---

## class openjiuwen.auto_harness.schema.CodeChangeArtifact

```
@dataclass
class openjiuwen.auto_harness.schema.CodeChangeArtifact
```

Implement 阶段输出。

**字段**：

* **related**(`List[Experience]`)：相关经验记录列表。默认 `[]`。
* **edited_files**(`List[str]`)：已编辑文件列表。默认 `[]`。

---

## class openjiuwen.auto_harness.schema.VerifyReportArtifact

```
@dataclass
class openjiuwen.auto_harness.schema.VerifyReportArtifact
```

Verify 阶段输出。

**字段**：

* **ci_result**(`Dict[str, Any]`)：CI 执行结果。默认 `{}`。
* **fix_errors**(`str`)：修复错误信息。默认 `""`。
* **reverted**(`bool`)：是否已回滚。默认 `False`。
* **error**(`str`)：错误信息。默认 `""`。

---

## class openjiuwen.auto_harness.schema.CommitArtifact

```
@dataclass
class openjiuwen.auto_harness.schema.CommitArtifact
```

Commit 阶段输出。

**字段**：

* **facts**(`CommitFacts | None`)：提交事实快照。默认 `None`。
* **status_text**(`str`)：状态文本。默认 `""`。
* **last_commit_stat**(`str`)：最近一次提交的统计信息。默认 `""`。
* **branch_name**(`str`)：分支名称。默认 `""`。
* **committed**(`bool`)：是否已成功提交。默认 `False`。
* **error**(`str`)：错误信息。默认 `""`。

---

## class openjiuwen.auto_harness.schema.PullRequestArtifact

```
@dataclass
class openjiuwen.auto_harness.schema.PullRequestArtifact
```

Publish 阶段输出。

**字段**：

* **pr_url**(`str`)：PR 链接。默认 `""`。
* **summary**(`str`)：PR 摘要。默认 `""`。

---

## class openjiuwen.auto_harness.schema.PullRequestDraft

```
@dataclass
class openjiuwen.auto_harness.schema.PullRequestDraft
```

由 communicate agent 生成的结构化 PR 草稿。

**字段**：

* **title**(`str`)：PR 标题。默认 `""`。
* **body**(`str`)：PR 正文。默认 `""`。
* **kind**(`str`)：PR 类型。默认 `""`。

---

## class openjiuwen.auto_harness.schema.CommitFacts

```
@dataclass
class openjiuwen.auto_harness.schema.CommitFacts
```

提交阶段的事实快照。

**字段**：

* **branch_name**(`str`)：分支名称。默认 `""`。
* **task_declared_files**(`List[str]`)：任务声明的文件列表。默认 `[]`。
* **preexisting_dirty_files**(`List[str]`)：预先存在的脏文件列表。默认 `[]`。
* **current_dirty_files**(`List[str]`)：当前脏文件列表。默认 `[]`。
* **tracked_modified_files**(`List[str]`)：已跟踪且已修改的文件列表。默认 `[]`。
* **untracked_files**(`List[str]`)：未跟踪文件列表。默认 `[]`。
* **edited_files**(`List[str]`)：已编辑文件列表。默认 `[]`。
* **allowed_files**(`List[str]`)：允许提交的文件列表。默认 `[]`。
* **derived_test_files**(`List[str]`)：推导出的测试文件列表。默认 `[]`。
* **legacy_related_test_files**(`List[str]`)：遗留相关测试文件列表。默认 `[]`。
* **verify_related_files**(`List[str]`)：验证相关文件列表。默认 `[]`。
* **diff_stat**(`str`)：diff 统计信息。默认 `""`。

---

## class openjiuwen.auto_harness.schema.ProjectProfile

```
@dataclass
class openjiuwen.auto_harness.schema.ProjectProfile
```

项目画像，承载 repo-specific 默认值。

**字段**：

* **name**(`str`)：项目名称。默认 `"agent-core"`。
* **repo_url**(`str`)：仓库 URL。默认 `"https://gitcode.com/openJiuwen/agent-core.git"`。
* **repo_slug**(`str`)：仓库 slug。默认 `"openJiuwen/agent-core"`。
* **platform**(`str`)：代码托管平台。默认 `"gitcode"`。
* **immutable_files**(`List[str]`)：不可变文件列表。默认为内置的 3 个文件（identity.md、ci_gate.yaml、prompt_security_rail.py）。
* **high_impact_prefixes**(`List[str]`)：高影响路径前缀列表。默认 `["openjiuwen/core/"]`。
* **default_base_branch**(`str`)：默认基础分支。默认 `"develop"`。
* **default_ci_profile**(`str`)：默认 CI 配置。默认 `"default"`。

---

## class openjiuwen.auto_harness.schema.AutoHarnessPaths

```
@dataclass
class openjiuwen.auto_harness.schema.AutoHarnessPaths
```

运行所需的路径派生结果。

**字段**：

* **data_dir**(`str`)：数据根目录。默认 `""`。
* **experience_dir**(`str`)：经验库目录。默认 `""`。
* **worktrees_dir**(`str`)：Worktree 根目录。默认 `""`。
* **runs_dir**(`str`)：运行记录目录。默认 `""`。
* **cache_repo_dir**(`str`)：Clone 缓存目录。默认 `""`。
* **runtime_extensions_dir**(`str`)：运行时扩展目录。默认 `""`。

---

## class openjiuwen.auto_harness.schema.AutoHarnessRuntimeState

```
@dataclass
class openjiuwen.auto_harness.schema.AutoHarnessRuntimeState
```

运行时状态。

**字段**：

* **current_workspace**(`str`)：当前工作空间路径。默认 `""`。
* **selected_pipeline**(`str`)：已选中的 pipeline 名称。默认 `""`。
* **config_bootstrapped**(`bool`)：配置是否已自动引导创建。默认 `False`。
* **suggested_local_repo**(`str`)：建议的本地仓库路径。默认 `""`。
* **session_id**(`str`)：会话唯一标识，自动生成 12 位十六进制字符串。默认 `uuid.uuid4().hex[:12]`。

---

## class openjiuwen.auto_harness.schema.StageResult

```
@dataclass
class openjiuwen.auto_harness.schema.StageResult
```

统一 stage 执行结果。

**字段**：

* **status**(`str`)：执行状态。默认 `"success"`。
* **artifacts**(`Dict[str, Any]`)：产物字典。默认 `{}`。
* **messages**(`List[str]`)：消息列表。默认 `[]`。
* **metrics**(`Dict[str, Any]`)：指标字典。默认 `{}`。
* **error**(`str`)：错误信息。默认 `""`。

---

## class openjiuwen.auto_harness.schema.StageSpec

```
@dataclass
class openjiuwen.auto_harness.schema.StageSpec
```

Stage 的声明式元数据。

**字段**：

* **name**(`str`)：Stage 名称。（必填）
* **stage_cls**(`type[Any]`)：Stage 实现类。（必填）
* **scope**(`str`)：作用域。默认 `"session"`。
* **consumes**(`List[str]`)：消费的产物名称列表。默认 `[]`。
* **produces**(`List[str]`)：产出的产物名称列表。默认 `[]`。
* **description**(`str`)：Stage 描述。默认 `""`。
* **slot**(`str`)：对应的阶段槽位。默认 `""`。

---

## class openjiuwen.auto_harness.schema.PipelineSpec

```
@dataclass
class openjiuwen.auto_harness.schema.PipelineSpec
```

Pipeline 的声明式模板。

**字段**：

* **name**(`str`)：Pipeline 名称。（必填）
* **pipeline_cls**(`type[Any]`)：Pipeline 实现类。（必填）
* **description**(`str`)：Pipeline 描述。默认 `""`。
* **expected_outputs**(`List[str]`)：预期输出产物列表。默认 `[]`。

---

## class openjiuwen.auto_harness.schema.AutoHarnessConfig

```
@dataclass
class openjiuwen.auto_harness.schema.AutoHarnessConfig
```

Auto Harness Agent 配置。

`data_dir` 由宿主 CLI 传入，所有产物（经验库、运行记录、clone 缓存、worktree）都存放在此目录下。

`local_repo` 可选，指向本地 agent-core 仓库路径，用于加速 worktree 创建。未配置时自动 clone 到 `{data_dir}/repo/agent-core`。

**样例**：
```python
>>> from openjiuwen.auto_harness.schema import AutoHarnessConfig
>>> config = AutoHarnessConfig(
...     data_dir="/tmp/auto_harness",
...     local_repo="/path/to/agent-core",
...     language="cn",
... )
>>> config.resolved_experience_dir
'/tmp/auto_harness/experience'
```

**字段**：

* **model**(`Optional[Model]`)：默认使用的 LLM 模型。默认 `None`。
* **plan_model**(`Optional[Model]`)：规划阶段使用的 LLM 模型。默认 `None`。
* **data_dir**(`str`)：数据根目录。默认 `""`。
* **local_repo**(`str`)：本地 agent-core 仓库路径。默认 `""`。
* **repo_url**(`str`)：远程仓库 URL。默认 `"https://gitcode.com/openJiuwen/agent-core.git"`。
* **skills_dirs**(`List[str]`)：技能目录列表。默认 `[]`。
* **community_skill_repos**(`List[str]`)：社区技能仓库列表。默认 `["https://github.com/anthropics/skills.git", "https://github.com/JimLiu/baoyu-skills.git"]`。
* **community_skill_cache_dir**(`str`)：社区技能缓存目录。默认 `""`。
* **stage_registrars**(`List[str]`)：Stage 注册器模块路径列表。默认 `[]`。
* **pipeline_registrars**(`List[str]`)：Pipeline 注册器模块路径列表。默认 `[]`。
* **language**(`str`)：输出语言。默认 `"cn"`。
* **optimization_goal**(`str`)：优化目标描述。默认 `""`。
* **pipeline_preference**(`str`)：Pipeline 偏好。默认 `"auto"`。
* **session_budget_secs**(`float`)：会话总预算（秒）。默认 `900000.0`。
* **cost_limit_usd**(`float`)：费用上限（美元）。默认 `10.0`。
* **task_timeout_secs**(`float`)：单任务超时时间（秒）。默认 `300000.0`。
* **model_timeout_secs**(`float`)：模型调用超时时间（秒）。默认 `300000.0`。
* **max_tasks_per_session**(`int`)：每会话最大任务数。默认 `10`。
* **self_driven_slots**(`int`)：自驱动槽位数。默认 `1`。
* **extension_verify_concurrency**(`int`)：扩展验证并发数。默认 `4`。
* **git_remote**(`str`)：Git remote 名称。默认 `""`。
* **git_base_branch**(`str`)：基础分支。默认 `"develop"`。
* **git_user_name**(`str`)：Git 用户名。默认 `""`。
* **git_user_email**(`str`)：Git 用户邮箱。默认 `""`。
* **fork_owner**(`str`)：Fork 所有者。默认 `""`。
* **upstream_owner**(`str`)：上游仓库所有者。默认 `"openJiuwen"`。
* **upstream_repo**(`str`)：上游仓库名称。默认 `"agent-core"`。
* **gitcode_username**(`str`)：GitCode 用户名。默认 `""`。
* **gitcode_token**(`str`)：GitCode 访问令牌。默认 `""`。
* **gitcode_token_env**(`str`)：GitCode token 环境变量名。默认 `"GITCODE_ACCESS_TOKEN"`。
* **ci_gate_config**(`str`)：CI 门控配置文件路径。默认 `""`。
* **ci_gate_python_executable**(`str`)：CI 门控 Python 可执行文件路径。默认 `""`。
* **ci_gate_install_command**(`str`)：CI 门控安装命令。默认 `""`。
* **fix_phase1_max_retries**(`int`)：Fix Loop 阶段一最大重试次数。默认 `10`。
* **fix_phase2_max_retries**(`int`)：Fix Loop 阶段二最大重试次数。默认 `9`。
* **immutable_files**(`List[str]`)：不可变文件列表。默认 `[]`。
* **high_impact_prefixes**(`List[str]`)：高影响路径前缀列表。默认 `["openjiuwen/core/"]`。
* **agent_iterations**(`Dict[str, int]`)：各 agent 阶段的最大迭代次数映射。默认 `{"implement": 30, "assess": 30, "plan": 15, "select_pipeline": 10, "eval": 10, "pr_draft": 5, "learnings": 5, "explore_subagent": 20, "browser_subagent": 20, "merge_ext": 8}`。
* **workspace**(`str`)：工作空间路径（已废弃，保留兼容）。默认 `""`。
* **config_path**(`str`)：配置文件路径。默认 `""`。
* **config_bootstrapped**(`bool`)：配置是否已自动引导创建。默认 `False`。
* **suggested_local_repo**(`str`)：建议的本地仓库路径。默认 `""`。
* **experience_dir**(`str`)：经验库目录（显式指定时使用）。默认 `""`。

### resolved_experience_dir

```
@property
resolved_experience_dir -> str
```

经验库目录，从 data_dir 派生。

**返回**：`str` —— 经验库目录路径。

### worktrees_dir

```
@property
worktrees_dir -> str
```

Worktree 根目录，从 data_dir 派生。

**返回**：`str` —— Worktree 根目录路径。

### runs_dir

```
@property
runs_dir -> str
```

运行记录目录，从 data_dir 派生。

**返回**：`str` —— 运行记录目录路径。

### cache_repo_dir

```
@property
cache_repo_dir -> str
```

Clone 缓存目录，从 data_dir 派生。

**返回**：`str` —— Clone 缓存目录路径。

### runtime_extensions_dir

```
@property
runtime_extensions_dir -> str
```

Session-local runtime extensions root.

**返回**：`str` —— 会话级运行时扩展根目录路径。

### resolved_community_skill_cache_dir

```
@property
resolved_community_skill_cache_dir -> str
```

Community skill repo cache directory.

**返回**：`str` —— 社区技能仓库缓存目录路径。

### resolve_repo_name(self) -> str

解析本地缓存路径使用的仓库目录名。

**返回**：`str` —— 仓库目录名称。

### resolve_gitcode_token(self) -> str

解析 GitCode token。优先使用 `gitcode_token`，否则从 `gitcode_token_env` 指定的环境变量读取。

**返回**：`str` —— Token 字符串，未配置时返回空字符串。

### resolve_gitcode_username(self) -> str

解析用于 git HTTPS 认证的 GitCode 登录用户名。

**返回**：`str` —— GitCode 用户名。

### resolve_ci_gate_python_executable(self) -> str

解析 CI 门控命令使用的 Python 可执行文件路径。

**返回**：`str` —— Python 可执行文件路径。

### resolve_agent_iterations(self, stage_name: str, default: int) -> int

解析 agent stage 的最大迭代次数。

**参数**：
* **stage_name**(`str`)：阶段名称。
* **default**(`int`)：默认迭代次数。

**返回**：`int` —— 该阶段的最大迭代次数。

### resolve_immutable_files(self) -> List[str]

返回已配置的不可变文件列表，若未配置则返回内置默认值。

**返回**：`List[str]` —— 不可变文件路径列表。

### build_project_profile(self) -> ProjectProfile

构建仓库专属的项目画像。

**返回**：`ProjectProfile` —— 项目画像实例。

### build_paths(self) -> AutoHarnessPaths

构建派生的运行时路径。

**返回**：`AutoHarnessPaths` —— 运行时路径实例。

### load_from_dict(data: Dict[str, Any]) -> 'AutoHarnessConfig'

`@staticmethod`

从字典构建配置，支持嵌套 YAML 结构。

**参数**：
* **data**(`Dict[str, Any]`)：配置字典，支持顶层和嵌套 key（如 `git`、`gitcode`、`budget`、`ci_gate`、`fix_loop`、`agent`、`extensions`）。

**返回**：`AutoHarnessConfig` —— 填充后的配置实例。

---

## openjiuwen.auto_harness.schema.load_auto_harness_config

```
load_auto_harness_config(config_path: str, workspace_hint: str = '') -> AutoHarnessConfig
```

从 YAML 文件加载 AutoHarnessConfig。文件不存在时自动生成模板并返回默认配置。

**参数**：
* **config_path**(`str`)：YAML 配置文件路径。
* **workspace_hint**(`str`)：当前 CLI 工作目录，用于推断建议的 `local_repo`。默认 `""`。

**返回**：`AutoHarnessConfig` —— 填充后的配置实例。

---

## openjiuwen.auto_harness.schema.is_placeholder_local_repo

```
is_placeholder_local_repo(path: str) -> bool
```

判断 `path` 是否为模板/示例值。

**参数**：
* **path**(`str`)：待检查的路径字符串。

**返回**：`bool` —— 若路径为已知的模板/示例值则返回 `True`。

---

## class openjiuwen.auto_harness.schema.ActivateDecision

```
@dataclass
class openjiuwen.auto_harness.schema.ActivateDecision
```

activate stage 的用户决策结果。

**字段**：

* **action**(`str`)：用户决策动作。默认 `"accept"`。
* **feedback**(`str`)：用户反馈内容。默认 `""`。
