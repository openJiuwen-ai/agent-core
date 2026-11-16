# Symphony 开发约定

遵循仓库根 AGENTS.md 和 jw-coding-flow，不运行格式化或静态检查。
检视涉及 Symphony 的改动时，必须继续阅读 [REVIEW.md](REVIEW.md)。

## 开发流程

1. 开发前阅读相关规约、公共导出、实际调用方和现有测试，明确验收场景与兼容影响。
2. 实现后对照代码检查模块 [README.md](README.md)。对外接口、配置及默认值、使用方式或用户可见行为有变更时，必须同步更新对应说明和示例。契约变更同步更新规约，配置变更按下文重新生成配置树。
3. 完成文档同步后运行与改动相关的行为测试，记录已验证的场景和待核实项。
4. 交接检视时提供受影响的接口和调用方、README 同步情况及测试证据。

## README 写作约定

- 面向 SDK 调用方和宿主集成方，用介绍项目的口吻写作。介绍能力时使用完整包名 `openjiuwen.symphony`，例如「openjiuwen.symphony 提供能力检索与编排」。说明用法时直接写调用步骤。
- 只介绍当前版本已实现且对外可用的功能。内部实现过程、开发进度和待办事项放在开发记录中。
- 优先说明公开接口、必要配置、参数和返回值，给出可照用的调用示例。
- 原理部分简述核心机制及影响使用的约束。

## 运行配置与生命周期

- `SymphonyRuntime(model, config)` 是统一组装入口；子服务直接使用 `config.py` 的子模型，解析快照不另设默认值。
- 配置接受非空的 `capability.sources` 列表。多个 Skill 和独立专家目录共用一棵分类树。编排以一个 Skill 目录为基础，可同时纳入专家目录；在同一图版本内分层匹配和规划。Flow 消费 Skill 层。构造只复制配置并固定路径。扫描、模型调用和后台任务由运行入口触发。
- `orchestration.experience.enabled` 与编排开关共同控制图观测写入和 Flow。规划使用已有动态图由 `orchestration.plan.dynamic_graph_enabled` 独立控制。
- 构建采用宿主注入模型；同步树算法通过宿主事件循环桥接。取消必须等待模型和文件写入工作收尾，不关闭宿主模型。
- 全量和增量发布前复核集合身份。失败或取消保留已发布版本。共用树按规范化来源集合隔离，单 Skill 产物布局保留。
- 配置有唯一可写路径；树结构与容量倍率在 retrieval.index_build.structure，任务策略在 index_build.task，构建展示及缓存指标在 index_build.output。
  指纹协议和一次性构树内部预算独立维护；模型连接、凭证和计量由宿主负责，禁止恢复 RetrievalModelConfig 和旧转换包装。
- `PUBLIC_CONFIG_MODELS` 覆盖 31 个公共树模型，`INTERNAL_CONFIG_MODELS` 登记 2 个内部模型。便利导出由 `__all__` 定义。
- Discovery 会话持有独立的 RetrievalConfig 副本，读取 prompt、output 和 use_existing_index。会话目录、可见和禁用对象、来源标签属于调用上下文。保持预算与索引版本冻结，并实时过滤禁用对象。

## 能力退役边界

- 旧模型检索服务、分支浏览工具、紧凑编码、检索专用模型后端和独立 `experience` 包已退役，不恢复配置或兼容别名。
- Discovery 继续使用 `retrieval/search/artifacts/loading.py`、`retrieval/search/runtime/lexical.py`
  和 `agent/retrieval_toolkit.py` 的发布恢复函数；这些共享路径不能随旧检索服务一并删除。
- 构树、指纹、编排、动态观测与 Flow 保留；Flow 的 `symphony.experience_recipe.v1` 是现有产物协议，
  不等于已退役的独立经验库。Runtime 使用显式新版构建签名，不能直接把全量配置 dump 作为缓存身份。

## 配置文档同步

- 配置字段、默认值和中文说明以 `openjiuwen/symphony/config.py` 为唯一来源。
- 修改该文件的模型、字段、默认值、校验、说明、公共/内部登记范围，或修改配置树生成脚本后，
  必须在本次开发交付前自动运行生成命令，无须再次询问用户：

  ```bash
  python scripts/export_symphony_config.py
  ```

- 执行前激活仓库已有 Python 环境并设置 PYTHONPATH；命令从仓库根运行。
  工作树没有 `.venv` 时可使用主仓已有虚拟环境，不为文档导出安装额外依赖。
- `openjiuwen/symphony/config.tree.txt` 是生成文件，与配置修改一同交付，不手工编辑。
  默认只展开 `SymphonyConfig` 的用户配置；`--include-internal` 可将保留模型参考导出到
  `config.reference.txt`，也可配合 `--output` 指定路径。内部模型类名不表示额外的 Runtime 输入。
- 配置变更的验证包含 `tests/unit_tests/symphony/test_config.py` 和 `test_config_export.py`；
  后者验证树形输出、生成文件一致性和独立导出入口。
- 自动导出由开发收尾流程执行，不在 Runtime 导入或启动时触发，不安装文件监听器或 Git hook。
