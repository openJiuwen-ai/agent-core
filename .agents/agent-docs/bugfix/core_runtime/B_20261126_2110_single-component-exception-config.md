---
id: B_20261126_2110_single-component-exception-config
title: 单组件直调错误路径被 AttributeError 掩盖原始组件异常
type: B
author: linghb
author_email: 957733934@qq.com
modules:
  - openjiuwen/core
files:
  - openjiuwen/core/workflow/_workflow.py
  - tests/unit_tests/core/workflow/test_workflow.py
designs: []
design_note: 对单组件直调路径的对齐性修复,不改变任何契约,无需独立设计。
---

# 单组件直调错误路径被 AttributeError 掩盖原始组件异常

## 现象与根因

`execute_single_component` 内联构造的 `SimpleNodeConfig` 只定义了
`io_configs` / `abilities` / `max_retries` 三个属性,而 `Vertex` 的错误恢复
路径无条件读取更多字段:

- `vertex._run_error_recovery`(`vertex.py:340`):
  `exception_config = self._node_config.exception_config if self._node_config else None`
- `vertex._build_timeout_error`(`vertex.py:304`)同样读取 `exception_config`。
- `vertex._run_executable_with_retry`(`vertex.py:239`)用
  `getattr(self._node_config, 'timeout', -1.0)` 读超时,已有默认兜底。

组件执行失败时调用链为
`execute_single_component → vertex.call → _run_executable_with_retry`
(max_retries=0,重试耗尽)→ `_run_error_recovery`(INVOKE 属可恢复 ability,
不被 337 行提前 raise)→ 340 行抛
`AttributeError: 'SimpleNodeConfig' object has no attribute 'exception_config'`。
该行位于错误恢复 try 块之外,异常直接向上传播,调用方看到的是配置对象缺属性
而不是组件的真实业务异常。超时路径同理(`_build_timeout_error` 在自身 try 内,
异常被吞后回退默认错误,行为上不致命但同样读不到配置)。

## 修复决策

给 `SimpleNodeConfig` 补齐 `Vertex` 错误路径无条件读取的字段:
`self.exception_config = None`(走默认恢复策略,与无配置的图节点行为一致)、
`self.timeout = -1.0`(不启用节点级超时,保持原直调行为)。

备选方案是改 `vertex.py` 两处为 `getattr(..., None)` 宽松读取;放弃原因是
`Vertex` 对真实 `NodeSpec` 的字段访问已定型,放宽会把"配置对象缺字段"的
编程错误静默化,不如让直调路径的对象对齐真实节点配置面。这也与
`_run_executable_with_retry` 读 `timeout` 时已有的 `getattr` 兜底语义一致
——`timeout` 侧本次一并补上,消除两套节点对象的面差。

## 回归证据

基线为 `46014823e`(develop HEAD),环境为 Windows、Python 3.11、uv 同步测试环境。

新增测试 `test_executor_single_component_error_propagates_original_exception`:
组件抛 `WORKFLOW_EXECUTION_ERROR`("组件执行失败"),断言异常原样传播且不含
"exception_config" 字样。修复前实测失败,错误为:

```text
AttributeError: 'SimpleNodeConfig' object has no attribute 'exception_config'
openjiuwen/core/graph/vertex.py:340
```

修复后该用例通过,且现有直调用例(HITL 交互、正常输出)不回归:

```powershell
.venv/Scripts/python.exe -B -m pytest tests/unit_tests/core/workflow tests/unit_tests/core/graph -q
```

结果 176 项通过(含既有单组件直调用例),0 失败。

## 遗留

无。`SimpleNodeConfig` 后续再新增 `Vertex` 读取的字段时,应同步补齐,
或推动 `Vertex` 统一用带默认值的 `getattr`。
