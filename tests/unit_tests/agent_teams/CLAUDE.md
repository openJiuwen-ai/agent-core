# tests/unit_tests/agent_teams

`openjiuwen/agent_teams/` 子系统的单元测试目录。在本目录内修改或新增代码时，
请遵循以下约定（补充根目录 `AGENTS.md` / `CLAUDE.md` 的规则）。

## 目录结构

```
tests/unit_tests/agent_teams/
├── test_team*.py                 # Team/TeamAgent/Team Rail 相关
├── test_coordination_*.py        # Coordinator 协调循环/生命周期
├── test_member.py                # Member 规范与装配
├── test_policy.py                # 策略/调度
├── test_persistent_team.py       # 可持久化团队
├── test_predefined_team.py       # 预定义团队装配
├── test_messager.py              # Messager 通道（inprocess / pyzmq）
├── test_message_manager.py       # MessageManager
├── test_task_manager.py          # TaskManager（任务状态机）
├── test_team_tools.py            # team tools（工具暴露与权限）
├── test_team_agent_tools.py      # team agent 侧工具
├── test_database.py              # SQLite schema / CRUD / migration
├── test_paths.py                 # 可配置路径、Windows junction 兼容
├── team_workspace/               # TeamWorkspace（manager/models）
├── worktree/                     # worktree 隔离（backend/git/slug/session 等）
├── docs/                         # 测试设计文档（含 test_design.md）
├── logs/                         # 测试运行时日志输出（勿手动提交）
└── report/                       # 测试报告产物（pytest-html/coverage 等）
```

被测源码位置：`openjiuwen/agent_teams/`。测试文件命名与被测模块一一对应，
新增被测模块时，请在同位置补测试文件。

## 被测源码对应关系

| 被测模块路径 | 对应测试文件 |
| --- | --- |
| `openjiuwen/agent_teams/agent/team.py` | `test_team.py` |
| `openjiuwen/agent_teams/agent/team_agent.py` | `test_team_agent.py`、`test_team_agent_tools.py` |
| `openjiuwen/agent_teams/agent/coordinator.py`、`dispatcher.py` | `test_coordination_lifecycle.py`、`test_coordination_loop.py`、`test_team_agent_coordination.py` |
| `openjiuwen/agent_teams/agent/member.py` | `test_member.py` |
| `openjiuwen/agent_teams/agent/policy.py` | `test_policy.py` |
| `openjiuwen/agent_teams/agent/team_rail.py`、`rails.py` | `test_team_rail.py` |
| `openjiuwen/agent_teams/agent/team_section_cache.py` | `test_team_section_cache.py` |
| `openjiuwen/agent_teams/messager/` | `test_messager.py`、`test_message_manager.py` |
| `openjiuwen/agent_teams/tools/task_manager.py` | `test_task_manager.py` |
| `openjiuwen/agent_teams/tools/team_tools.py`、`team.py` | `test_team_tools.py` |
| `openjiuwen/agent_teams/tools/database.py`、`memory_database.py` | `test_database.py` |
| `openjiuwen/agent_teams/paths.py` | `test_paths.py` |
| `openjiuwen/agent_teams/team_workspace/` | `team_workspace/` |
| `openjiuwen/agent_teams/worktree/` | `worktree/` |

## 运行方式

项目根目录执行（先激活环境）：

```bash
source .venv/bin/activate
export PYTHONPATH=.:$PYTHONPATH

# 全量
make test TESTFLAGS="tests/unit_tests/agent_teams/"

# 单文件
make test TESTFLAGS="tests/unit_tests/agent_teams/test_team.py"

# 单用例
make test TESTFLAGS="tests/unit_tests/agent_teams/test_team.py::TestTeam::test_xxx"

# 子目录
make test TESTFLAGS="tests/unit_tests/agent_teams/worktree/"
```

## 编写约定

### 通用

- 测试函数以 `test_` 开头，类以 `Test` 开头；异步用例加 `@pytest.mark.asyncio`。
- Python 3.11+ 语法；函数入参声明类型注解，利用类型系统替代 `hasattr`。
- docstring 英文（Google Style）；对话/提交信息用中文/英文分别遵守根目录规则。
- 禁止 `print` 打印日志，统一使用 `test_logger`。
- 禁用海象运算符 `:=`；Protocol 抽象方法不得单行 `def foo(): ...`。
- 推导式最多两个子句且单行，复杂条件提取变量。

### Fixture 与隔离

- `worktree/conftest.py` 已提供 `worktree_config`、`mock_messager`、
  `tmp_git_repo` 等 fixture，新增 worktree 用例优先复用；需要扩展时在同文件
  追加，不要散落到各 `test_*.py`。
- 涉及 SQLite 的测试使用 `tmp_path` 生成隔离数据库文件，禁止使用仓库内
  固定路径；session 级共享状态必须在用例结束时清理。
- `Runner.resource_mgr` 为进程全局；跨用例共享资源时用稳定 ID 并在
  teardown 显式释放，避免用例相互污染。
- 涉及外部 I/O 的组件（Messager、Git、模型调用）用 `AsyncMock` / `MagicMock`
  打桩；本目录不连真实模型与网络。

### Mock 原则

- 仅在系统边界 mock（模型、子进程、git subprocess、文件系统之外）。
- 不 mock 被测模块自身的内部函数——如果需要，说明抽象有缺陷，先重构
  被测模块而不是在测试里打补丁。
- 数据库不要用 mock 层替代真实 SQLite；用临时文件 + 真实 schema 才能守住
  migration 回归（参见 `test_database.py` 94 条覆盖）。

### 异步与生命周期

- 协调循环、messager、coordinator 的异步任务必须在用例结束前 `await`
  或 `cancel`；静默泄漏会污染后续用例。
- 生命周期相关断言参考 `test_coordination_lifecycle.py` 的关闭收敛模式。

### 断言粒度

- 优先断言状态机/数据结构的外部可观察结果，不断言实现细节（如日志字符串、
  内部调用次数）。
- 涉及 rails 工具注入时，断言工具 schema 或透出列表，而不是耦合具体注册
  顺序。

## 用例分级

参考 `docs/test_design.md` 的 Level 0 / Level 1 划分：

- **Level 0**：默认构造 + 主正向路径。PR gate 必须全绿。
- **Level 1**：参数组合、异常分支、并发/生命周期边界、schema 兼容。

目前未用 `pytest.mark.level0/level1` 硬编码，如需按级别筛选请在 PR 里
先批量打标再启用。

## 常见陷阱

- 修改 `openjiuwen/agent_teams/tools/database.py` 的 schema 时，必须
  在 `test_database.py` 增补对应 migration/字段用例；缺失会把静默 drop
  带到生产（参见 commit `8bdf9fa7`）。
- `team_workspace` 的 `version_control` flag 行为须在新增场景下保留
  honor 语义（commit `3215a3c4`）。
- 协调循环相关修复先复现失败用例，再动生产代码（参见 commit `afa1051e`
  中 4 个 stale-task 用例）。
- `logs/`、`report/` 为运行产物目录，勿手动提交运行时产物；测试设计/说明
  类文档统一放 `docs/`。

## 相关文档

- 设计文档：`tests/unit_tests/agent_teams/docs/test_design.md`
- 架构与变更规则：`.claude/rules/architecture.md`
- 测试规则：`.claude/rules/testing.md`
- Python 编码风格：`.claude/rules/python/coding-style.md`
