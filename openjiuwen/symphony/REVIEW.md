# Symphony AI 检视指引

AI 先比较基线与候选版本，按本次改动选择适用条目，从实际调用入口追踪到产物和消费方，并用相关测试核实行为。问题等级、定位、证据与验证范围沿用 [jw-review](../../.agents/skills/jw-review/SKILL.md)。

## AI 检查项

- 检查**接口与调用方**时，核对公开接口和跨模块调用契约的差异，追踪受影响的 SDK 和宿主调用方，检查兼容性与 README 的用法和示例。
  [公共 API 回归](../../tests/unit_tests/symphony/test_public_api.py)、[模块 README](README.md)
- 修改**配置**时，核对 `config.py` 中的默认值是否在实际入口生效，子服务是否读取对应的子配置，以及 `config.tree.txt` 是否与生成结果一致。
  [配置模型](config.py)、[配置导出回归](../../tests/unit_tests/symphony/test_config_export.py)
- 检查**索引与图产物发布**时，核对产物是否绑定规范化来源集合及发布版本，全量和增量发布前是否复核来源快照，以及失败或取消后能否继续读取上次成功发布的产物。
  [索引发布回归](../../tests/unit_tests/symphony/test_discovery_index_lifecycle.py)、[混合集合与图发布回归](../../tests/unit_tests/symphony/test_mixed_taxonomy.py)
- 检查**发现与编排**时，核对 Skill 和专家是否按类型分层并遵守会话可见范围和禁用条件。确认专家任务图无环，交接边引用本轮声明的源产物和 Core 生成的接收端口。
  核对候选专家仅以标识、名称和描述进入编排提示词，任务产物和交接由本轮请求决定。内联角色之间的交接按旧草稿显式声明的接收类型校验。
  [发现回归](../../tests/unit_tests/symphony/test_discovery_index_lifecycle.py)、[专家任务图回归](../../tests/unit_tests/symphony/test_expert_task_planning.py)
- 检查**观测与 Flow**时，核对 `orchestration.plan.dynamic_graph_enabled` 是否独立控制 Skill 规划读取已有动态观测。
  确认编排和经验开关共同控制新观测写入与 Runtime Flow，禁用时直接写入和 Flow 执行在创建存储前被拒绝。涉及 `aclose()` 时，检查在途任务和文件写入能否在关闭完成前收尾。
  [规划开关回归](../../tests/unit_tests/symphony/test_orchestration_service.py)、[Runtime 开关与关闭回归](../../tests/unit_tests/symphony/test_runtime_config_integration.py)

## 需人工确认

在检视报告中单列「需人工确认」。

- 新增、修改或移除公开接口，或改变宿主和下游依赖的调用契约时，必须提醒人工检查。范围包括导出、签名、输入输出、配置及默认值、错误语义和供下游读取的产物格式。向后兼容且测试通过的接口改动同样要提醒。
- AI 检查后仍缺关键证据时，列出人工需要核实的具体场景，例如实际宿主能否消费新结果，或已有配置和产物能否继续读取。
- 每项给出位置、改动前后、受影响的调用方、已有验证证据，以及请人工确认的问题。已有检视发现可直接引用。没有待确认事项时写「无」。
