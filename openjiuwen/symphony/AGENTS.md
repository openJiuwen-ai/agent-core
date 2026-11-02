# Symphony 开发约定

遵循仓库根 AGENTS.md 和 jw-coding-flow，不运行格式化或静态检查。

## 目标配置边界

- 用户可调策略通过 `SymphonyConfig` 的六个分组组合，每个参数只有一个可写路径。
  协议身份、模型后端及独立用途的算法和单次调用输入独立维护；归一化结果保留在原运行模块。
  目标 config.py 不再为旧入口的不同默认值声明同义镜像或包装，也不为测试恢复已退役模型。
  新配置尚未接入运行链路。
- 公共构建使用索引入口默认值和约束，旧底层入口的不同默认值继续由原业务模块维护。
  动态树只配置重平衡和扩展倍率，结构统一来自 retrieval.build.structure；派生阈值留给后续运行层计算。
  回退树策略归 retrieval.build.task，构建展示归 retrieval.build.output，不增加第二个可写路径。
- `PUBLIC_CONFIG_MODELS` 覆盖根 Schema 可达的子模型，`INTERNAL_CONFIG_MODELS` 登记其余模型。
  模型登记不等于 `__all__` 便利导出范围，新增子节点不自动增加独立导出。

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
