# openjiuwen.auto_harness.resources

Auto Harness 资源配置模板参考。包含两个 YAML 配置文件模板：`config.yaml`（主配置）和 `ci_gate.yaml`（CI 门禁配置）。这些文件定义了 auto-harness 运行时的行为参数，不是 Python API，而是部署和运维层面的配置参考。

---

## config.yaml

主配置文件模板。放置位置由宿主 CLI 决定：
- openjiuwen CLI → `~/.openjiuwen/auto_harness/config.yaml`
- jiuwenclaw → `~/.jiuwenclaw/auto_harness/config.yaml`

### 完整结构

```yaml
# 可选：本地 agent-core 仓库路径
# 配置后使用 git worktree 加速，不配则自动 clone 到 data_dir/repo/
# local_repo: "/home/user/code/agent-core"

# 可选：远端仓库 URL
# repo_url: "https://gitcode.com/openJiuwen/agent-core.git"

# 可选：session pipeline。auto 按目标信号选择，meta/extended 强制指定
# pipeline: "auto"

# 可选：Agent 提示词与 PR 模版语言。支持 "cn"（中文）或 "en"（英文）
# language: "cn"

# 可选：自定义 skills 目录，与框架内置 skills 合并
# skills_dirs:
#   - "/path/to/my-skills"

# 可选：覆盖不可编辑文件；留空时使用框架内置默认值
# immutable_files:
#   - "openjiuwen/auto_harness/prompts/identity.md"

# 可选：经验库目录
# experience_dir: "/home/user/.openjiuwen/auto_harness/experience"

git:
  remote: ""                    # fork remote 名称
  base_branch: "develop-auto-harness"        # PR 目标分支
  user_name: ""                 # git commit 用户名
  user_email: ""                # git commit 邮箱
  fork_owner: ""                # fork 所有者
  upstream_owner: "openJiuwen"  # 上游仓库所有者
  upstream_repo: "agent-core"   # 上游仓库名称

gitcode:
  username: ""                                 # GitCode 登录用户名；为空时回退到 git.fork_owner
  access_token_env: "GITCODE_ACCESS_TOKEN"    # 从此环境变量读取 token

budget:
  session_secs: 900000           # 会话总预算（秒）
  cost_limit_usd: 10.0          # 费用上限（美元）
  task_timeout_secs: 300000      # 单任务超时（秒）
  model_timeout_secs: 300000     # 单次 LLM 请求超时（秒）
  max_tasks_per_session: 5      # 每会话最大任务数

ci_gate:
  config_path: ""               # CI 门控配置路径，空则用仓库内默认
  python_executable: ""         # 固定 CI gate 使用的 Python；为空时回退到 .venv 或当前解释器
  install_command: ""           # 可选环境预热命令

fix_loop:
  phase1_max_retries: 10        # Phase 1 最大重试次数
  phase2_max_retries: 9         # Phase 2 最大重试次数

agent:
  implement: 60                 # implement agent max_iterations
  assess: 60                    # assess agent max_iterations
  plan: 60                      # plan agent max_iterations
  select_pipeline: 20           # pipeline selector max_iterations
  eval: 20                      # evaluator max_iterations
  pr_draft: 20                  # PR draft agent max_iterations
  learnings: 20                 # learnings agent max_iterations

extensions:
  stage_registrars: []          # module:callable，签名 (StageRegistry) -> None
  pipeline_registrars: []       # module:callable，签名 (PipelineRegistry, StageRegistry) -> None
```

### 各节说明

| 节 | 说明 |
|---|---|
| **顶层可选字段** | `local_repo`、`repo_url`、`pipeline`、`language`、`skills_dirs`、`immutable_files`、`experience_dir` 等全局配置 |
| **git** | Git 远程仓库配置，包括 fork remote、目标分支、用户信息、上下游仓库所有者 |
| **gitcode** | GitCode 平台认证配置，`username` 为空时回退到 `git.fork_owner`，token 从环境变量读取 |
| **budget** | 资源预算控制，包括会话总时长、费用上限、单任务超时、LLM 请求超时、最大任务数 |
| **ci_gate** | CI 门禁配置，包括自定义配置路径、Python 解释器路径、环境预热命令 |
| **fix_loop** | 修复循环配置，Phase 1（CI 修复）和 Phase 2（评审修复）的最大重试次数 |
| **agent** | 各 agent 的 `max_iterations` 上限，控制每个阶段的 agent 迭代次数 |
| **extensions** | 扩展注册点，通过 `module:callable` 格式注册自定义阶段和流水线 |

---

## ci_gate.yaml

CI 门禁规则配置文件模板。此文件被 `ImmutableFileRail` 保护，运行时不可修改。

### 完整结构

```yaml
# CI Gate Rules — Auto Harness Agent
# IMMUTABLE: This file is protected by ImmutableFileRail

immutable_files:
  - auto_harness/prompts/identity.md
  - auto_harness/resources/ci_gate.yaml
  - auto_harness/agent.py
  - auto_harness/orchestrator.py
  - auto_harness/infra/fix_loop.py
  - openjiuwen/harness/rails/security/prompt_security_rail.py

high_impact:
  - "openjiuwen/core/**"

requires_human_approval:
  - pyproject.toml
  - openjiuwen/harness/__init__.py
  - openjiuwen/harness/factory.py
  - openjiuwen/core/__init__.py

ci_gates:
  - name: lint
    command: "make check COMMITS=1"
    required: true
  - name: type-check
    command: "make type-check COMMITS=1"
    required: true

fix_loop:
  phase1_max_retries: 10
  phase1_timeout_per_attempt: 600
  phase2_max_retries: 9
  revert_on_exhaustion: true

session_budget:
  wall_clock_secs: 3600
  cost_limit_usd: 10.0
  task_timeout_secs: 1200

task_constraints:
  max_tasks_per_session: 10
  max_files_per_task: 3
  self_driven_slots: 1
```

### 各节说明

| 节 | 说明 |
|---|---|
| **immutable_files** | 不可编辑文件列表。这些文件在 auto-harness 运行期间禁止被 agent 修改，由 `ImmutableFileRail` 拦截 |
| **high_impact** | 高影响范围 glob 模式。匹配这些路径的文件变更会被标记为高影响，可能触发更严格的审查 |
| **requires_human_approval** | 需要人工审批的文件列表。修改这些文件时 auto-harness 会暂停并等待人工确认 |
| **ci_gates** | CI 门禁定义。每个门禁包含 `name`（名称）、`command`（执行命令）、`required`（是否必须通过） |
| **fix_loop** | 修复循环参数。`phase1_max_retries`：CI 修复最大重试次数；`phase1_timeout_per_attempt`：每次尝试超时（秒）；`phase2_max_retries`：评审修复最大重试次数；`revert_on_exhaustion`：耗尽重试后是否回滚 |
| **session_budget** | Session 级预算。`wall_clock_secs`：总墙钟时间（秒）；`cost_limit_usd`：费用上限（美元）；`task_timeout_secs`：单任务超时（秒） |
| **task_constraints** | 任务约束。`max_tasks_per_session`：每 session 最大任务数；`max_files_per_task`：每任务最大文件数；`self_driven_slots`：自驱动槽位数 |
