# RSI Skill 资格与自然激活改造报告

## 1. 本阶段目标

本阶段修复单 Harness 优化中“一个题目中的局部扣分点被直接写成 Skill，
随后模型认为不相关而不调用”的问题。目标不是强制调用 Skill，而是让系统只
生成真正可复用、可被任务早期信号自然路由的 Skill。

## 2. 已确认的根因

1. Analyzer 可以从一个 Case 的一个评分子项直接选择 Skill 修改面。
2. Planner 曾把 `during_investigation` 机械转换为 Skill，但“何时需要介入”
   并不能证明“该方法能跨题复用”。
3. 失败候选中曾出现固定合同编号、固定行数、已知异常文件名等 Case 专属内容。
   这类内容即使提升当前 Case，也不是可迁移能力。
4. JiuwenSwarm 会把 Skill 名称和 description 暴露给模型，由模型自然选择是否
   调用 `skill_tool`；当前 RSI 并未强制调用候选 Skill。

## 3. 已完成的代码改造

### 3.1 分离激活时机与能力复用

`during_investigation` 不再自动把 Prompt 诊断改写为 Skill。激活阶段只回答
行为在什么时候需要，Skill 资格由独立的跨 Case 证据判断。

### 3.2 增加 Skill 资格门槛

- 明确只有一个支持 Case：将 Skill 修改面降级为 `prompt_section`；
- 至少两个独立 Case 命中同一失败机制：允许继续规划 Skill；
- Planner 若仍尝试为单 Case 目标生成 Skill 动作，确定性校验直接拒绝；
- Analyzer 和 Planner 提示同时要求移除 Case ID、Verifier 子项、固定数量、
  已知答案和特定异常文件名。

### 3.3 保持自然激活口径

实验不强制模型调用 Skill。只有轨迹中成功完成
`skill_tool(skill_name=...)`，并且发生在持久化编辑之前，才记为 Skill 对本次
决策产生了可用的激活证据。

### 3.4 修复 JiuwenSwarm Skill 接入与证据统计

真实运行中进一步发现并修复了三个基础问题：

1. 编译器原来把 `skills/<skill_name>` 直接交给 `SkillUseRail`，但该 Rail
   要求的是包含多个 Skill 子目录的父目录。现在把 manifest 明确声明的 Skill
   复制到隔离的白名单根目录后再挂载，不会顺带暴露未声明 Skill。
2. JiuwenSwarm 0.2.3 的原生提示要求读取 `SKILL.md`，却没有提供运行时路径。
   现在增加精确 runtime name 和 `skill_tool` 入口说明；只说明如何调用，不替
   模型选择 Skill，也不强制调用。
3. JiuwenSwarm 轨迹的工具步骤使用 `type: tool`，旧统计器只识别
   `kind: tool`。现在兼容两种格式，并把 `code` 中的文件保存识别为持久化编辑，
   避免真实调用被误报为 `expected_skill_not_invoked_on_target_case`。

## 4. 验收设计

单元测试覆盖以下边界：

- 调查阶段的 Prompt 不再被自动升级为 Skill；
- 单 Case Skill 自动回退到 Prompt，Skill 动作被拒绝；
- 两个 Case 支持同一机制时仍可生成 Skill。

真实运行使用一个不含题目答案的通用 `spreadsheet-delivery-preflight` Skill，
选择需要从本地记录生成周汇总工作簿的 `ticket-weekly-L3-010`。验收同时检查：

1. Skill 是否由模型自然调用；
2. 调用是否发生在读取源数据和编辑文件之前；
3. 官方 Verifier 分数和交付物是否正常。

## 5. 当前结论

代码门槛、接入修复和真实 Case 验收已经完成。最终运行结果：

| 项目 | 结果 |
|---|---|
| Case | `ticket-weekly-L3-010` |
| 候选 Skill | `spreadsheet_delivery_preflight` |
| 调用方式 | 模型通过 `skill_tool` 自然选择，无强制 treatment |
| 调用时机 | 在首个持久化编辑之前 |
| 交付物 | `output/result.xlsx`，存在且非空 |
| 官方 Verifier | `1.0000`，15/15 原子检查通过 |
| 轨迹识别 | RSI 正确记录候选 Skill 与 `office_baseline` 的调用 |

迭代实验同时保留了失败证据：第一次运行因 Skill 根目录形状错误而未触发；修复
挂载后可以触发，但早期 Skill 版本因增加未请求合计行、周标签含糊而只有
`0.1333`；收紧通用交付规则后最终达到 `1.0000`。这说明“能被调用”和“调用后
有用”是两个必须分别验收的条件。

本次是单 Case 激活可行性验证，不是候选 Skill 的因果收益实验。由于最终轨迹中
同时调用了基线 Skill，且没有固定随机种子的配对对照，当前只能确认路由、执行和
评分链路闭环，不能据此宣称该候选 Skill 在总体数据集上稳定提升分数。下一步应在
至少两个适用 Case 上做同父、同配置的 paired evaluation，再决定是否发布。
