---
id: B_20261127_2030_trigger-parallel-session-injection
title: trigger_parallel 回调会话注入串用修复
type: B
author: zhangzj
author_email: zhangzhijun9@huawei.com
modules:
  - core/runner/callback
files:
  - openjiuwen/core/runner/callback/framework.py
  - tests/unit_tests/core/runner/callback/test_framework_triggers.py
designs: []
design_note: 对既有 AsyncCallbackFramework 契约的小范围实现缺陷修复，未形成独立设计文档。
---

# trigger_parallel 回调会话注入串用修复

## 现象与影响

`AsyncCallbackFramework.trigger_parallel` 在同一事件上注册多个回调时，若这些回调对 `session` 参数的需求不一致（例如一个 `async def needs_session(session)`、一个 `async def plain()`），则每次触发**恰好只有一个回调真正执行**，另一个被静默丢弃：不抛异常、无 error 级日志，调用方只拿到一条结果。

复现（修复前，直接调用仓库代码）：

- 注册顺序 `needs_session → plain`：`results == ['plain']`
- 注册顺序 `no_session → needs_session`：`results == ['needs']`

即调用方看到的是“少执行了一个回调”，且缺失的是哪一个取决于注册顺序。`trigger`（串行路径）与 `trigger_parallel` 的既有测试都只使用无 `session` 参数的回调，因此该缺陷此前不可见。`AsyncCallbackFramework` 由 `openjiuwen.core.runner.callback` 公开导出，任何使用并行触发且回调签名不一致的调用方都受影响。

## 关联设计与当前契约

无独立设计文档。本次依据的既有契约为：`trigger_parallel` 应执行该事件下所有已启用的回调并返回各自成功结果；`session` 是否注入只取决于该回调自身的签名（见 `_inject_session_if_needed`），与同事件其它回调无关。

## 根因与修复决策

两个缺陷叠加：

1. **闭包晚绑定**：`execute_callback` 在循环内定义，协程体读取的是外层循环变量 `callback`。`asyncio` 在任务真正调度时才求值，此时循环已结束，`callback` 恒为最后一个注册的回调。于是所有回调都按“最后一个回调”的签名决定是否注入 `session`：需要 `session` 的回调可能拿到错误参数而抛 `TypeError`，被并行分支的异常处理吞掉，只留下日志。修复为使用协程的默认参数 `cb=callback` 做逐任务绑定。
2. **共享 kwargs 污染**：无过滤器时 `filter_result.modified_kwargs` 为 `None`，`final_kwargs = ... or kwargs` 直接引用了调用方传入的同一个 dict。`_inject_session_if_needed` 会向其中写入 `session`，于是第一个任务的注入会串到同事件其它回调，甚至反向污染调用方对象。修复为 `dict(...)` 复制后再注入。

备选方案：给整个 `kwargs` 也加默认参数绑定、或用 `functools.partial` 包装协程。二者都能解决晚绑定，但前者仍共享同一个 dict，后者改动面更大；因此选择“绑定回调 + 复制 kwargs”的最小改动，不改变 `trigger` 与过滤器路径的语义。

## 回归证据与适用范围

- 新增 `tests/unit_tests/core/runner/callback/test_framework_triggers.py::test_trigger_parallel_injects_session_per_callback`，以 `needs_session_first` 参数化覆盖两种注册顺序，断言两个回调都执行且结果齐全。
- 修复前该测试在 `needs_session → plain` 顺序下失败：`assert ['needs_session'] == ['needs_session', 'plain']`。
- 修复后 `python -m pytest -o addopts="" tests/unit_tests/core/runner/callback/test_framework_triggers.py -q`：49 passed。
- 同目录整体 `python -m pytest -o addopts="" tests/unit_tests/core/runner/callback -q`：372 passed，4 errors；错误均为环境缺少 `pytest-mock`（`fixture 'mocker' not found`），与本次改动无关，改动前同样存在。
- 未验证范围：真实业务中带自定义过滤器组合的并行触发；`trigger` 串行路径未改动，不在本次范围内。

## 防错模式与资料同步

- 在循环内创建协程/任务时，必须在创建处绑定循环变量（默认参数或工厂函数），不能依赖闭包读外层可变变量。
- 并行执行共享的 `args`/`kwargs` 时，注入或改写前先复制，避免兄弟回调之间以及调用方对象之间互相污染。
- 本次未新增 spec：契约未变化，仅实现向既有契约对齐；防错要点以本记录和代码注释留存。

## 遗留与后续

无。`trigger_parallel` 之外未发现同类循环闭包绑定的会话注入点。
