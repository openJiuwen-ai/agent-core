---
name: verify
description: 验证规范 — 定义实现阶段应满足的验证等级与通过标准
immutable: true
tools:
  - read_file
  - bash_tool
  - glob_tool
  - grep_tool
---

# Verify Skill

你是实现阶段引用的验证规范，负责约束代码变更的验证等级和通过标准。

## 验证级别

根据变更范围选择验证级别：

| 变更类型 | 验证级别 | 必须通过 |
|---------|---------|---------|
| 单文件修改 | L1: lint + 相关单测 | ruff check, pytest 目标文件 |
| 多文件修改 | L2: L1 + 类型检查 | + pyright 目标模块 |
| 跨模块修改 | L3: L2 + 全量测试 | + pytest 全量 |
| 公共 API 变更 | L4: L3 + 示例验证 | + examples/ 可运行 |

## 命令参考

```bash
# 先确认解释器环境。项目要求 Python 3.11+，优先使用 uv 管理的环境，
# 不要直接使用系统 python，避免落到 Python 3.10 而缺少 tomllib。
uv run python --version

# L1: lint
uv run ruff check <file>

# L1: 单测
uv run pytest tests/unit_tests/<path> -x -q

# L2: 类型检查
uv run pyright <file>

# L3: 全量测试
make test

# 查看变更
git diff HEAD~1
```

说明：
- `make test` 会在检测到 `uv` 时自动走 `uv run pytest`，这是全量测试的首选路径。
- 只在仓库明确没有 `uv` 时，才退回到其他 Python 执行方式；不要默认使用 `python -m pytest`。

## 评审要点

验证时关注：

1. **正确性**: 变更是否解决了任务描述的问题
2. **安全性**: 是否引入注入、路径遍历等风险
3. **兼容性**: 是否破坏现有 API 或行为
4. **测试**: 是否有对应的测试覆盖
5. **风格**: 是否符合项目编码规范

## CI Gate 流程

1. 运行 lint → 必须 0 错误
2. 运行单测 → 必须全部通过
3. 运行类型检查 → 不得引入新错误
4. 综合判断 → 输出 verdict
